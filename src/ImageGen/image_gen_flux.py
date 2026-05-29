import logging
import re
import time
import urllib.request
from pathlib import Path

import replicate
from PIL import Image, ImageDraw

from ..brand import composite_badge, fetch_logo

logger = logging.getLogger(__name__)

_IMAGES_DIR = Path("data/images")
_REPLICATE_RATE_LIMIT_SLEEP = 11  # seconds; Replicate low-credit tier: 6 req/min burst=1

# ---------------------------------------------------------------------------
# Brand-aware prompt enrichment (Flux-specific; not used by Pillow renderer)
# ---------------------------------------------------------------------------

_BRAND_STYLES: dict[str, dict] = {
    "nvidia.com":    {"colors": "deep black, neon green #76B900", "style": "high-tech, precision, photorealistic"},
    "openai.com":    {"colors": "clean white, subtle gradient, black", "style": "minimal, modern AI"},
    "google.com":    {"colors": "bold primary colours, clean white", "style": "clean, modern, approachable"},
    "anthropic.com": {"colors": "warm beige, dark charcoal", "style": "thoughtful, refined"},
    "apple.com":     {"colors": "white, silver, clean gradients", "style": "minimal, premium, product photography"},
    "microsoft.com": {"colors": "Microsoft blue, white", "style": "professional, enterprise, clean surfaces"},
    "meta.com":      {"colors": "blue gradient, white", "style": "social, connected, modern"},
    "x.com":         {"colors": "black, white, high contrast", "style": "bold, minimal"},
}
_DEFAULT_STYLE = {"colors": "clean white, dark blue, modern", "style": "professional, tech editorial"}

_CLEANUP_PATTERNS = [
    r"\bcyberpunk\b", r"\bneon\b", r"\bfuturistic\b",
    r"\bdystopian\b", r"\bneon-lit\b",
]


def enrich_prompt(prompt: str, brand_domain: str | None) -> str:
    cleaned = prompt
    for pattern in _CLEANUP_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    style = _BRAND_STYLES.get(brand_domain or "", _DEFAULT_STYLE) if brand_domain else _DEFAULT_STYLE
    return f"{cleaned}, {style['style']}, color palette: {style['colors']}"[:1000]


# ---------------------------------------------------------------------------
# Pillow text-card fallback
# ---------------------------------------------------------------------------

def _image_path(post_id: int, slide_number: int) -> Path:
    _IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    return _IMAGES_DIR / f"{post_id}_{slide_number}.png"


def _pillow_text_card(post_id: int, slide: dict) -> Path:
    path = _image_path(post_id, slide.get("slide_number", 0))
    img = Image.new("RGB", (1080, 1350), color="#1a1a2e")
    draw = ImageDraw.Draw(img)
    draw.text(
        (540, 675),
        slide.get("title", "Image unavailable"),
        fill="white",
        anchor="mm",
    )
    img.save(path)
    return path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_for_slide(
    post_id: int,
    slide: dict,
    brand_domain: str | None,
    og_image_url: str = "",  # accepted for interface parity; Flux uses slide["image_prompt"]
) -> Path:
    try:
        enriched = enrich_prompt(slide["image_prompt"], brand_domain)
        output = replicate.run(
            "black-forest-labs/flux-schnell",
            input={
                "prompt": enriched,
                "aspect_ratio": "4:5",
                "output_format": "png",
                "num_outputs": 1,
            },
        )
        image_url = str(output[0])
        dest = _image_path(post_id, slide["slide_number"])
        urllib.request.urlretrieve(image_url, dest)

        if brand_domain:
            logo = fetch_logo(brand_domain)
            if logo:
                composite_badge(dest, logo)

        return dest
    except Exception as e:
        logger.error(
            "Flux image gen failed for post %d slide %d: %s — falling back to text card",
            post_id, slide.get("slide_number", 0), e,
        )
        return _pillow_text_card(post_id, slide)


def generate_for_post(
    post_id: int,
    carousel: dict,
    brand_domain: str | None,
) -> list[Path]:
    paths = []
    for i, slide in enumerate(carousel.get("slides", [])):
        if i > 0:
            time.sleep(_REPLICATE_RATE_LIMIT_SLEEP)
        paths.append(generate_for_slide(post_id, slide, brand_domain))
    return paths


def generate_for_post_with_events(
    post_id: int,
    carousel: dict,
    brand_domain: str | None,
) -> tuple[list[Path], list[dict]]:
    """Same behavior as generate_for_post, plus per-slide event metadata."""
    paths: list[Path] = []
    events: list[dict] = []
    for i, slide in enumerate(carousel.get("slides", [])):
        if i > 0:
            time.sleep(_REPLICATE_RATE_LIMIT_SLEEP)
        raw_prompt = slide.get("image_prompt", "")
        enriched = enrich_prompt(raw_prompt, brand_domain)
        try:
            output = replicate.run(
                "black-forest-labs/flux-schnell",
                input={
                    "prompt": enriched,
                    "aspect_ratio": "4:5",
                    "output_format": "png",
                    "num_outputs": 1,
                },
            )
            image_url = str(output[0])
            dest = _image_path(post_id, slide["slide_number"])
            urllib.request.urlretrieve(image_url, dest)
            if brand_domain:
                logo = fetch_logo(brand_domain)
                if logo:
                    composite_badge(dest, logo)
            path = dest
            status = "ok"
        except Exception as exc:
            logger.error(
                "Flux image gen failed for post %d slide %d: %s — falling back to text card",
                post_id, slide.get("slide_number", 0), exc,
            )
            path = _pillow_text_card(post_id, slide)
            status = "fallback"
        paths.append(path)
        events.append({
            "idx": slide.get("slide_number", 0),
            "prompt": raw_prompt,
            "enriched_prompt": enriched,
            "path": str(path),
            "status": status,
        })
    return paths, events
