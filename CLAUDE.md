# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## What This Project Builds

An automated Instagram content pipeline for tech news:

1. **Fetch** — scrape all active sources → `data/latest_articles.json`
2. **Deduplicate + Filter** — merge duplicate stories, score for virality
3. **Generate** — produce 8-slide carousel JSON via Claude Haiku
4. **Review** — ReviewAgent (Sonnet) scores; ReviseAgent (Haiku) rewrites failing posts
5. **Image** — render each slide as Pillow text card or Flux.1-schnell image
6. **Approve** — human approval queue via FastAPI web UI
7. **Publish** — auto-post approved carousels to Instagram on a schedule

**Run the full pipeline:** `python cli.py --run`

## Tech Stack

| Layer | Choice |
|-------|--------|
| Web framework | FastAPI + Jinja2 + uvicorn (HTML-first, HTMX polling) |
| Database | SQLite (`data/pipeline.db`; `sqlite3` stdlib only; WAL + `busy_timeout=5000`) |
| RSS | feedparser + RSSHub fallback + BeautifulSoup |
| Deduplication | scikit-learn TF-IDF + Jaccard (sklearn optional) |
| Carousel gen | Claude Haiku `claude-haiku-4-5` via LiteLLM |
| Orchestrator | LangGraph StateGraph (7 nodes incl. `images`); checkpoints to `data/pipeline_checkpoints.db`; live status in `pipeline_runs` + `pipeline_run_steps` |
| Image gen | `src/ImageGen/` — `IMAGE_RENDERER=pillow` (default) or `flux`; folded into orchestrator as 7th node |
| Brand assets | Logo.dev API → cached PNGs in `data/brand_assets/` |
| Instagram | instagrapi or Graph API (discuss ban risk before M7) |
| Scheduling | APScheduler (in-process cron) |
| Config | python-dotenv (`.env` only) |

## Module Map

```
src/
├── fetcher.py        — fetch all sources
├── discovery.py      — probe URLs for best fetch method
├── models.py         — Article TypedDict
├── shared.py         — HTTP helpers, RSS parsers, fetch_og_image
├── dedup.py          — deduplicate by title similarity
├── filter.py         — score + discard low-virality (not yet built)
├── carousel_gen.py   — Claude Haiku carousel generator (8 slides + brand_domain)
├── orchestrator.py   — LangGraph StateGraph (7 nodes: scrape → dedup → generate → review → revise → save_draft → images); writes live status to pipeline_runs / pipeline_run_steps
├── brand.py          — fetch_logo (Logo.dev+cache), composite_badge
├── ImageGen/
│   ├── __init__.py         — re-exports generate_for_post, generate_for_slide
│   ├── image_gen.py        — router: IMAGE_RENDERER env var
│   ├── image_gen_flux.py   — Flux.1-schnell + enrich_prompt + pillow fallback
│   └── image_gen_pillow.py — Pillow editorial renderer (1080×1350, DM Sans)
├── db.py             — SQLite helpers (incl. `post_events`, `set_post_run_id`, `get_active_sources`, `set_source_active`, `set_source_article_count`)
├── post_detail.py    — pure helpers for /posts/{id}: `diff_carousels`, `safe_image_url`
├── prompts.py        — shared prompt registry: CAROUSEL_SYSTEM, REVIEW_*, REVISE_*
├── settings.py       — runtime-editable pipeline knobs (single-row `pipeline_settings`); `get_settings`, `save_settings`, `restore_defaults`, `estimate_run_cost`
├── instagram.py      — M7: publish to Instagram
├── scheduler.py      — APScheduler jobs
└── main.py           — FastAPI app + routes

cli.py   — entry point
data/
├── pipeline.db                ← sources, crashed_sources, generated_posts
├── pipeline_checkpoints.db    ← LangGraph resume state
├── brand_assets/              ← domain logo PNGs (90-day cache)
├── fonts/                     ← DM Sans ExtraBold (auto-downloaded)
├── images/                    ← {post_id}_{slide}.png
├── latest_articles.json       ← --scrape output (≤12h articles)
├── deduped_articles.json      ← --dedup output
└── generated_posts.json       ← --generate debug copy
```

