# Brand-Aware Image Generation — Design Spec

**Date:** 2026-05-22  
**Milestone:** M5 (Image Generation)  
**Status:** Approved — ready for implementation planning

---

## Goal

Generate Instagram carousel images that look like real brand press assets — not generic AI art. When an article is about NVIDIA, the image uses NVIDIA green, GPU hardware imagery, and carries the actual NVIDIA logo composited as a corner badge. Viewers recognise the brand instantly.

---

## Full Pipeline Architecture

The complete pipeline decomposes into 12 single-responsibility nodes across three phases:

### Phase 1 — Pre-approval (auto-runs on `python cli.py --run`)

| # | Node | Responsibility |
|---|------|----------------|
| 1 | `scrape` | Fetch articles from all RSS sources |
| 2 | `dedup` | Merge duplicates, score virality |
| 3 | `generate_carousel` | Haiku: generate slides JSON + extract `brand_domain` |
| 4 | `review` | Sonnet: score carousel 1–10 |
| 5 | `revise` | Haiku: rewrite slides (conditional — only if score < 7) |
| 6 | `save_draft` | Write post to DB as `pending_review` |

### Phase 2 — Post-approval (triggered by human approval in M6 web UI)

| # | Node | Responsibility |
|---|------|----------------|
| 7 | `fetch_brand_assets` | Download + cache brand logo from Clearbit |
| 8 | `enrich_prompts` | Rewrite each slide's `image_prompt` with brand context |
| 9 | `generate_images` | Call Flux.1-schnell per slide |
| 10 | `composite_images` | Overlay circular logo badge on each image via Pillow |
| 11 | `save_images` | Write image paths to DB, set status `image_ready` |

### Phase 3 — Publishing (M7)

| # | Node | Responsibility |
|---|------|----------------|
| 12 | `publish` | Post approved carousel to Instagram |

**Design principle:** Each node does exactly one thing. No node makes two different kinds of API calls or mixes concerns. LangGraph checkpointing means any node can fail and be retried independently.

---

## LangGraph Changes to `orchestrator.py`

### Split `review_revise` into two nodes

```
BEFORE: review_revise → save

AFTER:  review → [conditional edge] → revise → save_draft
                                    ↘ save_draft (if score ≥ 7)
```

- `review` node: calls `run_review()`, writes `review_score` + `review_suggestions` to state
- `revise` node: calls `run_revise()`, only reached when `review_score < 7`
- Conditional edge: `lambda s: "revise" if s["review_score"] < 7 else "save_draft"`

### Post-approval sub-graph (new)

A separate LangGraph graph (`image_pipeline`) is assembled in `image_gen.py` and triggered by the M6 approval action. Input: `post_id`. Nodes 7–11 run sequentially with checkpointing.

---

## `carousel_gen.py` Changes

Add `brand_domain` as a top-level field in the JSON schema Haiku outputs.

### Prompt addition (appended to `_PROMPT_TEMPLATE`)

```
## BRAND EXTRACTION RULES:
Identify the SINGLE most relevant company this article focuses on.

Rules:
- If article announces something by one company → use that company's domain
- If article mentions a subsidiary, use the parent company's primary domain
  (e.g. "Google DeepMind" → "google.com", "Microsoft GitHub Copilot" → "microsoft.com")
- If two companies are equally featured (e.g. "NVIDIA and AMD partner") → set null
- If article is about a person, technology, or concept with no clear company → set null
- Use the company's primary .com domain (not regional, not product-specific)

Examples:
"OpenAI releases GPT-5" → "openai.com"
"Microsoft GitHub Copilot update" → "microsoft.com"
"Jensen Huang keynote at GTC" → "nvidia.com"
"Python 3.13 released" → null

Return brand_domain as string or null. No explanations.
```

### Output schema change

```json
{
  "brand_domain": "nvidia.com",
  "news_summary": "...",
  "total_slides": 4,
  "slides": [...]
}
```

`brand_domain` is `string | null`. `_validate_carousel()` does **not** change — `brand_domain` is optional metadata, not a required slide field.

---

## `src/brand.py` — New Module

Three focused, independently testable functions.

### `fetch_logo(domain: str, max_age_days: int = 90) -> Path | None`

- Cache path: `data/brand_assets/{domain}.png`
- Cache hit and age < 90 days → return path immediately (no network call)
- Cache hit but stale (≥ 90 days) → attempt synchronous re-fetch; on failure, use cached version and log a warning
- Cache miss → `GET https://logo.clearbit.com/{domain}` (5s timeout)
  - 200 → save PNG to cache, return path
  - 404 or any error → log warning, return `None`
- 90-day TTL guards against brand rebrands (e.g. Twitter → X). Re-fetch is synchronous — no background threads.

### `enrich_prompt(base_prompt: str, brand_domain: str | None) -> str`

- If `brand_domain` is `None` → return `base_prompt` unchanged
- Strips conflicting generic AI-art terms from `base_prompt` before injecting brand style, using case-insensitive regex: `cyberpunk`, `neon`, `futuristic`, `cinematic lighting`, `4K resolution`. This ensures brand style wins over carousel_gen's defaults.
- Injects brand style at the start of the prompt (highest priority position for Flux)
- Hard cap: truncates final prompt to 1000 chars to stay within Flux.1-schnell's token limit
- **v2 planned:** For unknown brands, auto-generate style cues via a one-time Haiku call (~$0.0005), cached to `data/brand_assets/{domain}_style.json`. Not in M5.
- Looks up built-in style dict for known brands:

