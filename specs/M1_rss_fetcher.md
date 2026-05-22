# M1 — RSS Feed Fetcher

**Status:** Complete  
**Depends on:** M0  
**Blocks:** M2

---

## Purpose

Fetch articles from all active sources and write them to `data/latest_articles.json`. Sources are discovered dynamically, their working fetch method is saved to SQLite, and any source that crashes is automatically quarantined so the pipeline keeps running.

---

## Entry Point

```bash
python cli.py --scrape
```

This is the single command that runs the full M1 pipeline. It:
1. Loads all active sources from `sources` table
2. Fetches each one using its saved method
3. Writes all articles to `data/latest_articles.json` (overwritten each run)
4. Prints a per-source summary to the console
5. Automatically moves any crashed source to `crashed_sources` table

---

## Full CLI Reference

```bash
# Run the scraper (primary command)
python cli.py --scrape

# Discover and add a new source
python cli.py --add --url https://sdtimes.com

# List all active sources
python cli.py --list

# List crashed/broken sources
python cli.py --crashed

# Restore a crashed source to the active list
python cli.py --fix <key>

# Test-fetch a single source (prints articles, no JSON output)
python cli.py --source <key>
```

---

## Crash vs Empty Return

Two distinct outcomes when fetching a source:

| Outcome | Meaning | Action |
|---------|---------|--------|
| Returns `[]` | Source works — no new articles in the window | Log `0 articles`, continue. **This is fine.** |
| Raises exception | Source is broken or unreachable | Move to `crashed_sources`, exclude from future fetches until `--fix <key>` |

Crashed sources are not silently swallowed — the user sees them in the summary log and in `--crashed`.

---

## Console Summary (--scrape output)

```
Scrape summary — 2026-05-20 14:30:00 UTC
─────────────────────────────────────────────
  hn             30 articles
  sdtimes        10 articles
  google_news    20 articles
  infoq          15 articles
  openai          0 articles  (empty — source OK)
  techcrunch     CRASHED → moved to crashed list  (HTTPError: 403...)
─────────────────────────────────────────────
  Total: 75 articles → data/latest_articles.json

  1 source(s) crashed. Run --crashed to review.
```

---

## Output JSON Format

File: `data/latest_articles.json` — overwritten on every `--scrape` run.

**Only articles published within the last 48 hours are included.** Articles older than 48h are silently dropped. The `source` field is stripped from output.

Articles with no parseable date are kept but flagged:

```json
[
  {
    "title": "OpenAI releases GPT-5",
    "url": "https://...",
    "summary": "...",
    "date": "2026-05-20T10:00:00+00:00",
    "scraped_at": "2026-05-20T14:30:00+00:00",
    "author": "",
    "rank": "",
    "points": "",
    "hn_discussion": "",
    "categories": [],
    "reading_time": ""
  },
  {
    "title": "Some article with no date",
    "url": "https://...",
    "summary": "...",
    "date": "",
    "scraped_at": "2026-05-20T14:30:00+00:00",
    "author": "",
    "rank": "", "points": "", "hn_discussion": "",
    "categories": [], "reading_time": "",
    "date_unknown": true
  }
]
```

`date_unknown: true` signals that age could not be verified. Downstream steps (M2+) should treat these as lower-confidence articles. Fields like `rank`, `points`, `hn_discussion` are HN-specific; `categories` and `reading_time` are InfoQ/SD Times-specific.

---

## Discovery Pipeline (--add)

Probes in order, stops at first success. "Success" = returns ≥ 1 parseable article.

```
Step 1: Extract a clean source key from the URL
        sdtimes.com → "sdtimes", news.ycombinator.com → "hn"

Step 2: Check if key already exists in SQLite sources table → abort if so

Step 3: Probe in order:
    Tier 1 — Native RSS/Atom
        - Fetch URL, scan <link rel="alternate"> and common paths
          (/feed, /rss, /feed.xml, /atom.xml, /rss.xml)
        - Parse with feedparser → success if ≥ 1 entry returned

    Tier 2 — RSSHub
        - Try rsshub.app/<slug> and rss.shab.fun/<slug> (mirror)
        - slug derived from domain (e.g. hackernews/news, infoq/articles)
        - Parse with feedparser → success if ≥ 1 entry returned

    Tier 3 — HTML scraping (requests + BeautifulSoup)
        - Fetch URL with HEADERS, parse article/h2/h3 elements
        - Success if ≥ 1 article with title + url extracted

    Tier 4 — Playwright
        - Headless Chromium, wait for article/h2/h3/a elements
        - Parse rendered DOM with BeautifulSoup
        - Success if ≥ 1 article extracted

Step 4: Save result to SQLite sources table

Step 5: Print summary — method found, sample article titles
```

If all 4 tiers fail → print clear error, nothing saved.

---

## SQLite Schema

### `sources` table