## SQLite Tables

`data/pipeline.db` — auto-created on first import of `src.db`.

| Table | Purpose |
|-------|---------|
| `sources` | active sources (key, url, method config, `is_active`, `last_article_count`) |
| `crashed_sources` | sources that threw during fetch; restored with `--fix <key>` |
| `pipeline_settings` | single row (id=1): `time_window_hours`, `per_source_max`, `global_max_carousels`, `min_slides`, `max_slides`, `image_renderer` |
| `generated_posts` | carousel JSON + score + image paths + `run_id` link; status: `pending_review`, `approved`, `rejected`, `published`, `failed`, `image_ready` |
| `pipeline_runs` | one row per pipeline run; status: `running`, `ok`, `failed`, `stopped`, `cancelled`; holds `pid`, `started_at`, `finished_at`, `error`, `stop_reason` |
| `pipeline_run_steps` | seven rows per run (one per node, fixed seq 1..7); status: `pending`, `running`, `ok`, `failed`, `skipped`, `cancelled`; free-text `progress` column |
| `post_events` | per-post lifecycle log; one row per `(post_id, stage)` where stage ∈ `review` / `revise` / `images`; holds JSON `prompt_vars` + `output` + `duration_ms`; read by `/posts/{id}` |
| `publish_queue` | scheduled publish times |

Raw articles are NOT in SQLite — `data/latest_articles.json` is the handoff file.

## CLI Reference

```bash
python cli.py --run                          # full 7-node pipeline (incl. images)
python cli.py --run --force                  # force-refresh generation

python cli.py --scrape                       # M1 → data/latest_articles.json
python cli.py --dedup                        # M2 → data/deduped_articles.json
python cli.py --generate [--force-refresh]   # M3 → data/generated_posts.json
python cli.py --images <post_id>             # ad-hoc re-render for one post (DB-saved)

python cli.py --add --url <url>              # discover + add source
python cli.py --list                         # active sources
python cli.py --crashed                      # broken sources
python cli.py --fix <key>                    # restore crashed source
python cli.py --source <key>                 # debug single source
```

## Crash vs Empty Return

- **`[]`** — acceptable; source works but no new articles in window.
- **Exception** — source moved to `crashed_sources`; not fetched again until `--fix <key>`.

## Environment Variables

```
ANTHROPIC_API_KEY=
REPLICATE_API_TOKEN=       # Flux renderer
LOGO_DEV_TOKEN=            # Logo.dev (free key at logo.dev)
INSTAGRAM_USERNAME=
INSTAGRAM_PASSWORD=

IMAGE_RENDERER=pillow      # FALLBACK only — dashboard /settings is the source of truth

CAROUSEL_MODEL=claude-haiku-4-5
REVIEW_MODEL=claude-sonnet-4-6
REVISE_MODEL=claude-haiku-4-5   # use claude-sonnet-4-6 if revision quality is poor

# Gemini: add GEMINI_API_KEY; OpenAI: add OPENAI_API_KEY
```

## Code Rules

- Secrets in `.env` only — never hardcode
- `sqlite3` stdlib only — no ORM
- Every external API call: `try/except` logging to `logs/`
- 12h age filter at scrape time; undated articles kept with `date_unknown: true`
- `time.sleep(1)` between LLM calls in any loop
- Web UI must work without JavaScript
- **After every task:** update CLAUDE.md + relevant spec files — stale docs are bugs

## Setup & Running

```bash
./venv/bin/python -m pip install -r requirements.txt
playwright install chromium        # scraping fallback only

python cli.py --run                                      # full pipeline
uvicorn src.main:app --reload --port 8000                # M6 web UI
```

## Development Workflow

Milestones M0→M7, each with a spec in `specs/`. Read and discuss the spec before writing code.

