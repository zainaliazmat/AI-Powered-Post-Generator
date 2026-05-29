import json
import logging
import os
import sqlite3
from pathlib import Path

from .shared import now_iso

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "pipeline.db"

_PIPELINE_STEPS = ("scrape", "dedup", "generate", "review", "revise", "save_draft", "images")

_SOURCE_COLUMNS = "key, url, method, feed_url, rsshub_slug, pw_wait_for, added_at, last_fetched"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                key          TEXT    NOT NULL UNIQUE,
                url          TEXT    NOT NULL,
                method       TEXT    NOT NULL,
                feed_url     TEXT,
                rsshub_slug  TEXT,
                pw_wait_for  TEXT,
                added_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                last_fetched TEXT
            );

            CREATE TABLE IF NOT EXISTS crashed_sources (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                key          TEXT    NOT NULL UNIQUE,
                url          TEXT    NOT NULL,
                method       TEXT    NOT NULL,
                feed_url     TEXT,
                rsshub_slug  TEXT,
                pw_wait_for  TEXT,
                added_at     TEXT    NOT NULL,
                last_fetched TEXT,
                crashed_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                error_msg    TEXT
            );

            CREATE TABLE IF NOT EXISTS generated_posts (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                article_hash  TEXT    NOT NULL UNIQUE,
                article_url   TEXT    NOT NULL,
                article_title TEXT    NOT NULL,
                carousel_json TEXT    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'pending_review',
                created_at    TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                reviewed_at   TEXT,
                published_at  TEXT
            );
        """)
    migrate_review_columns()
    migrate_image_columns()
    migrate_rejection_reason()
    migrate_pipeline_runs()
    migrate_post_events()
    migrate_pipeline_settings()
    migrate_sources_columns()


def migrate_review_columns() -> None:
    """Add review_score and reviewed columns if they don't exist. Safe to call repeatedly."""
    with get_conn() as conn:
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(generated_posts)").fetchall()
        }
        if "review_score" not in existing:
            conn.execute(
                "ALTER TABLE generated_posts ADD COLUMN review_score REAL"
            )
            logger.info("Added review_score column to generated_posts")
        if "reviewed" not in existing:
            conn.execute(
                "ALTER TABLE generated_posts ADD COLUMN reviewed INTEGER DEFAULT 0"
            )
            logger.info("Added reviewed column to generated_posts")


def migrate_image_columns() -> None:
    """Add image_paths column if it doesn't exist. Safe to call repeatedly."""
    with get_conn() as conn:
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(generated_posts)").fetchall()
        }
        if "image_paths" not in existing:
            conn.execute(
                "ALTER TABLE generated_posts ADD COLUMN image_paths TEXT"
            )
            logger.info("Added image_paths column to generated_posts")


def migrate_rejection_reason() -> None:
    """Add rejection_reason column if it doesn't exist. Safe to call repeatedly."""
    with get_conn() as conn:
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(generated_posts)").fetchall()
        }
        if "rejection_reason" not in existing:
            conn.execute(
                "ALTER TABLE generated_posts ADD COLUMN rejection_reason TEXT"
            )
            logger.info("Added rejection_reason column to generated_posts")


def migrate_pipeline_runs() -> None:
    """Create pipeline_runs and pipeline_run_steps tables + indexes."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger       TEXT    NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'running',
                pid           INTEGER,
                started_at    TEXT    NOT NULL,
                finished_at   TEXT,
                error         TEXT,
                stop_reason   TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_pipeline_runs_started
                ON pipeline_runs(started_at DESC);
            CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status
                ON pipeline_runs(status);

            CREATE TABLE IF NOT EXISTS pipeline_run_steps (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id        INTEGER NOT NULL REFERENCES pipeline_runs(id) ON DELETE CASCADE,
                node          TEXT    NOT NULL,
                seq           INTEGER NOT NULL,
                status        TEXT    NOT NULL DEFAULT 'pending',
                progress      TEXT,
                started_at    TEXT,
                finished_at   TEXT,
                error         TEXT,
                UNIQUE(run_id, node)
            );

            CREATE INDEX IF NOT EXISTS idx_pipeline_run_steps_run
                ON pipeline_run_steps(run_id);
        """)


