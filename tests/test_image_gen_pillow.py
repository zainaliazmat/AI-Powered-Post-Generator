from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


SAMPLE_SLIDE = {
    "slide_number": 1,
    "title": "NVIDIA drops RTX 50 series",
    "subtitle": "Next-gen GPUs unveiled",
    "body": "NVIDIA announced RTX 50 series with major performance gains.",
    "hashtags": "#NVIDIA #GPU",
    "image_prompt": "Futuristic GPU render",
    "footer": "#AI #Tech",
}


# ---------------------------------------------------------------------------
# Canvas geometry
# ---------------------------------------------------------------------------

def test_generate_for_slide_produces_1080x1350_canvas(tmp_path):
    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data", return_value=None):

        from src.ImageGen.image_gen_pillow import generate_for_slide
        result = generate_for_slide(1, SAMPLE_SLIDE, None, og_image_url="")

    assert result.exists()
    img = Image.open(result)
    assert img.size == (1080, 1350)


# ---------------------------------------------------------------------------
# Zone colours
# ---------------------------------------------------------------------------

def test_white_zone_pixel_is_white(tmp_path):
    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data", return_value=None):

        from src.ImageGen.image_gen_pillow import generate_for_slide
        result = generate_for_slide(1, SAMPLE_SLIDE, None, og_image_url="")

    img = Image.open(result).convert("RGB")
    # Sample well within the white zone (y=200, x=540)
    r, g, b = img.getpixel((540, 200))
    assert r > 230 and g > 230 and b > 230


def test_dark_zone_pixel_is_dark(tmp_path):
    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data", return_value=None):

        from src.ImageGen.image_gen_pillow import generate_for_slide
        result = generate_for_slide(1, SAMPLE_SLIDE, None, og_image_url="")

    img = Image.open(result).convert("RGB")
    # Sample well within the dark zone (y=800, x=540)
    r, g, b = img.getpixel((540, 800))
    assert r < 50 and g < 50 and b < 50


def test_lime_separator_present_at_zone_boundary(tmp_path):
    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data", return_value=None):

        from src.ImageGen.image_gen_pillow import generate_for_slide, WHITE_HEIGHT
        result = generate_for_slide(1, SAMPLE_SLIDE, None, og_image_url="")

    img = Image.open(result).convert("RGB")
    # Lime separator (#CCFF00 = 204, 255, 0) sits at y=WHITE_HEIGHT
    r, g, b = img.getpixel((540, WHITE_HEIGHT))
    assert r > 180 and g > 230 and b < 20


# ---------------------------------------------------------------------------
# Numbered badge
# ---------------------------------------------------------------------------

def test_numbered_badge_is_lime_at_top_left(tmp_path):
    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data", return_value=None):

        from src.ImageGen.image_gen_pillow import generate_for_slide, BADGE_X, BADGE_Y, BADGE_DIAMETER
        result = generate_for_slide(1, SAMPLE_SLIDE, None, og_image_url="")

    img = Image.open(result).convert("RGB")
    # Sample the top-center of the lime badge (avoids the black slide-number text at center)
    cx = BADGE_X + BADGE_DIAMETER // 2
    top_edge_y = BADGE_Y + 6  # 6px inside top edge — lime, no text
    r, g, b = img.getpixel((cx, top_edge_y))
    assert r > 180 and g > 230 and b < 20  # lime


# ---------------------------------------------------------------------------
# og:image dark zone fill
# ---------------------------------------------------------------------------

def test_og_image_fills_dark_zone(tmp_path):
    fake_og = Image.new("RGB", (800, 600), color=(220, 30, 30))  # bright red

    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data", return_value=fake_og):

        from src.ImageGen.image_gen_pillow import generate_for_slide
        result = generate_for_slide(1, SAMPLE_SLIDE, None, og_image_url="https://example.com/img.jpg")

    img = Image.open(result).convert("RGB")
    # Dark zone y=800; red channel should dominate after dark overlay
    r, g, b = img.getpixel((540, 800))
    assert r > g and r > b


# ---------------------------------------------------------------------------
# Brand badge
# ---------------------------------------------------------------------------

def test_brand_badge_called_when_logo_found(tmp_path):
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    logo_path = tmp_path / "nvidia.com.png"
    logo_path.write_bytes(fake_png)

    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data", return_value=None), \
         patch("src.ImageGen.image_gen_pillow.fetch_logo", return_value=logo_path), \
         patch("src.ImageGen.image_gen_pillow.composite_badge") as mock_badge:

        from src.ImageGen.image_gen_pillow import generate_for_slide
        generate_for_slide(1, SAMPLE_SLIDE, "nvidia.com", og_image_url="")

    mock_badge.assert_called_once()


