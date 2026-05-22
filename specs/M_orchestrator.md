# M_Orchestrator — Multi-Agent Pipeline Orchestrator

**Status:** ⚠️ SUPERSEDED — See `docs/superpowers/specs/2026-05-22-langgraph-litellm-orchestrator-design.md`  
**Implemented as:** LangGraph StateGraph (not Agent SDK). 6 nodes: scrape→dedup→generate→review→revise→save_draft  
**Depends on:** M1, M2, M3  
**Blocks:** M6, M7  
**Date:** 2026-05-21

---

## Purpose

Replace the manual step-by-step CLI workflow with a single `--run` command powered by the Claude Agent SDK. A head agent (Sonnet) manages the full pipeline. Two subagents (ReviewAgent, ReviseAgent) provide adversarial quality separation for carousel generation. All existing modules are reused unchanged — subagents call them via Bash.

---

## Architecture

```
python cli.py --run
      │
      ▼
OrchestratorAgent  (Head — claude-haiku-4-5)
      │
      ├─ Bash: python cli.py --scrape
      │    └─ 0 articles → EXIT early
      │
      ├─ Bash: python cli.py --dedup
      │    └─ 0 unique → EXIT early
      │
      ├─ Bash: python cli.py --generate [--force-refresh]
      │    └─ 0 posts → EXIT early
      │
      ├─ For each generated post:
      │    └─ Spawn ReviewAgent(post) → {score, issues, suggestions}
      │         ├─ score >= 7 → mark approved
      │         └─ score <  7 → Spawn ReviseAgent(post, review) → revised carousel
      │
      └─ INSERT approved posts into generated_posts (status=pending_review)
           Report: X scraped → Y unique → Z generated → N approved → N saved
```

**Principle:** Head handles all deterministic steps itself (Bash calls). Subagents are only spawned for tasks requiring genuine LLM reasoning.

---

## Agent Definitions

### OrchestratorAgent (Head)

| Field | Value |
|-------|-------|
| Model | `claude-haiku-4-5` |
| effort | `low` |
| max_tokens | `300` |
| max_turns | `25` |
| max_budget_usd | `0.10` |
| allowed_tools | `Bash`, `Read` |
| permission_mode | `dontAsk` |

**System prompt:**
```
You are a pipeline orchestrator. Execute each step using Bash in this exact order:
1. python cli.py --scrape
2. python cli.py --dedup
3. python cli.py --generate [--force-refresh if requested]
4. Read data/generated_posts.json, get list of post IDs
5. For each post_id: call review_agent(post_id), check score
6. If score < 7: call revise_agent(post_id, suggestions)
7. Save results. Print summary line.

Rules:
- If any step returns count=0, print "STOP: <reason>" and call no more tools.
- Pass only post_id to subagents, never full JSON.
- No explanations. Output only counts and decisions.
```

---

### ReviewAgent

| Field | Value |
|-------|-------|
| Model | `claude-sonnet-4-6` |
| effort | `medium` |
| max_tokens | `1500` |
| max_turns | `1` |
| allowed_tools | none |
| permission_mode | `dontAsk` |

**System prompt:**
```
You are an adversarial content reviewer. Find weaknesses, not strengths.
Return ONLY valid JSON: {"score": <1-10>, "issues": ["...", "..."], "suggestions": ["...", "..."]}
Score 7+ = publish-ready. Below 7 = needs revision.
No markdown. No commentary outside the JSON.
```

**User prompt template (head constructs this):**
```
Review post_id={post_id}. Read it from data/generated_posts.json.

Score on:
- Hook strength (slide 1 grabs attention instantly)
- Factual accuracy relative to the article summary
- Slide flow (logical progression 1→8)
- CTA quality (slide 8 drives engagement)
- Writing rules: ≤15 words/sentence, ≤2 emojis/slide

Return ONLY: {"score": <1-10>, "issues": [...], "suggestions": [...]}
```

ReviewAgent reads the post itself via `Read` tool — head never passes raw JSON inline.

---

### ReviseAgent

| Field | Value |
|-------|-------|
| Model | `claude-haiku-4-5` |
| effort | `low` |
| max_tokens | `4096` |
| max_turns | `1` |
| allowed_tools | none |
| permission_mode | `dontAsk` |

**User prompt template (head constructs this):**
```
Edit post_id={post_id}. Read it from data/generated_posts.json.
Apply these suggestions: {suggestions}
Return ONLY the revised carousel JSON in the exact same schema. No other text.
```

ReviseAgent reads the post itself via `Read` tool — head never passes raw JSON inline.

---

## Decision Logic (Head Agent)

