# Design: LangGraph + LiteLLM Orchestrator Refactor

**Date:** 2026-05-22  
**Status:** Approved  
**Replaces:** `M_orchestrator.md` (direct Anthropic API + for-loop orchestration)  
**Affects:** `src/orchestrator.py`, `src/carousel_gen.py`

---

## Problem

The current orchestrator (`src/orchestrator.py`) has three gaps:

1. **No resume on failure** — if the pipeline crashes at step 4 of 6, the next run restarts from step 1 (re-scrape, re-generate, re-pay)
2. **No observability** — no way to inspect where a run stopped or what state it left behind
3. **Provider lock-in** — all LLM calls are hardcoded to the Anthropic SDK; switching to a cheaper provider requires code changes across multiple files

---

## Solution

Replace the for-loop orchestrator with a **LangGraph StateGraph** (checkpointed pipeline) and replace all `anthropic.messages.create()` calls project-wide with **LiteLLM** (provider-agnostic completion interface).

---

## Architecture

```
python cli.py --run
      │
      ▼
LangGraph StateGraph (SqliteSaver checkpoint at data/pipeline_checkpoints.db)
      │
      ├─ [scrape]         → cmd_scrape() → articles_count in state
      │    └─ count=0 → STOP
      │
      ├─ [dedup]          → cmd_dedup() → unique_count in state
      │    └─ count=0 → STOP
      │
      ├─ [generate]       → cmd_generate() → posts in state
      │    └─ count=0 → STOP
      │
      ├─ [review_revise]  → for each post: litellm review → (litellm revise if score<7)
      │                   → reviewed_posts in state
      │
      └─ [save]           → INSERT to pipeline.db → saved_count in state
                          → print summary
```

**Resume behaviour:** On `--run`, LangGraph checks `data/pipeline_checkpoints.db` for today's thread (`pipeline-{YYYY-MM-DD}`). If an incomplete run exists, execution resumes from the last completed node — skipping already-done steps.

**Force behaviour:** `--run --force` deletes today's checkpoint thread before starting, forcing a full re-run.

---

## Pipeline State

```python
class PipelineState(TypedDict):
    force: bool           # passed from CLI --force flag
    articles_count: int   # set by scrape node
    unique_count: int     # set by dedup node
    posts: list[dict]     # set by generate node
    reviewed_posts: list[dict]  # set by review_revise node
    saved_count: int      # set by save node
    stop_reason: str | None     # set when pipeline exits early
```

---

## LiteLLM Integration

**Scope:** All `anthropic.messages.create()` calls project-wide replaced with `litellm.completion()`.

**Files changed:**
- `src/carousel_gen.py` — carousel generation (M3, most expensive step ~$0.29/run)
- `src/orchestrator.py` — review and revise calls (~$0.054/run)

**Pattern:**
```python
# before
resp = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=2048,
    system=SYSTEM_PROMPT,
    messages=[{"role": "user", "content": prompt}],
)
text = resp.content[0].text

# after
resp = litellm.completion(
    model=os.getenv("CAROUSEL_MODEL", "claude-haiku-4-5"),
    max_tokens=2048,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ],
)
text = resp.choices[0].message.content
```

**Provider switching — `.env` only, no code changes:**
```
# Claude (default)
CAROUSEL_MODEL=claude-haiku-4-5
REVIEW_MODEL=claude-sonnet-4-6
REVISE_MODEL=claude-haiku-4-5

# Gemini (cost comparison)
CAROUSEL_MODEL=gemini/gemini-1.5-flash
REVIEW_MODEL=gemini/gemini-1.5-pro
REVISE_MODEL=gemini/gemini-1.5-flash

# OpenAI (cost comparison)
CAROUSEL_MODEL=gpt-4o-mini
REVIEW_MODEL=gpt-4o
REVISE_MODEL=gpt-4o-mini
```

---

## Checkpoint / Resume Detail

