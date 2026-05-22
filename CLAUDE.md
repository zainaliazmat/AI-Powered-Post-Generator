# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Project Builds

An automated Instagram content pipeline for tech news:

1. **Fetch** — scrape all active sources → single JSON file (`data/latest_articles.json`)
2. **Deduplicate + Filter** — merge duplicate stories, score for virality
3. **Generate** — produce 8-slide carousel JSON via Claude Haiku (summary + caption + image prompts)
4. **Review** — adversarial ReviewAgent (Sonnet) scores each carousel; ReviseAgent (Haiku) rewrites failing posts
5. **Image** — render each carousel slide as a Pillow text card or Flux.1-schnell image
6. **Approve** — human approval queue via FastAPI web UI before publishing
7. **Publish** — auto-post approved carousels to Instagram on a schedule

**Run the full pipeline in one command:** `python cli.py --run`

## Tech Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Web framework | FastAPI + Jinja2 + uvicorn | HTML-first, no JS required |
| Database | SQLite | Local file at `data/pipeline.db`; `sqlite3` stdlib only |
| RSS | feedparser + RSSHub fallback + BeautifulSoup | Three-tier fetch strategy |
| Deduplication | scikit-learn (TF-IDF + cosine) + Jaccard | Two-pass; sklearn optional fallback to Jaccard-only |
| Carousel generation | Claude Haiku (`claude-haiku-4-5`) | 8-slide JSON + `brand_domain` per article |
| **Orchestrator** | **LangGraph + LiteLLM** | **StateGraph: scrape→dedup→generate→review→revise→save_draft; checkpointed to `data/pipeline_checkpoints.db`** |
| Image generation | Flux.1-schnell via Replicate + Pillow | Brand-aware prompts; Clearbit logo badge composited top-right; Pillow text card as fallback |
| Brand assets | Clearbit Logo API + Pillow | `fetch_logo` → cache `data/brand_assets/`; `enrich_prompt` injects brand colors/style; `composite_badge` overlays circular logo |
| Instagram | instagrapi or Graph API | Discuss ban risk before M7 |
| Scheduling | APScheduler | In-process cron |
| Config | python-dotenv | `.env` only — never hardcode secrets |

## Module Map

```
src/
├── fetcher.py        ← M1: fetch all sources, return articles list
├── discovery.py      ← M1: probe URLs to find the best fetch method
├── models.py         ← M1: Article TypedDict (includes M2 dedup fields)
├── shared.py         ← M1: HTTP helpers, RSS parsers, generic extractors
├── dedup.py          ← M2: deduplicate articles by title similarity before scoring
├── filter.py         ← M2: score + discard low-virality (not yet built)
├── carousel_gen.py   ← M3: Claude Haiku carousel generator; outputs slides + brand_domain per article
├── orchestrator.py   ← M_Orch: LangGraph StateGraph pipeline (6 nodes); LiteLLM for review + revise
├── brand.py          ← M5: fetch_logo (Clearbit+cache), enrich_prompt (brand styles), composite_badge (Pillow)
├── image_gen.py      ← M5: generate_for_post/slide (Flux.1-schnell + brand badge); _pillow_text_card fallback
├── db.py             ← shared SQLite helpers (data/pipeline.db)
├── instagram.py      ← M7: publish to Instagram
├── scheduler.py      ← M6: APScheduler jobs
└── main.py           ← M6: FastAPI app + routes

cli.py              ← entry point (scrape, dedup, generate, run, images, add sources, manage crashed)
data/
├── pipeline.db                  ← SQLite: sources + crashed_sources + generated_posts tables
├── pipeline_checkpoints.db      ← LangGraph checkpoint state (resume failed runs)
├── brand_assets/                ← Clearbit logo PNGs cached by domain (e.g. nvidia.com.png)
├── images/                      ← Generated slide images ({post_id}_{slide_number}.png, 1080×1350px)
├── latest_articles.json         ← output of --scrape; overwritten each run; articles ≤12h old
├── deduped_articles.json        ← output of --dedup; merged unique articles with provenance fields
└── generated_posts.json         ← output of --generate; debug copy of carousel data
```

## SQLite Tables

DB file: `data/pipeline.db` — auto-created on first import of `src.db`.

| Table | Purpose |
|-------|---------|
| `sources` | active registered sources (key, url, method config) |
| `crashed_sources` | sources that threw an exception during fetch; removed from active list |
| `generated_posts` | carousel JSON + review score + image paths; status: `pending_review`, `approved`, `rejected`, `published`, `failed`, `image_ready` |
| `publish_queue` | scheduled publish times for approved posts |

**Raw articles are NOT stored in SQLite.** The scraper writes `data/latest_articles.json` which downstream steps (M2+) read from.

## CLI Reference

```bash
# Full pipeline (recommended) — scrape → dedup → generate → review → revise → save_draft
python cli.py --run
python cli.py --run --force   # force-refresh carousel generation

# Individual steps (manual / debug)
python cli.py --scrape        # M1: scrape all sources → data/latest_articles.json
python cli.py --dedup         # M2: deduplicate → data/deduped_articles.json
python cli.py --generate      # M3: generate carousels → data/generated_posts.json
python cli.py --generate --force-refresh

# M5: generate images for an approved post by DB id
python cli.py --images <post_id>   # Flux + brand badge → data/images/; saves paths to DB

# Source management
python cli.py --add --url https://news.ycombinator.com   # discover + add new source
python cli.py --list                                      # list active sources
python cli.py --crashed                                   # list crashed/broken sources
python cli.py --fix <key>                                 # restore crashed source to active

# Debug a single source (prints articles, no JSON output)
python cli.py --source <key>
```

## Crash vs Empty Return