> `specs/M_orchestrator.md` is superseded — use `docs/superpowers/specs/2026-05-22-langgraph-litellm-orchestrator-design.md`.

## Orchestrator Pipeline

`src/orchestrator.py` is a **LangGraph StateGraph** (not Agent SDK):

```
scrape → dedup → generate → review → revise → save_draft → images → END
                                    ↘ save_draft (all scores ≥ 7)
```

Key invariants:
- Each node does exactly one kind of API call
- Every node body runs inside `_step(run_id, name)` — writes `running` on enter, `ok`/`failed` on exit
- Conditional edge `_route_after_review` is pure (no DB writes); `revise=skipped` is written at the entry of `_save_draft_node` when arriving without `revised_posts`
- `save_draft` falls back to `review_results` if `reviewed_posts` is empty; returns `saved_post_ids` for the `images` node
- `images` node calls `generate_for_post` per id; per-slide failures are absorbed by ImageGen's Pillow fallback
- Pending steps on a finished run are rendered as `skipped` (no DB write needed for that path)
- Scoring rubric in user prompt (not system prompt) — keeps system prompt cacheable
- `max_tokens`: review=1024, revise=2048
- ReviewAgent JSON parse failure → treat as score=5, call revise
- ReviseAgent output → always re-validate with `_validate_carousel()`

## Pipeline Observability

`pipeline_runs` + `pipeline_run_steps` are written live by the orchestrator and read by the dashboard. The single-run slot is claimed atomically via `INSERT … WHERE NOT EXISTS` in `db.create_pipeline_run`; `create_pipeline_run` returns `None` if a `running` row already exists.

Dashboard routes (all gated by `require_auth`):
- `POST /pipeline/start` — claims a run row, spawns `python cli.py --run --run-id <id>` via `subprocess.Popen(start_new_session=True)`; returns 409 if a run is already active
- `POST /pipeline/stop` — `SIGTERM` to the active run's PID (404 if no active run)
- `GET /pipeline/status` — returns the `_pipeline_card.html` fragment; HTMX `hx-trigger="every 2s"` while running
- `GET /pipeline/runs/{id}` — full run-detail page
- `GET /posts/{id}` — per-post detail page (sticky carousel preview + lifecycle log built from `post_events`); available for any status, not just `pending_review`
- `GET /posts/{id}/lightbox?slide=N` — carousel-style lightbox; partial when `HX-Request: true`, standalone page otherwise; slide clamped to `[1, total]`
- `POST /review/{id}/approve|reject` — accept optional `redirect_to` form field (whitelist: `/review`, `/posts/{id}`); whitelisted values produce an `HX-Redirect` header so the detail page round-trips back to `/review` while the queue card retains its in-place swap behavior
- `GET /sources` + `GET /sources/list` — manage sources (add via streamed discovery, edit, re-probe, pause, test-fetch, delete); list fragment supports `q`, `method`, `status` filters
- `GET /sources/discover?url=…` and `GET /sources/{key}/reprobe` — SSE streams of discovery events (`tier_start` / `probe` / `tier_end` / `done`); the final `done` echoes back `url` and a derived `key` (via `discovery.url_to_key`) so the Save-source form can auto-fill
- `POST /sources` (JSON), `PUT /sources/{key}` (JSON), `DELETE /sources/{key}`, `POST /sources/{key}/toggle-active`, `POST /sources/{key}/restore`, `DELETE /sources/crashed/{key}`, `POST /sources/bulk-delete`, `POST /sources/{key}/test-fetch` — CRUD + read-only test-fetch (uses `fetch_source(key, dry_run=True)`)
- `GET /settings`, `PUT /settings`, `POST /settings/restore-defaults`, `GET /settings/cost-estimate` — view + edit runtime pipeline knobs with live cost estimate

`cli.py --run --run-id N` is a hidden flag (`argparse.SUPPRESS`) used by the dashboard subprocess; `python cli.py --run` (no id) keeps working and creates its own row with `trigger='cli'`.