def migrate_pipeline_settings() -> None:
    """Create pipeline_settings table (single row id=1) with seed defaults. Idempotent."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS pipeline_settings (
                id                      INTEGER PRIMARY KEY CHECK (id = 1),
                time_window_hours       INTEGER NOT NULL,
                per_source_max          INTEGER NOT NULL,
                global_max_carousels    INTEGER NOT NULL,
                min_slides              INTEGER NOT NULL,
                max_slides              INTEGER NOT NULL,
                image_renderer          TEXT    NOT NULL,
                updated_at              TEXT    NOT NULL
            );

            INSERT OR IGNORE INTO pipeline_settings
                (id, time_window_hours, per_source_max, global_max_carousels,
                 min_slides, max_slides, image_renderer, updated_at)
            VALUES
                (1, 12, 10, 8, 4, 6, 'pillow',
                 strftime('%Y-%m-%dT%H:%M:%SZ', 'now'));
        """)


def migrate_sources_columns() -> None:
    """Add is_active (default 1) and last_article_count to sources. Idempotent."""
    with get_conn() as conn:
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(sources)").fetchall()
        }
        if "is_active" not in existing:
            conn.execute(
                "ALTER TABLE sources ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1"
            )
            logger.info("Added is_active column to sources")
        if "last_article_count" not in existing:
            conn.execute(
                "ALTER TABLE sources ADD COLUMN last_article_count INTEGER"
            )
            logger.info("Added last_article_count column to sources")


def migrate_post_events() -> None:
    """Create post_events table + indexes; add generated_posts.run_id. Idempotent."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS post_events (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id      INTEGER NOT NULL REFERENCES generated_posts(id) ON DELETE CASCADE,
                run_id       INTEGER REFERENCES pipeline_runs(id) ON DELETE SET NULL,
                stage        TEXT    NOT NULL,
                status       TEXT    NOT NULL,
                prompt_vars  TEXT,
                output       TEXT,
                duration_ms  INTEGER,
                created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            );

            CREATE INDEX IF NOT EXISTS idx_post_events_post
                ON post_events(post_id);
            CREATE INDEX IF NOT EXISTS idx_post_events_post_stage
                ON post_events(post_id, stage);
            CREATE INDEX IF NOT EXISTS idx_post_events_run
                ON post_events(run_id);
        """)
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(generated_posts)").fetchall()
        }
        if "run_id" not in existing:
            conn.execute(
                "ALTER TABLE generated_posts ADD COLUMN run_id INTEGER REFERENCES pipeline_runs(id) ON DELETE SET NULL"
            )
            logger.info("Added run_id column to generated_posts")


# ---------------------------------------------------------------------------
# sources table
# ---------------------------------------------------------------------------

def get_source(key: str) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sources WHERE key = ?", (key,)).fetchone()
        return dict(row) if row else None


