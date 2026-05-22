# M2 — Viral Filter

**Status:** Pre-discussion  
**Depends on:** M1  
**Blocks:** M3

---

## Purpose

Score every raw article for virality potential and discard low-signal noise before spending money on LLM calls. This is a pure Python module — no external APIs, no ML runtime unless you opt into MiniLM.

Target: from ~100 fetched articles, surface ~15–20 worth generating posts for.

---

## Input / Output

```
Input:  data/latest_articles.json  (output of python cli.py --scrape)
Output: data/filtered_articles.json  — articles that passed the score threshold,
        each article annotated with its viral_score
```

---

## Tasks

| ID | Task | Details |
|----|------|---------|
| T2.1 | Keyword scorer | Score title+summary against a curated keyword list; return 0–100 |
| T2.2 | Heuristic filter | Apply threshold (score ≥ 40 → keep); write keepers with `viral_score` to `filtered_articles.json` |
| T2.3 | MiniLM embedding filter (optional) | Embed article text; cosine-similarity to viral-topic embeddings; add to score |

**Checkpoint:** 100 fetched articles → ~15–20 in `data/filtered_articles.json` with sensible `viral_score` values.

---

## Scoring Logic (Proposed)

### Tier 1: Keyword Categories

Each match in title adds more weight than a match in summary.

| Category | Example Keywords | Score Bonus |
|----------|-----------------|-------------|
| AI/LLM launches | GPT, Claude, Gemini, Llama, model release, open source, benchmark | +25 |
| Big company moves | Google, OpenAI, Microsoft, Meta, Apple, acquisition, layoffs | +15 |
| Dev tools | GitHub, VS Code, framework, SDK, API, open source, release | +20 |
| Emerging tech | quantum, chip, robotics, autonomous, breakthrough | +20 |
| Regulation/law | ban, regulation, EU, FTC, lawsuit, antitrust | +15 |
| Funding/business | funding, Series, IPO, valuation, billion | +10 |

### Tier 2: Negative Signals (subtract)

| Signal | Score Penalty |
|--------|--------------|
| Title is a job listing | -50 |
| Title is a tutorial/how-to | -15 |
| Older than 24 hours | -30 |
| Source is a press release domain | -20 |

### Threshold

- Score ≥ 40 → `filtered=true` (send to M3)
- Score < 40 → `filtered=false` (archived, not processed)

---

## Key Decisions to Discuss

**1. Keyword list: hardcoded or editable via web UI?**
- Hardcoded in `filter.py`: simple, fast to implement
- Editable via M6 web UI: more flexible, lets you tune without code changes
- Recommendation: hardcode for MVP; add UI editor in Phase 2

**2. Threshold: 40 is arbitrary**
- Need to validate against real data after M1 is running
- Plan: run M2 in "log-only" mode for the first day (score everything but don't filter) to calibrate
- The threshold should probably be configurable via `.env` rather than hardcoded

**3. MiniLM: include in MVP or defer?**
- Adds `sentence-transformers` dependency (~500MB download)
- Requires pre-computed "viral topic" embeddings (who defines what's viral?)
- For MVP, keyword scoring alone is likely sufficient
- Recommendation: skip MiniLM entirely for MVP; re-evaluate after seeing false negatives

**4. Recency weighting**
- The spec mentions 24h retention — articles older than 24h should score low regardless
- Should recency be a hard cutoff (discard > 24h) or a soft penalty (−30)?
- Hard cutoff is simpler and avoids reprocessing old articles

**5. Duplicate topic detection**
- If 5 articles all cover the same GPT-5 release, should only 1 be promoted?
- For MVP: let all 5 pass (dedup happens at post level via content hash)
- Better later: cluster by topic and pick the highest-scoring article per cluster

---

## Risks

- **Keyword list goes stale**: tech moves fast — "GPT" was the hot word in 2023, might be different next year. Need a way to update the list.
- **Title-only scoring is naive**: "Everything you need to know about GPT" scores high but might be low-quality content.
- **Pakistani tech news context**: Google News is configured for Pakistan. Some articles that score high globally may not be relevant to your audience. Consider adding region/relevance signals.
- **False negatives**: important stories with unusual phrasing will be filtered out. No automated fix for this — the human review queue in M6 is the safety net.

---

## Open Questions

1. Do you have examples of past posts that did well? That would directly inform the keyword list.
2. Should the filter be audience-specific (e.g. Pakistani tech audience, devs, general tech enthusiasts)?
3. Do you want a "manual override" — a way to force an article through regardless of score?
