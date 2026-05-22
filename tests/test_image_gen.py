import json
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
    "image_prompt": "Futuristic cyberpunk GPU, neon cyan, 4K resolution",
}


def test_pillow_text_card_creates_image(tmp_path):
    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        from src.image_gen import _pillow_text_card
        result = _pillow_text_card(post_id=1, slide=SAMPLE_SLIDE)

    assert result.exists()
    img = Image.open(result)
    assert img.size == (1080, 1350)


def test_pillow_text_card_uses_slide_title(tmp_path):
    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        from src.image_gen import _pillow_text_card
        result = _pillow_text_card(post_id=1, slide=SAMPLE_SLIDE)

    assert result.stat().st_size > 0


def test_generate_for_slide_calls_flux_and_returns_path(tmp_path):
    fake_image_url = "https://replicate.example.com/output.png"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        with patch("src.image_gen.replicate.run", return_value=[fake_image_url]):
            with patch("src.image_gen.urllib.request.urlretrieve",
                       side_effect=lambda url, dest: Path(dest).write_bytes(fake_png)):
                with patch("src.image_gen.fetch_logo", return_value=None):
                    from src.image_gen import generate_for_slide
                    result = generate_for_slide(
                        post_id=1,
                        slide=SAMPLE_SLIDE,
                        brand_domain="nvidia.com",
                    )

    assert result.exists()
    assert result.name == "1_1.png"


def test_generate_for_slide_composites_badge_when_logo_found(tmp_path):
    fake_image_url = "https://replicate.example.com/output.png"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    fake_logo = tmp_path / "nvidia.com.png"
    fake_logo.write_bytes(fake_png)

    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        with patch("src.image_gen.replicate.run", return_value=[fake_image_url]):
            with patch("src.image_gen.urllib.request.urlretrieve",
                       side_effect=lambda url, dest: Path(dest).write_bytes(fake_png)):
                with patch("src.image_gen.fetch_logo", return_value=fake_logo):
                    with patch("src.image_gen.composite_badge") as mock_badge:
                        from src.image_gen import generate_for_slide
                        generate_for_slide(
                            post_id=1,
                            slide=SAMPLE_SLIDE,
                            brand_domain="nvidia.com",
                        )

    mock_badge.assert_called_once()


def test_generate_for_slide_falls_back_to_text_card_on_flux_failure(tmp_path):
    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        with patch("src.image_gen.replicate.run", side_effect=Exception("Flux API down")):
            from src.image_gen import generate_for_slide
            result = generate_for_slide(
                post_id=1,
                slide=SAMPLE_SLIDE,
                brand_domain="nvidia.com",
            )

    assert result.exists()
    img = Image.open(result)
    assert img.size == (1080, 1350)


def test_generate_for_post_returns_path_per_slide(tmp_path):
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    carousel = {
        "brand_domain": "nvidia.com",
        "total_slides": 2,
        "slides": [
            {**SAMPLE_SLIDE, "slide_number": 1},
            {**SAMPLE_SLIDE, "slide_number": 2, "title": "Slide 2"},
        ],
    }

    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        with patch("src.image_gen.replicate.run", return_value=["https://example.com/img.png"]):
            with patch("src.image_gen.urllib.request.urlretrieve",
                       side_effect=lambda url, dest: Path(dest).write_bytes(fake_png)):
                with patch("src.image_gen.fetch_logo", return_value=None):
                    from src.image_gen import generate_for_post
                    paths = generate_for_post(
                        post_id=42,
                        carousel=carousel,
                        brand_domain="nvidia.com",
                    )

    assert len(paths) == 2
    assert paths[0].name == "42_1.png"
    assert paths[1].name == "42_2.png"
