import logging
import re
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

_BRAND_ASSETS_DIR = Path("data/brand_assets")

_BRAND_STYLES: dict[str, dict] = {
    "nvidia.com": {
        "colors": ["#76B900"],
        "style_cue": "dark background with NVIDIA green (#76B900) accents, GPU hardware and server arrays, photorealistic corporate photography",
    },
    "google.com": {
        "colors": ["#4285F4", "#EA4335", "#FBBC05", "#34A853"],
        "style_cue": "clean minimalist white background, Google multicolor accents, professional campus photography",
    },
    "microsoft.com": {
        "colors": ["#F25022", "#7FBA00", "#00A4EF", "#FFB900"],
        "style_cue": "blue-heavy professional corporate photography, Windows aesthetic, clean modern office",
    },
    "apple.com": {
        "colors": ["#000000", "#FFFFFF"],
        "style_cue": "stark white or black background, product-focused, minimalist, high-gloss photography",
    },
    "meta.com": {
        "colors": ["#0866FF"],
        "style_cue": "blue gradient background, social connectivity or VR/AR imagery, modern corporate",
    },
    "openai.com": {
        "colors": ["#000000"],
        "style_cue": "dark minimal background, abstract AI neural network patterns, clean professional",
    },
    "amazon.com": {
        "colors": ["#FF9900"],
        "style_cue": "orange accents on dark background, logistics and cloud infrastructure, AWS server rooms",
    },
    "tesla.com": {
        "colors": ["#CC0000"],
        "style_cue": "red accents, electric vehicles on clean backgrounds, minimalist product photography",
    },
    "samsung.com": {
        "colors": ["#1428A0"],
        "style_cue": "deep Samsung blue, consumer electronics and device hardware, clean studio photography",
    },
    "intel.com": {
        "colors": ["#0071C5"],
        "style_cue": "Intel blue accents, silicon chip and processor close-ups, clean corporate photography",
    },
}

_CLEANUP_PATTERNS = [
    r"cyberpunk",
    r"neon\b",
    r"futuristic",
    r"cinematic lighting",
    r"4K resolution",
    r"\b4K\b",
]

_DEFAULT_STYLE = "corporate tech brand, professional photography style, clean background"


def _download_logo(domain: str, dest: Path) -> bool:
    try:
        resp = requests.get(
            f"https://logo.clearbit.com/{domain}",
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code == 200:
            dest.write_bytes(resp.content)
            return True
        logger.warning("Clearbit returned %d for %s", resp.status_code, domain)
        return False
    except Exception as e:
        logger.warning("Clearbit fetch failed for %s: %s", domain, e)
        return False


def fetch_logo(domain: str, max_age_days: int = 90) -> Path | None:
    _BRAND_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _BRAND_ASSETS_DIR / f"{domain}.png"

    if cache_path.exists():
        age_seconds = time.time() - cache_path.stat().st_mtime
        if age_seconds < max_age_days * 86400:
            return cache_path
        logger.info("Logo cache stale for %s — refreshing", domain)
        if _download_logo(domain, cache_path):
            return cache_path
        logger.warning("Logo refresh failed for %s — using stale cache", domain)
        return cache_path

    return cache_path if _download_logo(domain, cache_path) else None


def enrich_prompt(base_prompt: str, brand_domain: str | None) -> str:
    if not brand_domain:
        return base_prompt

    cleaned = base_prompt
    for pattern in _CLEANUP_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    brand = _BRAND_STYLES.get(brand_domain)
    if brand:
        colors_str = ", ".join(brand["colors"])
        enriched = f"{brand['style_cue']}. {cleaned}. Brand colors: {colors_str}."
    else:
        enriched = f"{_DEFAULT_STYLE}. {cleaned}."

    if len(enriched) > 1000:
        enriched = enriched[:997] + "..."

    return enriched


def composite_badge(image_path: Path, logo_path: Path) -> Path:
    BADGE_SIZE = 80
    BORDER_WIDTH = 3
    INSET = 20
    inner_size = BADGE_SIZE - 2 * BORDER_WIDTH

    with Image.open(image_path).convert("RGBA") as base:
        with Image.open(logo_path).convert("RGBA") as logo:
            logo_resized = logo.resize((inner_size, inner_size), Image.LANCZOS)

            # Circular mask for logo interior
            logo_mask = Image.new("L", (inner_size, inner_size), 0)
            ImageDraw.Draw(logo_mask).ellipse(
                (0, 0, inner_size - 1, inner_size - 1), fill=255
            )
            logo_circle = Image.new("RGBA", (inner_size, inner_size), (0, 0, 0, 0))
            logo_circle.paste(logo_resized, mask=logo_mask)

            # Badge canvas — white filled circle for border ring
            badge = Image.new("RGBA", (BADGE_SIZE, BADGE_SIZE), (0, 0, 0, 0))
            border_mask = Image.new("L", (BADGE_SIZE, BADGE_SIZE), 0)
            ImageDraw.Draw(border_mask).ellipse(
                (0, 0, BADGE_SIZE - 1, BADGE_SIZE - 1), fill=255
            )
            white_ring = Image.new("RGBA", (BADGE_SIZE, BADGE_SIZE), (255, 255, 255, 255))
            badge.paste(white_ring, mask=border_mask)
            badge.paste(logo_circle, (BORDER_WIDTH, BORDER_WIDTH), logo_circle)

            # Paste badge at top-right corner
            x = base.width - BADGE_SIZE - INSET
            y = INSET
            base.paste(badge, (x, y), badge)
            base.convert("RGB").save(image_path)

    return image_path