def save_source(key: str, url: str, config: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO sources (key, url, method, feed_url, rsshub_slug, pw_wait_for)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                url          = excluded.url,
                method       = excluded.method,
                feed_url     = excluded.feed_url,
                rsshub_slug  = excluded.rsshub_slug,
                pw_wait_for  = excluded.pw_wait_for
            """,
            (
                key,
                url,
                config["method"],
                config.get("feed_url"),
                config.get("rsshub_slug"),
                config.get("pw_wait_for"),
            ),
        )
    logger.info("Saved source '%s' (method: %s)", key, config["method"])


def get_all_sources() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM sources ORDER BY added_at").fetchall()
        return [dict(r) for r in rows]


def get_active_sources() -> list[sqlite3.Row]:
    """Return only sources with is_active = 1, ordered by key."""
    with get_conn() as conn:
        return conn.execute(
            f"SELECT {_SOURCE_COLUMNS}, is_active, last_article_count FROM sources "
            "WHERE is_active = 1 ORDER BY key"
        ).fetchall()


def update_last_fetched(key: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sources SET last_fetched = ? WHERE key = ?",
            (now_iso(), key),
        )


def set_source_active(key: str, is_active: bool) -> bool:
    """Flip is_active. Returns True if a row was updated."""
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE sources SET is_active = ? WHERE key = ?",
            (1 if is_active else 0, key),
        )
    return cur.rowcount > 0


_EDITABLE_SOURCE_FIELDS = ("method", "feed_url", "rsshub_slug", "pw_wait_for", "url")


def update_source_config(key: str, config: dict) -> bool:
    """Update method/feed_url/rsshub_slug/pw_wait_for/url for an existing source.
    Silently drops unknown fields (e.g. 'key' cannot be renamed). Returns True on update."""
    fields = {k: v for k, v in config.items() if k in _EDITABLE_SOURCE_FIELDS}
    if not fields:
        return False
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        cur = conn.execute(
            f"UPDATE sources SET {set_clause} WHERE key = ?",
            list(fields.values()) + [key],
        )
    return cur.rowcount > 0


def delete_source(key: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM sources WHERE key = ?", (key,))
    return cur.rowcount > 0


def delete_crashed_source(key: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM crashed_sources WHERE key = ?", (key,))
    return cur.rowcount > 0


def set_source_article_count(key: str, count: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sources SET last_article_count = ? WHERE key = ?",
            (count, key),
        )


def list_sources_with_health() -> list[dict]:
    """Return active + paused + crashed sources merged. Each row has a `status` and `error_msg`."""
    cols = "id, key, url, method, feed_url, rsshub_slug, pw_wait_for, added_at, last_fetched, is_active, last_article_count"
    out: list[dict] = []
    with get_conn() as conn:
        for r in conn.execute(f"SELECT {cols} FROM sources ORDER BY key"):
            d = dict(r)
            d["status"] = "active" if d["is_active"] else "paused"
            d["error_msg"] = None
            out.append(d)
        for r in conn.execute(
            "SELECT id, key, url, method, feed_url, rsshub_slug, pw_wait_for, added_at, last_fetched, error_msg FROM crashed_sources ORDER BY key"
        ):
            d = dict(r)
            d["is_active"] = 0
            d["last_article_count"] = None
            d["status"] = "crashed"
            out.append(d)
    return out


# ---------------------------------------------------------------------------
# crashed_sources table
# ---------------------------------------------------------------------------

def mark_source_crashed(key: str, error_msg: str) -> None:
    """Move a source from sources → crashed_sources and log the error."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM sources WHERE key = ?", (key,)).fetchone()
        if not row:
            logger.warning("mark_source_crashed: '%s' not found in active sources", key)
            return
        conn.execute(
            """
            INSERT INTO crashed_sources
                (key, url, method, feed_url, rsshub_slug, pw_wait_for,
                 added_at, last_fetched, error_msg)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                crashed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                error_msg  = excluded.error_msg
            """,
            (
                row["key"], row["url"], row["method"],
                row["feed_url"], row["rsshub_slug"], row["pw_wait_for"],
                row["added_at"], row["last_fetched"], error_msg,
            ),
        )
        conn.execute("DELETE FROM sources WHERE key = ?", (key,))
    logger.warning("Source '%s' moved to crashed_sources: %s", key, error_msg)