def test_brand_badge_not_called_when_logo_missing(tmp_path):
    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data", return_value=None), \
         patch("src.ImageGen.image_gen_pillow.fetch_logo", return_value=None), \
         patch("src.ImageGen.image_gen_pillow.composite_badge") as mock_badge:

        from src.ImageGen.image_gen_pillow import generate_for_slide
        generate_for_slide(1, SAMPLE_SLIDE, "nvidia.com", og_image_url="")

    mock_badge.assert_not_called()


# ---------------------------------------------------------------------------
# generate_for_post
# ---------------------------------------------------------------------------

def test_generate_for_post_returns_one_path_per_slide(tmp_path):
    carousel = {
        "slides": [
            {**SAMPLE_SLIDE, "slide_number": 1},
            {**SAMPLE_SLIDE, "slide_number": 2},
            {**SAMPLE_SLIDE, "slide_number": 3},
        ],
        "og_image_url": "",
    }
    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data", return_value=None):

        from src.ImageGen.image_gen_pillow import generate_for_post
        paths = generate_for_post(1, carousel, None)

    assert len(paths) == 3
    assert paths[0].name == "1_1.png"
    assert paths[2].name == "1_3.png"


# ---------------------------------------------------------------------------
# Fallback card
# ---------------------------------------------------------------------------

def test_fallback_card_produced_on_render_error(tmp_path):
    """Any exception during slide render must produce a valid 1080×1350 PNG."""
    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data",
               side_effect=Exception("network timeout")):

        from src.ImageGen.image_gen_pillow import generate_for_slide
        result = generate_for_slide(1, SAMPLE_SLIDE, None, og_image_url="https://example.com/img.jpg")

    assert result.exists()
    img = Image.open(result)
    assert img.size == (1080, 1350)


def test_fallback_card_format_matches_normal_output(tmp_path):
    """Fallback card is a valid PNG, same dimensions as a normal render."""
    with patch("src.ImageGen.image_gen_pillow._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_pillow._fetch_og_image_data", return_value=None):

        from src.ImageGen.image_gen_pillow import generate_for_slide, _fallback_card
        normal = generate_for_slide(1, SAMPLE_SLIDE, None)
        fallback = _fallback_card(post_id=2, slide={**SAMPLE_SLIDE, "slide_number": 1})

    normal_img = Image.open(normal)
    fallback_img = Image.open(fallback)
    assert normal_img.size == fallback_img.size
    assert normal_img.format == fallback_img.format or fallback_img.format == "PNG"


# ---------------------------------------------------------------------------
# generate_for_post_with_events
# ---------------------------------------------------------------------------

def test_generate_for_post_with_events_returns_tuple(tmp_path, monkeypatch):
    from src.ImageGen import image_gen_pillow as ig
    monkeypatch.setattr(ig, "_IMAGES_DIR", tmp_path)

    carousel = {
        "og_image_url": "",
        "slides": [
            {"slide_number": 1, "title": "A", "subtitle": "s", "body": "b",
             "hashtags": "#x", "image_prompt": "p1"},
            {"slide_number": 2, "title": "B", "subtitle": "s", "body": "b",
             "hashtags": "#x", "image_prompt": "p2"},
        ],
    }
    paths, events = ig.generate_for_post_with_events(42, carousel, brand_domain=None)
    assert len(paths) == 2
    assert len(events) == 2
    assert events[0] == {
        "idx": 1, "prompt": "p1", "enriched_prompt": None,
        "path": str(paths[0]), "status": "ok",
    }
    assert events[1]["idx"] == 2
    assert events[1]["status"] == "ok"


def test_pillow_event_status_is_fallback_when_render_raises(tmp_path, monkeypatch):
    from src.ImageGen import image_gen_pillow as ig
    monkeypatch.setattr(ig, "_IMAGES_DIR", tmp_path)
    monkeypatch.setattr(ig, "_render_slide", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")))

    carousel = {"og_image_url": "", "slides": [
        {"slide_number": 1, "title": "A", "image_prompt": "p1"},
    ]}
    paths, events = ig.generate_for_post_with_events(99, carousel, brand_domain=None)
    assert events[0]["status"] == "fallback"
    assert paths[0].exists()