```sql
id           INTEGER PRIMARY KEY AUTOINCREMENT
key          TEXT NOT NULL UNIQUE         -- "sdtimes", "hn", "openai"
url          TEXT NOT NULL                -- original URL passed to --add
method       TEXT NOT NULL                -- "rss" | "rsshub" | "html" | "playwright"
feed_url     TEXT                         -- populated if method is rss or rsshub
rsshub_slug  TEXT                         -- populated if method is rsshub
pw_wait_for  TEXT                         -- populated if method is playwright
added_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
last_fetched TEXT                         -- updated on every successful fetch
```

### `crashed_sources` table

```sql
id           INTEGER PRIMARY KEY AUTOINCREMENT
key          TEXT NOT NULL UNIQUE
url          TEXT NOT NULL
method       TEXT NOT NULL
feed_url     TEXT
rsshub_slug  TEXT
pw_wait_for  TEXT
added_at     TEXT NOT NULL                -- preserved from sources table
last_fetched TEXT
crashed_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
error_msg    TEXT                         -- exception type + message
```

---

## Target Architecture

```
src/
├── models.py       ← Article TypedDict (unified schema)
├── shared.py       ← HEADERS, fetch_html(), get_playwright_html(),
│                      fetch_rss(), polite_sleep(), parse_date()
├── db.py           ← get_source(), save_source(), get_all_sources(),
│                      mark_source_crashed(), restore_source()
├── discovery.py    ← discover_method(url) → SourceConfig
│                      probe_rss(), probe_rsshub(), probe_html(), probe_playwright()
└── fetcher.py      ← fetch_source(key) → list[Article]
                       fetch_all() → (results, crashes)

cli.py              ← unified entry point
data/
└── latest_articles.json  ← output; overwritten each --scrape run
```

---

## Article TypedDict (Unified Schema)

```python
class Article(TypedDict):
    title:         str
    url:           str
    summary:       str
    date:          str          # raw string; parsed to ISO before JSON write
    source:        str          # key from sources table — stripped from JSON output
    scraped_at:    str          # UTC ISO 8601

    # optional — empty string if unavailable
    author:        str
    rank:          str          # HN only
    points:        str          # HN only
    hn_discussion: str          # HN only
    categories:    list[str]    # InfoQ / SD Times
    reading_time:  str          # InfoQ only
```

---

## Tasks

| ID | Task | Status |
|----|------|--------|
| T1.1 | `src/models.py` — Article TypedDict | ✅ Done |
| T1.2 | `src/shared.py` — HTTP helpers, RSS parsers, generic extractors | ✅ Done |
| T1.3 | `src/db.py` — source CRUD + crashed source management | ✅ Done |
| T1.4 | `src/discovery.py` — 4-tier probe pipeline | ✅ Done |
| T1.5 | `src/fetcher.py` — fetch_source(), fetch_all() with crash isolation | ✅ Done |
| T1.6 | `cli.py` — --scrape, --add, --list, --crashed, --fix, --source | ✅ Done |

**Checkpoint:**
- `python cli.py --add --url https://news.ycombinator.com` → discovers method, saves to SQLite, prints sample titles
- `python cli.py --scrape` → fetches all sources, prints summary, writes `data/latest_articles.json`
- `python cli.py --crashed` → shows any sources that failed
- `python cli.py --fix hn` → restores a crashed source

---

## Decisions (Locked)

| # | Decision | Choice | Reason |
|---|----------|--------|--------|
| 1 | RSS parsing | **feedparser** | Handles malformed feeds, Atom, encoding edge cases |
| 2 | Logging | **logging module** | One `getLogger(__name__)` per module, configured at CLI entry |
| 3 | Old scrapers | **Deleted entirely** | Clean break; new system replaces all of them |
| 4 | Source config storage | **SQLite `sources` table** | Persistent, queryable, survives restarts |
| 5 | Raw article storage | **JSON file** | No need for SQL; downstream steps read `data/latest_articles.json` |
| 6 | Crash behavior | **Quarantine to `crashed_sources`** | Pipeline keeps running; user fixes broken sources manually |
| 7 | Empty return | **Acceptable** | 0 articles ≠ crash; source might just have no new posts |
| 8 | `source` field in JSON | **Stripped** | Consumers of the JSON don't need to know which source an article came from |
| 9 | Deduplication | **url only** | Title-similarity dedup has false positives; revisit later |
| 10 | Error handling | **Retry ×3 with exponential backoff** | Built into fetch_html(); all tiers exhausted → exception propagates to fetch_all() |
| 11 | 48h age filter | **Applied in `cmd_scrape()`** | No old articles in JSON; undated articles kept with `date_unknown: true` flag |

---

## Risks

- **RSSHub slug guessing** — slug derivation from domain is heuristic; may miss or misfire. Discovery logs what was tried.
- **JS-heavy pages** — Playwright is the catch-all but adds latency; acceptable since discovery runs once.
- **Paywalled sources** — titles + summaries only; M3 must handle empty `full_text`.
- **Re-discovery** — no `--re-add` flag; to change a source's method, run `--fix <key>` to restore it (if crashed) or delete the row from `data/pipeline.db` directly, then re-run `--add`.
- **latest_articles.json is overwritten** — if you need to preserve a run's output, copy the file before running `--scrape` again.