def get_crashed_sources() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM crashed_sources ORDER BY crashed_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def restore_source(key: str) -> bool:
    """Move a source from crashed_sources → sources. Returns True on success."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM crashed_sources WHERE key = ?", (key,)
        ).fetchone()
        if not row:
            return False
        conn.execute(
            """
            INSERT INTO sources (key, url, method, feed_url, rsshub_slug, pw_wait_for, added_at, last_fetched)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(key) DO NOTHING
            """,
            (
                row["key"], row["url"], row["method"],
                row["feed_url"], row["rsshub_slug"], row["pw_wait_for"],
                row["added_at"], row["last_fetched"],
            ),
        )
        conn.execute("DELETE FROM crashed_sources WHERE key = ?", (key,))
    logger.info("Source '%s' restored to active sources", key)
    return True


# ---------------------------------------------------------------------------
# generated_posts table
# ---------------------------------------------------------------------------

def save_generated_post(
    article_hash: str,
    article_url: str,
    article_title: str,
    carousel_json: dict,
) -> bool:
    """Insert a generated post. Returns False if article_hash already exists."""
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO generated_posts
                    (article_hash, article_url, article_title, carousel_json)
                VALUES (?, ?, ?, ?)
                """,
                (article_hash, article_url, article_title, json.dumps(carousel_json)),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def save_reviewed_post(
    article_hash: str,
    article_url: str,
    article_title: str,
    carousel_json: dict,
    review_score: float,
) -> bool:
    """Insert or update a reviewed post. Always returns True."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO generated_posts
                (article_hash, article_url, article_title, carousel_json,
                 review_score, reviewed, status)
            VALUES (?, ?, ?, ?, ?, 1, 'pending_review')
            ON CONFLICT(article_hash) DO UPDATE SET
                carousel_json = excluded.carousel_json,
                review_score  = excluded.review_score,
                reviewed      = 1
            """,
            (
                article_hash,
                article_url,
                article_title,
                json.dumps(carousel_json),
                review_score,
            ),
        )
    return True


def get_pending_posts() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM generated_posts WHERE status = 'pending_review' ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def save_image_paths(post_id: int, image_paths: list[str]) -> bool:
    """Set image_paths and mark post as image_ready. Returns True always."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE generated_posts SET image_paths = ?, status = 'image_ready' WHERE id = ?",
            (json.dumps(image_paths), post_id),
        )
    return True


def get_post_counts() -> dict[str, int]:
    """Row counts by status plus a 'total' key."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) FROM generated_posts GROUP BY status"
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM generated_posts"
        ).fetchone()[0]
    counts: dict[str, int] = {
        "pending_review": 0,
        "image_ready": 0,
        "approved": 0,
        "rejected": 0,
        "published": 0,
        "failed": 0,
    }
    for status, n in rows:
        if status in counts:
            counts[status] = n
    counts["total"] = total
    return counts


def get_post(post_id: int) -> dict | None:
    """Fetch a single post by id. Returns None if not found."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM generated_posts WHERE id = ?", (post_id,)
        ).fetchone()
        return dict(row) if row else None


def approve_post(post_id: int) -> bool:
    """Set status='approved'. Returns False if post is already reviewed."""
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE generated_posts
               SET status = 'approved', reviewed_at = ?
               WHERE id = ? AND status IN ('pending_review', 'image_ready')""",
            (now_iso(), post_id),
        )
        return cur.rowcount > 0


def reject_post(post_id: int, reason: str) -> bool:
    """Set status='rejected' and save reason. Returns False if already reviewed."""
    with get_conn() as conn:
        cur = conn.execute(
            """UPDATE generated_posts
               SET status = 'rejected', rejection_reason = ?, reviewed_at = ?
               WHERE id = ? AND status IN ('pending_review', 'image_ready')""",
            (reason, now_iso(), post_id),
        )
        return cur.rowcount > 0


def get_review_queue(limit: int = 50, offset: int = 0) -> list[dict]:
    """Posts pending review, newest first, paginated."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM generated_posts
               WHERE status IN ('pending_review', 'image_ready')
               ORDER BY created_at DESC
               LIMIT ? OFFSET ?""",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# post_events helpers
# ---------------------------------------------------------------------------

