# M5 — Image Generation

**Status:** ✅ Implemented (2026-05-22) — See `docs/superpowers/specs/2026-05-22-brand-aware-image-gen-design.md` for full design  
**Implemented as:** `src/brand.py` (fetch_logo, enrich_prompt, composite_badge) + `src/image_gen.py` (generate_for_post/slide, _pillow_text_card fallback)  
**CLI trigger:** `python cli.py --images <post_id>`  
**Depends on:** M4  
**Blocks:** M6

---

## Purpose

Produce at least one image per approved post. Two tiers: a cheap/fast Pillow text card (always works, no API cost) and an AI-generated Flux.1-schnell image (better visuals, ~$0.003/image via Replicate).

A router decides which tier to use based on caption + tone.

---

## Input / Output

```
Input:  generated_posts rows with caption + tone, status='captioned'
Output: generated_posts rows updated with image_url + image_type
        Image file saved to data/images/
        status='image_ready'
```

---

## Tasks

| ID | Task | Details |
|----|------|---------|
| T5.1 | Image complexity detector | Caption + tone → `"simple"` or `"complex"` |
| T5.2 | Pillow text-card generator | Text + brand colors → 1080×1350px PNG (4:5 ratio) |
| T5.3 | Flux.1-schnell API call | Derived prompt → image URL via Replicate |
| T5.4 | Image router | Calls T5.2 if `simple`, T5.3 if `complex` → stores result |
| T5.5 | Carousel builder | Groups 3–5 images into ordered carousel metadata |

**Checkpoint:** Each captioned post has an image file + `image_type` in DB.

---

## Pillow Text Card Design (T5.2)

Minimum viable design:
```
┌─────────────────────────────┐
│  [BRAND LOGO / NAME]        │  ← top bar, brand color
│                             │
│   HEADLINE TEXT             │  ← bold, 3–4 lines max
│   in large readable font    │
│                             │
│   Source: TechCrunch        │  ← small footer text
│   @yourhandle               │
└─────────────────────────────┘
```

Parameters to configure:
- Background color (per tone: e.g. dark for professional, bright for hype)
- Font (Pillow ships with basic fonts; custom `.ttf` file needed for brand fonts)
- Brand colors (hex values in `.env` or config)
- Output size: 1080×1350px (Instagram 4:5 portrait)

---

## Complexity Detector Logic (T5.1)

Use Pillow (simple, free) when:
- Tone is `professional`
- Caption is fact-dense (numbers, company names)
- Topic is regulation, policy, funding

Use Flux (complex, paid) when:
- Tone is `witty` or `hype`
- Caption references a visual concept (robot, chip, rocket, etc.)
- Score above 70 (high-virality articles deserve better visuals)

This can be a simple rule set — no LLM needed.

---

## Flux.1-schnell via Replicate (T5.3)

```python
import replicate

output = replicate.run(
    "black-forest-labs/flux-schnell",
    input={
        "prompt": image_prompt,
        "aspect_ratio": "4:5",
        "output_format": "png",
        "num_outputs": 1,
    }
)
image_url = output[0]
```

Image prompt derived from caption (not full caption — 20-30 word visual description).

Example derivation:
- Caption: "OpenAI just dropped GPT-5 and it benchmarks above everything. Wild times 🚀"
- Image prompt: "Futuristic AI brain with glowing neural networks, dark background, cinematic, no text"

---

## Key Decisions to Discuss

**1. Where are images stored?**
- **Local `data/images/` folder**: fits the local-only project model; served by FastAPI as static files
- **Cloudflare R2 / S3**: more robust if ever deployed to a server, but unnecessary for local use
- Decision: **`data/images/` local folder** — matches the local-only SQLite approach; FastAPI mounts `/static/images/` to serve them in the web UI

**2. Flux.1-schnell: Replicate vs ComfyUI**
- **Replicate** (~$0.003/image): pay per run, no setup, just API key. Best for MVP.
- **ComfyUI (self-hosted)**: zero per-image cost but needs a GPU machine (your local, a Runpod instance, etc.)
- Recommendation: Replicate for MVP, ComfyUI later if volume justifies it

**3. Should every post have an image before entering review?**
- Yes (current spec): image is generated before the human sees it — reviewer approves caption + image together
- Alternative: generate image only after caption is approved — saves cost on rejected captions
- Recommendation: **generate image after caption approval** (M6 approval action triggers image gen) — this reduces wasted Flux API calls

**4. Carousel: automatic or manual?**
- Auto-carousel: pipeline groups related articles into one post (complex logic, prone to errors)
- Manual carousel: reviewer selects "make carousel" in the UI, picks which images to combine
- Recommendation: manual carousel via UI for MVP — auto-carousel is a Phase 2 feature

**5. Pillow fonts**
- Pillow's default font is ugly and small
- You'll want a `.ttf` file (e.g. Inter, Poppins, or your brand font)
- Decision: what font do you want on the text cards?

---

## Risks

- **Flux image may not match caption**: the image prompt is auto-derived — results are unpredictable. Human review is the safety net.
- **Replicate async**: Flux calls take 3–15 seconds. The pipeline must handle this asynchronously (don't block the web request).
- **Image moderation**: Flux can occasionally produce unexpected images. Replicate has basic content filtering but not foolproof.
- **Disk usage**: 30 images/day × 365 days = ~10,950 images/year. At ~200KB each = ~2GB/year. Add a periodic cleanup job that deletes images for rejected/old posts to keep disk usage manageable.

---

## Open Questions

1. Do you want Pillow text cards or Flux images as the default? (affects quality vs cost vs speed)
2. Do you have brand colors, logo, and font files ready for the Pillow cards?
3. Should image generation happen before or after human review of the caption?
4. Replicate or ComfyUI (self-hosted GPU) for Flux?
