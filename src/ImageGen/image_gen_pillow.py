import logging
import urllib.request
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

from ..brand import composite_badge, fetch_logo

logger = logging.getLogger(__name__)

_IMAGES_DIR = Path("data/images")
_FONTS_DIR = Path("data/fonts")
_FONT_TTF_URL = "https://github.com/googlefonts/dm-fonts/raw/main/Sans/fonts/ttf/DMSans-ExtraBold.ttf"

_font_download_attempted = False

# ---------------------------------------------------------------------------
# Layout constants (exported so tests can reference them without magic numbers)
# ---------------------------------------------------------------------------
WHITE_HEIGHT = 607       # y: 0 … WHITE_HEIGHT-1  — white editorial zone
SEPARATOR_HEIGHT = 3     # y: WHITE_HEIGHT … WHITE_HEIGHT+2  — lime line
DARK_START = WHITE_HEIGHT + SEPARATOR_HEIGHT   # y: 610
FOOTER_START = 1255      # y: 1255 … 1349 — hashtag footer
CANVAS_W, CANVAS_H = 1080, 1350

LIME = (204, 255, 0)         # #CCFF00
DARK_BG = (13, 13, 13)       # base dark zone background
DARK_OVERLAY = (13, 13, 13, 80)  # RGBA overlay on og:image
FOOTER_COLOR = (120, 120, 120)

BADGE_DIAMETER = 64
BADGE_X, BADGE_Y = 36, 28   # top-left corner of the numbered badge

TEXT_MARGIN_X = 36
TITLE_Y = 100
TITLE_SIZE = 74
SUBTITLE_SIZE = 40
BODY_SIZE = 34


# ---------------------------------------------------------------------------
# Font loading
# ---------------------------------------------------------------------------

def _download_font() -> bool:
    global _font_download_attempted
    if _font_download_attempted:
        return False
    _font_download_attempted = True
    _FONTS_DIR.mkdir(parents=True, exist_ok=True)
    dest = _FONTS_DIR / "DMSans-ExtraBold.ttf"
    try:
        urllib.request.urlretrieve(_FONT_TTF_URL, dest)
        return True
    except Exception as exc:
        logger.warning("DM Sans font download failed: %s", exc)
        return False


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        _FONTS_DIR / "DMSans-ExtraBold.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        Path("/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        Path("/System/Library/Fonts/Helvetica.ttc"),
        Path("/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf"),
    ]
    if not candidates[0].exists():
        if not _download_font():
            logger.warning(
                "DM Sans unavailable — falling back to system font for Pillow renderer"
            )
    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except Exception:
                continue
    return ImageFont.load_default()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _image_path(post_id: int, slide_number: int) -> Path:
    _IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    return _IMAGES_DIR / f"{post_id}_{slide_number}.png"