- **Empty return** (`[]`) — acceptable. Source works; no new articles in the window. Counted as 0 in the summary log.
- **Exception** — source is automatically moved from `sources` → `crashed_sources` table. It will not be fetched again until `--fix <key>` is run.

## Environment Variables

```
ANTHROPIC_API_KEY=       # required for Claude models
REPLICATE_API_TOKEN=     # for Flux.1-schnell image generation (M5)
INSTAGRAM_USERNAME=
INSTAGRAM_PASSWORD=

# LiteLLM model selection — change to switch providers (no code changes needed)
CAROUSEL_MODEL=claude-haiku-4-5     # or gemini/gemini-1.5-flash, gpt-4o-mini
REVIEW_MODEL=claude-sonnet-4-6      # or gemini/gemini-1.5-pro, gpt-4o
REVISE_MODEL=claude-haiku-4-5       # or claude-sonnet-4-6 for higher revision quality

# Gemini requires GEMINI_API_KEY; OpenAI requires OPENAI_API_KEY
```

**Quality note on `REVISE_MODEL`:** The default (`claude-haiku-4-5`) is fast and cheap but may not fully execute complex suggestions from the reviewer (Sonnet). If repeated low scores appear after revision, set `REVISE_MODEL=claude-sonnet-4-6`. This doubles revision cost but improves adherence to suggestions.

## Rules for All Code in This Repo

- Never store secrets in code — `.env` only, loaded via `python-dotenv`
- Use `sqlite3` (stdlib) for all DB operations — no ORM, no external DB client
- Every function that calls an external API must have `try/except` logging to `logs/`
- 12h age filter applied at scrape time; articles older than 12h are dropped. Undated articles (no parseable date) are kept but flagged with `date_unknown: true`
- Add `time.sleep(1)` between LLM calls in any loop
- Web UI must work without JavaScript (Jinja2 templates first)
- **After completing any task:** update CLAUDE.md (tech stack, module map, CLI reference, rules) and all relevant spec files (`specs/`, `docs/superpowers/specs/`) to reflect the current architecture — stale docs are bugs

## Setup

```bash
source venv/bin/activate
pip install -r requirements.txt

# Playwright (only needed for scraping fallback)
playwright install chromium
```

## Running

```bash
# Full pipeline (one command)
python cli.py --run

# Individual steps
python cli.py --scrape
python cli.py --dedup
python cli.py --generate

# M6 — start web UI (future)
uvicorn src.main:app --reload --port 8000
```

## Development Workflow

Milestones are built in order (M0 → M7). Each milestone has a spec in `specs/` — read and discuss the spec before writing any code. Each milestone ends with a checkpoint test before the next begins.

Current milestone specs live in `specs/`:
- `specs/M0_setup.md`
- `specs/M1_rss_fetcher.md`
- `specs/M2_viral_filter.md`
- `specs/M3_summarization.md`
- `specs/M4_caption_generation.md`
- `specs/M_orchestrator.md`  ← ⚠️ superseded; see `docs/superpowers/specs/2026-05-22-langgraph-litellm-orchestrator-design.md`
- `specs/M5_image_generation.md`
- `specs/M6_web_dashboard.md`
- `specs/M7_instagram_publishing.md`

## Git Rules

- **Never run any git commands** (commit, add, push, status, diff, log, etc.)
- At the end of each task, suggest the git commands the user should run — do not run them
- The user manages all version control themselves

## Orchestrator Pipeline Rules

The orchestrator (`src/orchestrator.py`) is a **LangGraph StateGraph** — not Agent SDK. 6 single-responsibility nodes:

```
scrape → dedup → generate → review → [conditional] → revise → save_draft
                                           ↘ save_draft (all scores ≥ 7)
```

| Node | Model | Responsibility |
|------|-------|----------------|
| `scrape` | — | `cmd_scrape()` → `articles_count` in state |
| `dedup` | — | `cmd_dedup()` → `unique_count` in state |
| `generate` | Haiku | `cmd_generate()` → `posts` in state |
| `review` | Sonnet | LiteLLM: score each carousel 1-10 → `review_results` |
| `revise` | Haiku | LiteLLM: rewrite score<7 carousels → `reviewed_posts` |
| `save_draft` | — | INSERT to `generated_posts` as `pending_review` |

**Key rules:**
- Each node does exactly one thing — no node makes two different kinds of API calls
- Conditional edge: `"revise"` if any score < 7, else skip to `"save_draft"`
- `save_draft` falls back to `review_results` if `reviewed_posts` is empty (revise skipped)
- Scoring rubric in the **user prompt**, not system prompt — keeps system prompt cacheable
- `max_tokens` capped per model: review=1024, revise=2048
- On ReviewAgent JSON parse failure → treat as score=5 and call revise
- On ReviseAgent output → always re-validate with `_validate_carousel()` from carousel_gen.py
- Checkpoint stored at `data/pipeline_checkpoints.db`; today's run resumes on re-run

## Image Generation Rules (M5)

- `src/brand.py`: `fetch_logo` (Clearbit + 90-day cache), `enrich_prompt` (brand style dict + cleanup), `composite_badge` (80×80px circular badge, top-right, 20px inset)
- `src/image_gen.py`: `generate_for_slide` calls `enrich_prompt` → Flux.1-schnell → `composite_badge`; falls back to `_pillow_text_card` on any Flux failure
- Brand logo fetched from `https://logo.clearbit.com/{domain}` — free, no auth, cached to `data/brand_assets/`
- `enrich_prompt` strips cyberpunk/neon/futuristic defaults before injecting brand style; hard cap 1000 chars
- Image gen triggered post-approval via `python cli.py --images <post_id>` (M6 will trigger it from the web UI)