```
1. Run: python cli.py --scrape
   Read: data/latest_articles.json
   → count = 0 → log "No new articles" → STOP

2. Run: python cli.py --dedup
   Read: data/deduped_articles.json
   → count = 0 → log "All duplicates" → STOP

3. Run: python cli.py --generate [--force-refresh]
   Read: data/generated_posts.json
   → count = 0 → log "Generation failed" → STOP

4. For each post in generated_posts.json:
   → Call ReviewAgent with carousel JSON + scoring rubric
   → Parse {score, issues, suggestions}
   → score >= 7: mark approved, keep original carousel
   → score <  7: call ReviseAgent(original_carousel, suggestions) → replace carousel

5. INSERT each approved post into generated_posts table
   Fields: article_title, carousel_json, status='pending_review', review_score, reviewed=1
   Note: review_score column must be added to generated_posts schema (ALTER TABLE or migration)
   Call: import src.db and use existing sqlite3 helpers — no subprocess needed

6. Print summary:
   "Scraped: X | Unique: Y | Generated: Z | Approved: N | Saved: N"
```

---

## Prompt Design Principles

- **Role in one line** — no verbose persona paragraphs
- **Output format declared upfront** — model locks to schema before generating
- **Hard constraints use "ONLY" and "No X"** — cuts hallucinated extra text
- **No few-shot examples** — schema is self-evident, examples waste tokens
- **Scoring rubric in user prompt, not system** — system prompt stays cacheable across calls
- **max_tokens capped per task** — ReviewAgent gets 1500 (critique + JSON), Haiku agents get minimum needed

---

## Cost Model

| Agent | Model | Est. calls/run | Est. tokens | Est. cost/run |
|-------|-------|---------------|-------------|---------------|
| OrchestratorAgent | **Haiku** | 1 session / 25 turns | ~3,000 in / 300 out total | ~$0.0003 |
| ReviewAgent | Sonnet | 10 posts | ~1,500 in / 400 out per call | ~$0.045 |
| ReviseAgent | Haiku | ~3 posts (30% fail review) | ~5,000 in / 4,000 out per call | ~$0.009 |
| **Total** | | | | **~$0.054/run** |

Existing carousel generation cost (M3): ~$0.29/run (15 articles × Haiku)  
**New overhead from orchestrator: ~$0.054 — adds ~19% to total run cost.**  
**Haiku head saves ~85% vs Sonnet head for orchestration turns (was $0.009 → now $0.0003).**

---

## New Files

```
src/orchestrator.py     ← OrchestratorAgent + ReviewAgent + ReviseAgent
```

**Nothing else changes.** Existing modules untouched.

### `src/orchestrator.py` structure

```python
# Constants
ORCH_SYSTEM    # head agent system prompt
REVIEW_SYSTEM  # reviewer system prompt
REVISE_SYSTEM  # reviser system prompt
REVIEW_RUBRIC  # scoring criteria (injected into user prompt, not system)

# Functions
async def run_review(post: dict) -> dict          # spawn ReviewAgent → {score, issues, suggestions}
async def run_revise(post: dict, review: dict) -> dict  # spawn ReviseAgent → revised carousel
async def run_pipeline(force: bool = False) -> None     # head agent loop
```

---

## CLI Integration

Add one `elif` block to `cli.py`:

```bash
python cli.py --run           # full pipeline
python cli.py --run --force   # force-refresh carousel generation
```

---

## Data Flow

```
data/latest_articles.json     ← written by --scrape
data/deduped_articles.json    ← written by --dedup
data/generated_posts.json     ← written by --generate (existing format)
data/pipeline.db              ← INSERT by orchestrator (generated_posts table)
```

No new files or schema changes required.

---

## Environment Variables

```
ANTHROPIC_API_KEY=    # already required for M3; reused by Agent SDK
```

---

## Schema Change

Add `review_score` column to `generated_posts`:
```sql
ALTER TABLE generated_posts ADD COLUMN review_score REAL;
ALTER TABLE generated_posts ADD COLUMN reviewed INTEGER DEFAULT 0;
```

This runs once at orchestrator startup if the columns don't exist (checked via `PRAGMA table_info`).

---

## Dependencies

```
claude-agent-sdk    # pip install claude-agent-sdk
```

Add to `requirements.txt`.

---

## Risks

| Risk | Mitigation |
|------|-----------|
| ReviewAgent returns malformed JSON | Wrap in try/except; on parse failure treat as score=5 and revise |
| ReviseAgent drifts from carousel schema | `_validate_carousel()` from carousel_gen.py re-used to validate revised output |
| Head agent hits max_turns before finishing | Set max_turns=25; 6 Bash steps + 10 reviews + 3 revisions = ~19 turns max |
| Budget overrun | max_budget_usd=0.15 hard cap; stops loop with error_max_budget_usd |
| Existing --generate cached results stale | Pass --force flag through from --run --force |

---

## Checkpoint

Full run completes without error:
```
python cli.py --run
Scraped: 15 | Unique: 10 | Generated: 10 | Approved: 10 | Saved: 10
```

All 10 rows in `generated_posts` have `status='pending_review'` and `review_score` populated.