| Property | Value |
|----------|-------|
| Storage backend | `SqliteSaver` (LangGraph built-in) |
| Checkpoint file | `data/pipeline_checkpoints.db` |
| Thread ID | `pipeline-{YYYY-MM-DD}` (one per calendar day) |
| Resume granularity | Node-level (not post-level within `review_revise`) |
| New day | Automatically starts fresh (new thread ID) |
| `--force` | Deletes today's thread before running |

**On failure mid-`review_revise`:** The node re-runs from the start of that node. Review/revise calls are stateless and idempotent, so re-running them is safe.

---

## New Environment Variables

Add to `.env` (all optional — defaults keep current behaviour):

```
CAROUSEL_MODEL=claude-haiku-4-5
REVIEW_MODEL=claude-sonnet-4-6
REVISE_MODEL=claude-haiku-4-5
```

**Provider API keys** — LiteLLM reads these from `.env` automatically:

| Provider | Key needed |
|----------|-----------|
| Claude (default) | `ANTHROPIC_API_KEY` (already set) |
| Gemini | `GEMINI_API_KEY` |
| OpenAI | `OPENAI_API_KEY` |

Only the key for the active provider needs to be present.

---

## Dependencies

Add to `requirements.txt`:
```
langgraph
litellm
```

Remove (no longer needed directly):
```
anthropic  # still installed as a transitive dep via litellm, but no longer imported directly
```

Note: `anthropic` stays in `requirements.txt` as an explicit dep — litellm uses it internally for Claude calls. Do not remove it.

---

## Files Changed

| File | Change |
|------|--------|
| `src/orchestrator.py` | Full rewrite — LangGraph StateGraph replaces for-loop; litellm replaces direct anthropic calls |
| `src/carousel_gen.py` | Replace `anthropic.Anthropic()` client + `messages.create()` with `litellm.completion()` |
| `requirements.txt` | Add `langgraph`, `litellm` |
| `.env` / `.env.example` | Add `CAROUSEL_MODEL`, `REVIEW_MODEL`, `REVISE_MODEL` |

**No other files change.** `fetcher.py`, `dedup.py`, `db.py`, `models.py`, `shared.py`, `cli.py` are untouched.

---

## Error Handling

All existing `try/except` behaviour is preserved:
- ReviewAgent JSON parse failure → treat as score=5, call ReviseAgent (unchanged)
- ReviseAgent invalid output → keep original carousel (unchanged)
- LiteLLM will raise `litellm.exceptions.APIConnectionError` etc. — these propagate up to the LangGraph node, which logs and marks the run as failed at that node (resumable on next `--run`)

---

## Risks

| Risk | Mitigation |
|------|-----------|
| LiteLLM response format differs per provider | Parse `resp.choices[0].message.content` — standard across all providers |
| Provider returns non-JSON for review/revise | Existing `_strip_markdown_fences()` + `json.loads()` + fallback unchanged |
| `SqliteSaver` checkpoint file grows unbounded | One thread per day; old threads are inert. Add periodic cleanup in M6 if needed. |
| Non-Claude providers have different token limits | `max_tokens` values already set conservatively (512 review, 2048 revise, 2048 carousel) — safe for all major providers |

---

## Out of Scope

- LangSmith / external tracing (no external service dependency)
- Claude Agent SDK (already removed in commit `ee5e4e4`)
- M5 image generation (Flux via Replicate — not an LLM call, unaffected)
- Any changes to M6 web dashboard or M7 Instagram publishing

---

## Checkpoint

Full run completes and is resumable:
```bash
python cli.py --run
# Scraped: 15 | Unique: 10 | Generated: 10 | Approved: 10 | Saved: 10

# Kill mid-run, then:
python cli.py --run
# Resumes from last completed node — no re-scrape, no re-generate

# Switch provider:
CAROUSEL_MODEL=gemini/gemini-1.5-flash python cli.py --run
# Full run completes using Gemini for carousel generation
```