def _fit_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale + center-crop *img* to exactly (w, h)."""
    src_w, src_h = img.size
    scale = max(w / src_w, h / src_h)
    new_w = int(src_w * scale)
    new_h = int(src_h * scale)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def _fetch_og_image_data(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=5, stream=True)
        if resp.status_code != 200:
            return None
        content = b""
        size_limit = 5 * 1024 * 1024
        for chunk in resp.iter_content(65536):
            content += chunk
            if len(content) > size_limit:
                logger.warning("og:image too large (>5MB), skipping: %s", url[:80])
                return None
        from io import BytesIO
        return Image.open(BytesIO(content)).convert("RGB")
    except Exception as exc:
        logger.warning("og:image fetch failed: %s", exc)
        return None


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
               max_width: int, max_lines: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        probe = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), probe, font=font)
        if bbox[2] <= max_width:
            current = probe
        else:
            if current:
                lines.append(current)
                if len(lines) >= max_lines:
                    break
            current = word
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines[:max_lines]


# ---------------------------------------------------------------------------
# Fallback card (always succeeds)
# ---------------------------------------------------------------------------

def _fallback_card(post_id: int, slide: dict) -> Path:
    path = _image_path(post_id, slide.get("slide_number", 0))
    img = Image.new("RGB", (CANVAS_W, CANVAS_H), color=(255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.text(
        (CANVAS_W // 2, CANVAS_H // 2),
        slide.get("title", "Image unavailable"),
        fill=(0, 0, 0),
        anchor="mm",
    )
    img.save(path, format="PNG")
    return path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_for_slide(
    post_id: int,
    slide: dict,
    brand_domain: str | None,
    og_image_url: str = "",
) -> Path:
    try:
        return _render_slide(post_id, slide, brand_domain, og_image_url)
    except Exception as exc:
        logger.error(
            "Pillow render failed for post %d slide %d: %s — using fallback card",
            post_id, slide.get("slide_number", 0), exc,
        )
        return _fallback_card(post_id, slide)


def _render_slide(
    post_id: int,
    slide: dict,
    brand_domain: str | None,
    og_image_url: str,
) -> Path:
    canvas = Image.new("RGB", (CANVAS_W, CANVAS_H), color=(255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # ── dark zone ──────────────────────────────────────────────────────────
    dark_height = FOOTER_START - DARK_START
    dark_zone = Image.new("RGB", (CANVAS_W, dark_height), color=DARK_BG)

    og_img = _fetch_og_image_data(og_image_url)
    if og_img:
        fitted = _fit_crop(og_img, CANVAS_W, dark_height)
        overlay = Image.new("RGBA", (CANVAS_W, dark_height), DARK_OVERLAY)
        fitted_rgba = fitted.convert("RGBA")
        composited = Image.alpha_composite(fitted_rgba, overlay).convert("RGB")
        dark_zone.paste(composited, (0, 0))

    canvas.paste(dark_zone, (0, DARK_START))

    # ── footer ─────────────────────────────────────────────────────────────
    footer_zone = Image.new("RGB", (CANVAS_W, CANVAS_H - FOOTER_START), color=DARK_BG)
    canvas.paste(footer_zone, (0, FOOTER_START))

    draw = ImageDraw.Draw(canvas)

    # ── lime separator ─────────────────────────────────────────────────────
    draw.rectangle(
        [(0, WHITE_HEIGHT), (CANVAS_W - 1, WHITE_HEIGHT + SEPARATOR_HEIGHT - 1)],
        fill=LIME,
    )

    # ── numbered badge ─────────────────────────────────────────────────────
    draw.ellipse(
        [(BADGE_X, BADGE_Y), (BADGE_X + BADGE_DIAMETER - 1, BADGE_Y + BADGE_DIAMETER - 1)],
        fill=LIME,
    )
    badge_font = _load_font(28)
    slide_num = str(slide.get("slide_number", 1))
    cx = BADGE_X + BADGE_DIAMETER // 2
    cy = BADGE_Y + BADGE_DIAMETER // 2
    draw.text((cx, cy), slide_num, fill=(0, 0, 0), font=badge_font, anchor="mm")

    # ── title ──────────────────────────────────────────────────────────────
    title_font = _load_font(TITLE_SIZE)
    title_lines = _wrap_text(draw, slide.get("title", ""), title_font, CANVAS_W - 2 * TEXT_MARGIN_X, 2)
    y = TITLE_Y
    for line in title_lines:
        draw.text((TEXT_MARGIN_X, y), line, fill=(0, 0, 0), font=title_font)
        bbox = draw.textbbox((0, 0), line, font=title_font)
        y += (bbox[3] - bbox[1]) + 8

    # ── subtitle ───────────────────────────────────────────────────────────
    y += 12
    sub_font = _load_font(SUBTITLE_SIZE)
    subtitle = slide.get("subtitle", "")
    if subtitle:
        sub_lines = _wrap_text(draw, subtitle, sub_font, CANVAS_W - 2 * TEXT_MARGIN_X, 1)
        for line in sub_lines:
            draw.text((TEXT_MARGIN_X, y), line, fill=(50, 50, 50), font=sub_font)
            bbox = draw.textbbox((0, 0), line, font=sub_font)
            y += (bbox[3] - bbox[1]) + 6

    # ── body ───────────────────────────────────────────────────────────────
    y += 10
    body_font = _load_font(BODY_SIZE)
    body = slide.get("body", "")
    if body:
        body_lines = _wrap_text(draw, body, body_font, CANVAS_W - 2 * TEXT_MARGIN_X, 3)
        for line in body_lines:
            draw.text((TEXT_MARGIN_X, y), line, fill=(80, 80, 80), font=body_font)
            bbox = draw.textbbox((0, 0), line, font=body_font)
            y += (bbox[3] - bbox[1]) + 4

    # ── footer hashtags ────────────────────────────────────────────────────
    footer_font = _load_font(28)
    hashtags = slide.get("hashtags", slide.get("footer", ""))
    if hashtags:
        draw.text(
            (TEXT_MARGIN_X, FOOTER_START + 30),
            hashtags,
            fill=FOOTER_COLOR,
            font=footer_font,
        )

    # ── save ───────────────────────────────────────────────────────────────
    dest = _image_path(post_id, slide["slide_number"])
    canvas.save(dest, format="PNG")

    # ── brand badge (top-right of full canvas, within white zone) ──────────
    if brand_domain:
        logo = fetch_logo(brand_domain)
        if logo:
            composite_badge(dest, logo)

    return dest


def generate_for_post(
    post_id: int,
    carousel: dict,
    brand_domain: str | None,
) -> list[Path]:
    og_image_url = carousel.get("og_image_url", "")
    paths = []
    for slide in carousel.get("slides", []):
        paths.append(generate_for_slide(post_id, slide, brand_domain, og_image_url=og_image_url))
    return paths
