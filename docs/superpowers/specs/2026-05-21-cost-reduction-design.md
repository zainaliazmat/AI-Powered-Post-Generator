# Cost Reduction Design — LLM Pipeline

**Date:** 2026-05-21
**Status:** Approved
**Goal:** Reduce per-cycle LLM cost from $0.25–0.30 to ~$0.045 (85% reduction) with no quality loss.

## Root Cause

`run_review()` and `run_revise()` in `src/orchestrator.py` use the Claude Agent SDK `query()` function. This spawns a full Claude Code subprocess per call, loading the entire Claude Code system prompt (~5,000–8,000 tokens of tool definitions) on top of the actual task prompt. Combined with `claude-sonnet-4-6` pricing and `effort="medium"` triggering extended thinking tokens, each review call costs ~$0.04–0.08. With 3–5 articles per cycle, this reaches $0.25–0.30.

`carousel_gen.py` already uses the right pattern: direct `anthropic.messages.create()` calls with no SDK overhead.

## Changes

### 1. `src/orchestrator.py` — Replace Agent SDK with direct Anthropic API

**`run_review()`:**
- Remove: `query()` from `claude_agent_sdk`, `ClaudeAgentOptions`, `ResultMessage`
- Add: `anthropic.Anthropic()` client, `client.messages.create()`
- Model: `claude-haiku-4-5` (was `claude-sonnet-4-6`)
- `max_tokens`: `512` (review output is a small JSON: score + issues + suggestions)
- Remove `async def` — becomes a regular synchronous function

**`run_revise()`:**
- Same transport swap: `query()` → `client.messages.create()`
- Model stays `claude-haiku-4-5`
- `max_tokens`: `2048` (revised carousel output is ~1200 tokens)
- Remove `async def` — becomes synchronous

**`run_pipeline()`:**
- Remove `async def` — becomes a regular `def`
- Remove `await asyncio.sleep(1)` → `time.sleep(1)`
- Remove `await run_review(...)` / `await run_revise(...)` → direct calls

**Imports to remove:** `asyncio`, `claude_agent_sdk` (entire import line)
**Imports to add:** `anthropic`, `time`

The Anthropic client is instantiated once at module level (same pattern as `carousel_gen.py`).

### 2. `src/carousel_gen.py` — Lower `max_tokens`

- Change `max_tokens=4096` → `max_tokens=2048`
- Rationale: measured carousel output is ~1200 tokens; 2048 gives 70% headroom with no truncation risk.

### 3. `cli.py` — Remove `asyncio.run()`

`run_pipeline()` is currently called via `asyncio.run(run_pipeline(...))`. After making it synchronous, this becomes a direct call: `run_pipeline(force=args.force)`.

## Cost Estimate After Changes (5 articles per cycle)

| Step | Model | Input | Output | Cost |
|---|---|---|---|---|
| Carousel gen ×5 | Haiku 4.5 | ~1500 tok | ~1200 tok | ~$0.027 |
| Review ×5 | Haiku 4.5 | ~1400 tok | ~300 tok | ~$0.007 |
| Revise ×2 (est. 40% fail rate) | Haiku 4.5 | ~1800 tok | ~1200 tok | ~$0.011 |
| **Total** | | | | **~$0.045** |

## Files Changed

| File | Change |
|---|---|
| `src/orchestrator.py` | Replace SDK calls with direct Anthropic API; remove async; Haiku for review |
| `src/carousel_gen.py` | `max_tokens` 4096 → 2048 |
| `cli.py` | Remove `asyncio.run()` wrapper around `run_pipeline` |

## What Does NOT Change

- Prompt text for `_REVIEW_PROMPT` and `_REVISE_PROMPT` — identical
- Review logic (score threshold 7, revise if below)
- `_validate_carousel()` call after revise
- `save_reviewed_post()` DB save
- All other pipeline steps (scrape, dedup, generate)
- Carousel generation model (Haiku, unchanged)