def insert_post_event(
    post_id: int,
    run_id: int | None,
    stage: str,
    status: str,
    prompt_vars: dict | None = None,
    output: dict | None = None,
    duration_ms: int | None = None,
) -> int:
    """Insert one post_events row. Returns the new row id."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO post_events
                (post_id, run_id, stage, status, prompt_vars, output, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post_id,
                run_id,
                stage,
                status,
                json.dumps(prompt_vars) if prompt_vars is not None else None,
                json.dumps(output) if output is not None else None,
                duration_ms,
            ),
        )
        return cur.lastrowid


def get_post_events(post_id: int) -> list[dict]:
    """All events for a post, oldest first (by created_at then id)."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM post_events WHERE post_id = ? "
            "ORDER BY created_at ASC, id ASC",
            (post_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def set_post_run_id(post_id: int, run_id: int) -> None:
    """Set generated_posts.run_id for a post."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE generated_posts SET run_id = ? WHERE id = ?",
            (run_id, post_id),
        )


# ---------------------------------------------------------------------------
# pipeline_runs / pipeline_run_steps
# ---------------------------------------------------------------------------

def create_pipeline_run(trigger: str) -> int | None:
    """Atomically claim the single-run slot. Returns the new run id, or None
    if another run is already active. On success, seeds 7 pending step rows."""
    started = now_iso()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO pipeline_runs (trigger, status, started_at)
            SELECT ?, 'running', ?
            WHERE NOT EXISTS (
                SELECT 1 FROM pipeline_runs WHERE status='running'
            )
            """,
            (trigger, started),
        )
        if cur.rowcount == 0:
            return None
        run_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO pipeline_run_steps (run_id, node, seq, status) "
            "VALUES (?, ?, ?, 'pending')",
            [(run_id, node, seq) for seq, node in enumerate(_PIPELINE_STEPS, start=1)],
        )
    return run_id


def update_run(run_id: int, **fields) -> None:
    """Generic field update on pipeline_runs."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE pipeline_runs SET {set_clause} WHERE id = ?",
            (*fields.values(), run_id),
        )


def update_run_step(run_id: int, node: str, **fields) -> None:
    """Generic field update on pipeline_run_steps, scoped to (run_id, node)."""
    if not fields:
        return
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE pipeline_run_steps SET {set_clause} "
            "WHERE run_id = ? AND node = ?",
            (*fields.values(), run_id, node),
        )


def cancel_running_step(run_id: int) -> None:
    """Mark the currently 'running' step (if any) as 'cancelled'."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE pipeline_run_steps "
            "SET status = 'cancelled', finished_at = ? "
            "WHERE run_id = ? AND status = 'running'",
            (now_iso(), run_id),
        )


def finish_pipeline_run(
    run_id: int,
    status: str,
    error: str | None = None,
    stop_reason: str | None = None,
) -> None:
    """Set terminal fields on a run row; clear pid."""
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE pipeline_runs
            SET status = ?, finished_at = ?, error = ?, stop_reason = ?, pid = NULL
            WHERE id = ?
            """,
            (status, now_iso(), error, stop_reason, run_id),
        )


def get_active_run() -> dict | None:
    """Most recent run with status='running'. None if no active run."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_runs WHERE status = 'running' "
            "ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_latest_run() -> dict | None:
    """Most recent run by started_at."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None


def get_run(run_id: int) -> dict | None:
    """Single run by id."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row else None


def get_run_steps(run_id: int) -> list[dict]:
    """Step rows for one run, ordered by seq."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_run_steps WHERE run_id = ? ORDER BY seq",
            (run_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_recent_runs(limit: int = 5) -> list[dict]:
    """Last N runs, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def _pid_alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def reconcile_stale_runs() -> int:
    """For each 'running' row, check if its PID is alive. If dead (or NULL),
    mark the run as 'failed' and any 'running' step as 'failed'. Returns count."""
    err = "subprocess died without cleanup"
    reconciled = 0
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, pid FROM pipeline_runs WHERE status = 'running'"
        ).fetchall()
        now = now_iso()
        for row in rows:
            if _pid_alive(row["pid"]):
                continue
            conn.execute(
                """
                UPDATE pipeline_runs
                SET status = 'failed', finished_at = ?, error = ?, pid = NULL
                WHERE id = ?
                """,
                (now, err, row["id"]),
            )
            conn.execute(
                "UPDATE pipeline_run_steps "
                "SET status = 'failed', error = ?, finished_at = ? "
                "WHERE run_id = ? AND status = 'running'",
                (err, now, row["id"]),
            )
            reconciled += 1
    return reconciled


# ---------------------------------------------------------------------------
# utils
# ---------------------------------------------------------------------------

def test_connection() -> bool:
    try:
        init_db()
        logger.info("SQLite connection OK — %s", DB_PATH)
        return True
    except Exception as e:
        logger.error("SQLite connection failed: %s", e)
        return False


# Auto-initialize on first import
init_db()