Stale-run reconciliation: `db.reconcile_stale_runs` runs on FastAPI startup (`lifespan`) and every 60s in a background `asyncio` task; checks `os.kill(pid, 0)` and marks dead `running` rows as `failed`.

## Image Generation

`src/ImageGen/` — router (`image_gen.py`) + two renderers; `pipeline_settings.image_renderer` is the source of truth, with `IMAGE_RENDERER` env var as fallback; raises `ValueError` on unknown values.

- **Pillow** (`image_gen_pillow.py`): 1080×1350px; white zone + lime separator + dark og:image zone + footer; DM Sans ExtraBold (auto-downloaded); per-slide fallback to `_fallback_card`
- **Flux** (`image_gen_flux.py`): Flux.1-schnell via Replicate; `enrich_prompt` strips cyberpunk defaults; 11s rate-limit sleep between slides; per-slide fallback to `_pillow_text_card`
- **`brand.py`**: `fetch_logo` (Logo.dev, 90-day cache) + `composite_badge` (80px circle, top-right)
- **`fetch_og_image` (shared.py)**: SSRF-guarded (blocks localhost/169.254.169.254/\*.local); 5s timeout

---

# Project Rules

# Git Workflow (STRICT)

## Branching
Every new task, feature, or fix gets a new branch from the current branch before any changes.

Name format: `type/short-description`
Types: `feature` | `fix` | `refactor` | `chore`

```bash
git checkout -b feature/auth-session-refresh
```

## Commits
After completing the task, review all modified files, then create clean focused commits.

Message format: `type(scope): short summary`

The message must explain what changed, why, and the impact — enough for a future maintainer to understand the decision without reading the diff.

```bash
feat(auth): implement JWT refresh token rotation
fix(api): prevent duplicate order creation on retry
```

No unrelated files. No debug code. No WIP commits.

## Push
```bash
git push origin feature/auth-session-refresh
```

## Before Marking Complete
- [ ] Build passes
- [ ] Lint and tests pass
- [ ] No debug/temp code
- [ ] No unrelated files in commits
- [ ] Commits are clean and reviewable

## Required End-of-Task Output
Always close with this block:

```
Branch:  feature/auth-session-refresh
Commits: abc1234 feat(auth): implement JWT refresh token rotation
Push:    origin/feature/auth-session-refresh ✓
```

If any git step fails, state exactly which step and why.


## Paths (STRICT)

**Always use relative paths** in every Bash command, Read, Write, and Edit call. Never use absolute paths like `/home/zain-ali/Documents/Scraper/...`. Reference files as `src/main.py`, `.claude/last-task-summary.md`, `data/pipeline.db`, etc.

## Security

- Stay within project directory — no `..`, `~`, or `$HOME`
- No global package installs
- Never touch `/etc`, `~/.ssh`, `~/.aws`

## Escalation

STOP and ask if: plan must fundamentally change, permission error, test fix requires out-of-scope changes, undiscussed dependencies or config files needed.

Proceed without asking for: typos, import adjustments, minor changes within the spirit of the plan.

## Testing

- Run `./venv/bin/python -m pytest` if tests exist
- No pipes (`|`) or redirects in test commands — run directly
- Always use `./venv/bin/python`, never `source venv/bin/activate`
- On test failure: fix if in scope, else STOP

## Spec Standards

Specs (`specs/`) are reviewed by the developer with external AI before implementation — vague specs are rejected.

Required sections: Overview, Goals, Non-Goals, Background, Detailed Design (architecture, interface, data structures, error handling, dependencies), Implementation Plan, Testing Plan, Open Questions, Decision Log, References.

Writing rules:
- No vague language — "handle errors gracefully" → specify exactly what to log/return
- Every interface must be typed (`def foo(data: list[Article]) -> str`)
- Non-goals are mandatory; open questions must be resolved before implementation
- Diagrams for flows with 3+ steps (ASCII is fine)
- Plan tasks must have a clear done condition and be ordered by dependency
