import logging
import os
import time
from pathlib import Path

import requests
from PIL import Image, ImageDraw

logger = logging.getLogger(__name__)

_BRAND_ASSETS_DIR = Path("data/brand_assets")


def _download_logo(domain: str, dest: Path) -> bool:
    token = os.getenv("LOGO_DEV_TOKEN", "")
    url = f"https://img.logo.dev/{domain}?format=png&size=80"
    if token:
        url += f"&token={token}"
    try:
        resp = requests.get(url, timeout=5, headers={"User-Agent": "Mozilla/5.0"})
        if resp.status_code == 200:
            dest.write_bytes(resp.content)
            return True
        logger.warning("Logo.dev returned %d for %s", resp.status_code, domain)
        return False
    except Exception as e:
        logger.warning("Logo.dev fetch failed for %s: %s", domain, e)
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


def composite_badge(image_path: Path, logo_path: Path) -> Path:
    BADGE_SIZE = 80
    BORDER_WIDTH = 3
    INSET = 20
    inner_size = BADGE_SIZE - 2 * BORDER_WIDTH

    with Image.open(image_path).convert("RGBA") as base:
        with Image.open(logo_path).convert("RGBA") as logo:
            logo_resized = logo.resize((inner_size, inner_size), Image.LANCZOS)

            logo_mask = Image.new("L", (inner_size, inner_size), 0)
            ImageDraw.Draw(logo_mask).ellipse(
                (0, 0, inner_size - 1, inner_size - 1), fill=255
            )
            logo_circle = Image.new("RGBA", (inner_size, inner_size), (0, 0, 0, 0))
            logo_circle.paste(logo_resized, mask=logo_mask)

            badge = Image.new("RGBA", (BADGE_SIZE, BADGE_SIZE), (0, 0, 0, 0))
            border_mask = Image.new("L", (BADGE_SIZE, BADGE_SIZE), 0)
            ImageDraw.Draw(border_mask).ellipse(
                (0, 0, BADGE_SIZE - 1, BADGE_SIZE - 1), fill=255
            )
            white_ring = Image.new("RGBA", (BADGE_SIZE, BADGE_SIZE), (255, 255, 255, 255))
            badge.paste(white_ring, mask=border_mask)
            badge.paste(logo_circle, (BORDER_WIDTH, BORDER_WIDTH), logo_circle)

            x = base.width - BADGE_SIZE - INSET
            y = INSET
            base.paste(badge, (x, y), badge)
            base.convert("RGB").save(image_path)

    return image_path
