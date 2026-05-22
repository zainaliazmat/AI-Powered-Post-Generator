# M0 — Project Setup

**Status:** Complete  
**Depends on:** Nothing  
**Blocks:** All other milestones

---

## Purpose

Create the project skeleton: folder structure, dependencies, SQLite schema, and environment config. Every other milestone assumes this is done and verified.

---

## Tasks

| ID | Task | Output |
|----|------|--------|
| T0.1 | Create folder structure | `src/`, `logs/`, `data/`, `templates/`, `static/`, `specs/` |
| T0.2 | Write `requirements.txt` | All dependencies pinned (`sqlite3` is stdlib — no DB package needed) |
| T0.3 | Write `src/db.py` | SQLite helpers: `init_db()`, source CRUD, crashed source management — DB auto-created at `data/pipeline.db` on first import |
| T0.4 | Write `.env.example` | Template for all env vars, no real values |

**Checkpoint:** `python -c "from src.db import test_connection; test_connection()"` prints "SQLite connection OK". `data/pipeline.db` exists with `sources` and `crashed_sources` tables.

---

## SQLite Schema

DB file: `data/pipeline.db` — created automatically by `init_db()` on first import of `src.db`. Raw articles are NOT stored in SQLite — the scraper writes `data/latest_articles.json` instead.

```sql
CREATE TABLE IF NOT EXISTS sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    key          TEXT    NOT NULL UNIQUE,
    url          TEXT    NOT NULL,
    method       TEXT    NOT NULL,        -- "rss" | "rsshub" | "html" | "playwright"
    feed_url     TEXT,
    rsshub_slug  TEXT,
    pw_wait_for  TEXT,
    added_at     TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_fetched TEXT
);

-- Sources that threw an exception during fetch are moved here automatically.
-- They are removed from the active sources list until the user runs --fix <key>.
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

-- Added in M3+
CREATE TABLE IF NOT EXISTS generated_posts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    article_id   TEXT    NOT NULL,        -- url hash from latest_articles.json
    summary_text TEXT,
    caption      TEXT,
    tone         TEXT,                    -- 'witty' | 'professional' | 'hype'
    image_url    TEXT,
    image_type   TEXT,                    -- 'pillow' | 'flux'
    status       TEXT    NOT NULL DEFAULT 'pending_review',
    created_at   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    reviewed_at  TEXT,
    published_at TEXT
);

-- Added in M6
CREATE TABLE IF NOT EXISTS publish_queue (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id      INTEGER NOT NULL REFERENCES generated_posts(id),
    scheduled_at TEXT    NOT NULL,
    status       TEXT    NOT NULL DEFAULT 'pending'   -- pending | sent | failed
);
```

---

## Decisions (Locked)

| # | Decision | Choice | Reason |
|---|----------|--------|--------|
| 1 | Database | **SQLite (local)** | Project runs fully local — no cloud DB, no credentials, no internet dependency |
| 2 | Raw article storage | **JSON file (`data/latest_articles.json`)** | Simpler than SQL for intermediate pipeline data; overwritten each scrape run |
| 3 | Python version | **3.11+** | Existing scrapers use 3.10+ syntax; 3.11 perf gains are free |
| 4 | Virtual environment | **venv** | Already in use; keep consistent |
| 5 | `content_hash` strategy | **sha256(url)** — URL-only dedup | Title-based dedup has false positives; URL is sufficient for MVP |

---

## Risks

- SQLite is single-writer; concurrent writes from scheduler + web UI must use WAL mode (already set in `db.py` via `PRAGMA journal_mode=WAL`)
- Schema changes after M1 starts require manual `ALTER TABLE` or a DB reset; get this right before building M1
- `.env` must never be committed — `.gitignore` added in T0.1