| Domain | Colors | Style cue |
|--------|--------|-----------|
| nvidia.com | #76B900 | Dark background, GPU hardware, photorealistic |
| google.com | #4285F4 #EA4335 #FBBC05 #34A853 | Minimalist, colorful, clean |
| microsoft.com | #F25022 #7FBA00 #00A4EF #FFB900 | Blue-heavy, professional corporate |
| apple.com | #000000 #FFFFFF | Stark white/black, product-focused |
| meta.com | #0866FF | Blue gradient, social/VR imagery |
| openai.com | #000000 | Dark minimal, abstract AI |
| amazon.com | #FF9900 | Orange accents, logistics/cloud |
| tesla.com | #CC0000 | Red accents, vehicles, clean |
| samsung.com | #1428A0 | Deep blue, device hardware |
| intel.com | #0071C5 | Intel blue, chip/processor imagery |

- Unknown brands → appends: `"corporate tech brand, professional photography style, clean background"`
- Rewrites the generic cyberpunk defaults from `carousel_gen.py` into brand-accurate equivalents

### `composite_badge(image_path: Path, logo_path: Path) -> Path`

- Opens Flux output image (1080×1350px RGBA)
- Resizes logo to 80×80px
- Applies circular PIL mask to create a circular logo
- Adds a 3px white ring border around the circle
- Pastes at top-right corner with 20px inset from each edge
- Saves in-place (overwrites Flux output), returns path

---

## `src/image_gen.py` — New Module

### Public API

```python
def generate_for_post(post_id: int, carousel: dict, brand_domain: str | None) -> list[Path]
def generate_for_slide(post_id: int, slide: dict, brand_domain: str | None) -> Path
```

### `generate_for_slide` flow

```
1. enriched = brand.enrich_prompt(slide["image_prompt"], brand_domain)
2. output = replicate.run("black-forest-labs/flux-schnell",
       input={"prompt": enriched, "aspect_ratio": "4:5", "output_format": "png", "num_outputs": 1})
3. download output[0] → data/images/{post_id}_{slide_number}.png
4. logo = brand.fetch_logo(brand_domain) if brand_domain else None
5. if logo: brand.composite_badge(image_path, logo)
6. return image_path
```

Fallback: any exception in steps 2–5 → call `_pillow_text_card(slide)`, log error.

### `_pillow_text_card(slide: dict) -> Path` (fallback only)

Minimal implementation — this is a failure fallback, not a feature:

```python
def _pillow_text_card(slide: dict) -> Path:
    img = Image.new('RGB', (1080, 1350), color='#1a1a2e')
    draw = ImageDraw.Draw(img)
    draw.text((540, 675), slide.get('title', 'Image unavailable'),
              fill='white', anchor='mm')
    path = _image_path(slide)
    img.save(path)
    return path
```

No brand colors, no logo. Dark background + white text. Its only job is to ensure there is always *something* in the image slot.

### Image storage

`data/images/{post_id}_{slide_number}.png` — 1080×1350px (Instagram 4:5 portrait).

---

## Error Handling & Fallbacks

| Failure point | Behaviour |
|---|---|
| Clearbit 404 | Skip badge — pure Flux image used |
| Clearbit timeout (>5s) | Same as 404, log warning |
| Flux API error | Fall back to Pillow text card |
| Pillow composite error | Keep Flux image without badge, log error |
| `brand_domain` is null | Pure Flux image, generic prompt, no badge |
| All image gen fails | Post status set to `failed`, visible to human reviewer |

---

## File Changes Summary

| File | Change type | What changes |
|---|---|---|
| `src/carousel_gen.py` | Modify | Add `brand_domain` extraction to prompt + JSON schema |
| `src/orchestrator.py` | Modify | Split `review_revise` → `review` + `revise` nodes |
| `src/brand.py` | New | `fetch_logo`, `enrich_prompt`, `composite_badge` |
| `src/image_gen.py` | New | `generate_for_post`, `generate_for_slide`, `_pillow_text_card` |
| `data/brand_assets/` | New dir | Logo cache (created on first fetch) |
| `data/images/` | New dir | Generated images (created on first image gen) |

---

## What Was Ruled Out

- **NewsAPI real-image fetching:** Style-mimicry of copyrighted news photos is legally grey and adds API cost and dependency. Dropped.
- **FLUX.1-redux (img2img):** Not needed — brand feel comes from prompt enrichment + real logo compositing, not style transfer.
- **Hardcoded brand dictionary only:** Won't scale. LLM extraction handles any company; style dict is just a quality booster for the top 10.
- **Top-bar or bottom-bar logo placement:** Corner badge chosen — image-first, minimal footprint, recognisable.

---

## Open Questions (resolved)

| Question | Decision |
|---|---|
| Logo source | LLM-extracted domain → Clearbit API, cached to disk |
| Logo placement | Top-right corner badge, 80×80px circular, 20px inset |
| When to generate images | After human approval in M6 (not during pipeline run) |
| Flux model | Flux.1-schnell via Replicate (no change from M5 base spec) |
| Reference image fetching | Not used — prompt enrichment + compositing is sufficient |
