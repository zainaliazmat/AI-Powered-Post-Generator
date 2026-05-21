import json
import logging
import sqlite3
from pathlib import Path

from .shared import now_iso

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "pipeline.db"

_SOURCE_COLUMNS = "key, url, method, feed_url, rsshub_slug, pw_wait_for, added_at, last_fetched"


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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


def update_last_fetched(key: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE sources SET last_fetched = ? WHERE key = ?",
            (now_iso(), key),
        )


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


def get_pending_posts() -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM generated_posts WHERE status = 'pending_review' ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


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
