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
    "image_prompt": "Futuristic GPU render, cinematic lighting",
    "footer": "#AI #Tech",
}

FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200


# ---------------------------------------------------------------------------
# generate_for_slide
# ---------------------------------------------------------------------------

def test_generate_for_slide_saves_file_on_success(tmp_path):
    with patch("src.ImageGen.image_gen_flux._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_flux.replicate.run", return_value=["https://r.example.com/out.png"]), \
         patch("src.ImageGen.image_gen_flux.urllib.request.urlretrieve",
               side_effect=lambda url, dest: Path(dest).write_bytes(FAKE_PNG)), \
         patch("src.ImageGen.image_gen_flux.fetch_logo", return_value=None):

        from src.ImageGen.image_gen_flux import generate_for_slide
        result = generate_for_slide(1, SAMPLE_SLIDE, "nvidia.com")

    assert result.exists()
    assert result.name == "1_1.png"


def test_generate_for_slide_composites_badge_when_logo_found(tmp_path):
    logo_path = tmp_path / "nvidia.com.png"
    logo_path.write_bytes(FAKE_PNG)

    with patch("src.ImageGen.image_gen_flux._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_flux.replicate.run", return_value=["https://r.example.com/out.png"]), \
         patch("src.ImageGen.image_gen_flux.urllib.request.urlretrieve",
               side_effect=lambda url, dest: Path(dest).write_bytes(FAKE_PNG)), \
         patch("src.ImageGen.image_gen_flux.fetch_logo", return_value=logo_path), \
         patch("src.ImageGen.image_gen_flux.composite_badge") as mock_badge:

        from src.ImageGen.image_gen_flux import generate_for_slide
        generate_for_slide(1, SAMPLE_SLIDE, "nvidia.com")

    mock_badge.assert_called_once()


def test_generate_for_slide_falls_back_to_text_card_on_replicate_failure(tmp_path):
    with patch("src.ImageGen.image_gen_flux._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_flux.replicate.run", side_effect=Exception("Replicate down")):

        from src.ImageGen.image_gen_flux import generate_for_slide
        result = generate_for_slide(1, SAMPLE_SLIDE, None)

    assert result.exists()
    img = Image.open(result)
    assert img.size == (1080, 1350)


def test_generate_for_slide_ignores_og_image_url(tmp_path):
    """og_image_url param is accepted but never used by the Flux renderer."""
    with patch("src.ImageGen.image_gen_flux._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_flux.replicate.run", return_value=["https://r.example.com/out.png"]), \
         patch("src.ImageGen.image_gen_flux.urllib.request.urlretrieve",
               side_effect=lambda url, dest: Path(dest).write_bytes(FAKE_PNG)), \
         patch("src.ImageGen.image_gen_flux.fetch_logo", return_value=None):

        from src.ImageGen.image_gen_flux import generate_for_slide
        # Passing og_image_url must not raise and must behave identically to omitting it
        result = generate_for_slide(1, SAMPLE_SLIDE, None, og_image_url="https://example.com/img.jpg")

    assert result.exists()


# ---------------------------------------------------------------------------
# generate_for_post
# ---------------------------------------------------------------------------

def test_generate_for_post_returns_one_path_per_slide(tmp_path):
    carousel = {
        "slides": [
            {**SAMPLE_SLIDE, "slide_number": 1},
            {**SAMPLE_SLIDE, "slide_number": 2},
        ]
    }
    with patch("src.ImageGen.image_gen_flux._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_flux.replicate.run", return_value=["https://r.example.com/out.png"]), \
         patch("src.ImageGen.image_gen_flux.urllib.request.urlretrieve",
               side_effect=lambda url, dest: Path(dest).write_bytes(FAKE_PNG)), \
         patch("src.ImageGen.image_gen_flux.fetch_logo", return_value=None), \
         patch("src.ImageGen.image_gen_flux.time.sleep"):

        from src.ImageGen.image_gen_flux import generate_for_post
        paths = generate_for_post(42, carousel, None)

    assert len(paths) == 2
    assert paths[0].name == "42_1.png"
    assert paths[1].name == "42_2.png"


def test_generate_for_post_sleeps_between_slides_not_before_first(tmp_path):
    carousel = {
        "slides": [
            {**SAMPLE_SLIDE, "slide_number": 1},
            {**SAMPLE_SLIDE, "slide_number": 2},
            {**SAMPLE_SLIDE, "slide_number": 3},
        ]
    }
    with patch("src.ImageGen.image_gen_flux._IMAGES_DIR", tmp_path), \
         patch("src.ImageGen.image_gen_flux.replicate.run", return_value=["https://r.example.com/out.png"]), \
         patch("src.ImageGen.image_gen_flux.urllib.request.urlretrieve",
               side_effect=lambda url, dest: Path(dest).write_bytes(FAKE_PNG)), \
         patch("src.ImageGen.image_gen_flux.fetch_logo", return_value=None), \
         patch("src.ImageGen.image_gen_flux.time.sleep") as mock_sleep:

        from src.ImageGen.image_gen_flux import generate_for_post
        generate_for_post(1, carousel, None)

    # 3 slides → sleep called 2 times (between slide 1→2 and 2→3, NOT before slide 1)
    assert mock_sleep.call_count == 2
