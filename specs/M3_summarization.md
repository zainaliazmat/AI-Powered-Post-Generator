# M3 — Carousel Generator

**Status:** In progress  
**Depends on:** M2  
**Blocks:** M5, M6

---

## Decision Log

| Decision | Choice | Reason |
|----------|--------|--------|
| LLM model | Claude Haiku (`claude-3-5-haiku-20241022`) | Replaces DeepSeek-V3; stable JSON output, one provider |
| Scope | Carousel generator (8 slides) | Combines original M3 (summary) + M4 (caption) in one call |
| M4 status | Absorbed into M3 | `caption_gen.py` no longer needed as a separate module |
| Caching | Content-hash file cache (`carousel_cache/`) | Avoids re-generating unchanged articles |
| Cost tracking | Built-in per-call usage stats | Haiku: $1/MTok input, $5/MTok output |

---

## Purpose

For each filtered + deduplicated article, generate a complete 8-slide Instagram carousel in one Claude Haiku API call. Each slide includes a title, subtitle, body copy, hashtags, and a Flux-ready image prompt. Output is stored in SQLite `generated_posts` and routed to the M6 human review queue.

This replaces both M3 (summarize) and M4 (caption) from the original spec.

---

## Input / Output

```
Input:  data/deduped_articles.json     (output of M2 --dedup)
Output: SQLite generated_posts rows    status = 'pending_review'
        data/generated_posts.json      (same data, for debugging)
        carousel_cache/<hash>.json     (per-article cache files)
```

---

## Carousel JSON Structure (per article)

```json
{
  "news_summary": "One powerful sentence summarizing the biggest takeaway",
  "total_slides": 8,
  "slides": [
    {
      "slide_number": 1,
      "title": "CATCHY HOOK (max 40 chars, use emoji)",
      "subtitle": "Supporting line (max 60 chars)",
      "body": "2-3 punchy sentences.\nShort. Impactful.",
      "hashtags": "#Hashtag1 #Hashtag2 #Hashtag3",
      "image_prompt": "50-100 words describing subject, colors, lighting, style, resolution"
    }
  ]
}
```

---

## Slide Guidelines

| Slide | Role |
|-------|------|
| 1 | Hook — biggest headline, create FOMO |
| 2–3 | Break down the story with data/numbers |
| 4–5 | Second angle or implications |
| 6–7 | Technical details simplified or future predictions |
| 8 | CTA + recap + question for comments |

---

## Module: `src/carousel_gen.py`

### Class: `ClaudeCarouselGenerator`

| Method | Responsibility |
|--------|---------------|
| `__init__(api_key, cache_dir, max_retries)` | Init client, cache dir, usage stats |
| `generate_carousel(article, force_refresh)` | Main entry — cache check → prompt → parse → validate → cache write |
| `batch_generate(articles)` | Loop over list with 1s sleep between calls; collect results |
| `_build_prompt(article)` | Build the full user prompt from article fields |
| `_call_api(prompt)` | Call Claude with retry + exponential backoff; update usage stats |
| `_extract_json(response)` | Parse JSON; fallback strips code fences; raises on failure |
| `_validate_carousel(carousel)` | Assert required fields; warn if slide count ≠ 8 |
| `get_usage_report()` | Return cost summary dict |

---

## Tasks

| ID | Task | Details |
|----|------|---------|
| T3.1 | Install anthropic SDK | Add to requirements.txt; add ANTHROPIC_API_KEY to .env |
| T3.2 | Update DB schema | Add `carousel_json` + `article_hash` columns to `generated_posts` |
| T3.3 | Write `src/carousel_gen.py` | Full class as specified above |
| T3.4 | Add `--generate` CLI command | Reads deduped_articles.json → calls batch_generate → saves to DB + JSON |
| T3.5 | End-to-end test | 3 real articles → 3 `generated_posts` rows in SQLite, status=pending_review |

**Checkpoint:** 3 articles from `deduped_articles.json` → 3 rows in `generated_posts` with valid 8-slide carousel JSON, `status='pending_review'`.

---

## Prompt System Message

```
You are an expert at generating structured JSON for social media carousels.
Return ONLY valid JSON. Never include markdown formatting or explanations.
```

---

## Cost Reference

| Articles | Est. Input Tokens | Est. Output Tokens | Est. Cost |
|----------|-------------------|--------------------|-----------|
| 1 | ~2,000 | ~3,500 | $0.0195 |
| 15 (one scrape cycle) | ~30,000 | ~52,500 | $0.29 |
| 100/day | ~200,000 | ~350,000 | $1.95 |

---

## Environment Variables

```
ANTHROPIC_API_KEY=sk-ant-...    # Required for M3
```

---

## Risks

- **Empty summary field**: Many HN articles have `summary = "Comments"`. Prompt must handle title-only input gracefully; add `low_confidence=true` flag on output.
- **JSON parse failure**: Claude occasionally wraps output in markdown. `_extract_json()` must strip code fences before parsing.
- **Cost at scale**: 15 articles × 2,000 input tokens = 30K tokens/cycle. At 6 cycles/day = $1.75/day — acceptable. Monitor via `get_usage_report()`.
- **Cache staleness**: Cache key is a hash of `title + url + summary`. If a source updates an article's summary, the old carousel is served from cache. Acceptable for MVP.
