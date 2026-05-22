import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_fetch_logo_downloads_and_caches(tmp_path):
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png

    with patch("src.brand._BRAND_ASSETS_DIR", tmp_path):
        with patch("src.brand.requests.get", return_value=mock_resp):
            from src.brand import fetch_logo
            result = fetch_logo("nvidia.com")

    assert result is not None
    assert result.exists()
    assert result.read_bytes() == fake_png


def test_fetch_logo_returns_cached_on_second_call(tmp_path):
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png

    with patch("src.brand._BRAND_ASSETS_DIR", tmp_path):
        with patch("src.brand.requests.get", return_value=mock_resp) as mock_get:
            from src.brand import fetch_logo
            fetch_logo("nvidia.com")
            fetch_logo("nvidia.com")

    assert mock_get.call_count == 1  # network called only once


def test_fetch_logo_returns_none_on_404(tmp_path):
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("src.brand._BRAND_ASSETS_DIR", tmp_path):
        with patch("src.brand.requests.get", return_value=mock_resp):
            from src.brand import fetch_logo
            result = fetch_logo("unknown-brand-xyz.com")

    assert result is None


def test_fetch_logo_refreshes_stale_cache(tmp_path):
    fake_png_old = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    fake_png_new = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

    cache_file = tmp_path / "nvidia.com.png"
    cache_file.write_bytes(fake_png_old)
    old_mtime = time.time() - (91 * 86400)
    os.utime(cache_file, (old_mtime, old_mtime))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png_new

    with patch("src.brand._BRAND_ASSETS_DIR", tmp_path):
        with patch("src.brand.requests.get", return_value=mock_resp):
            from src.brand import fetch_logo
            result = fetch_logo("nvidia.com")

    assert result is not None
    assert result.read_bytes() == fake_png_new


def test_enrich_prompt_returns_unchanged_for_none_domain():
    from src.brand import enrich_prompt
    base = "Futuristic data center, neon cyan on dark background, 4K"
    assert enrich_prompt(base, None) == base


def test_enrich_prompt_injects_known_brand_style():
    from src.brand import enrich_prompt
    result = enrich_prompt("Some image prompt here", "nvidia.com")
    assert "#76B900" in result
    assert "GPU" in result or "dark background" in result


def test_enrich_prompt_strips_cyberpunk_terms():
    from src.brand import enrich_prompt
    base = "Cyberpunk scene with neon lights, futuristic city, 4K resolution"
    result = enrich_prompt(base, "google.com")
    assert "cyberpunk" not in result.lower()
    assert "futuristic" not in result.lower()
    assert "neon" not in result.lower()


def test_enrich_prompt_uses_default_style_for_unknown_brand():
    from src.brand import enrich_prompt
    result = enrich_prompt("Some image prompt", "unknownbrand999.com")
    assert "corporate tech" in result or "professional" in result


def test_enrich_prompt_truncates_at_1000_chars():
    from src.brand import enrich_prompt
    long_prompt = "A " * 600  # 1200 chars
    result = enrich_prompt(long_prompt, "nvidia.com")
    assert len(result) <= 1000
    assert result.endswith("...")


def test_composite_badge_produces_file(tmp_path):
    from PIL import Image
    from src.brand import composite_badge

    base_path = tmp_path / "base.png"
    img = Image.new("RGB", (1080, 1350), color=(0, 0, 200))
    img.save(base_path)

    logo_path = tmp_path / "logo.png"
    logo = Image.new("RGBA", (100, 100), color=(255, 255, 255, 255))
    logo.save(logo_path)

    result = composite_badge(base_path, logo_path)
    assert result == base_path
    assert result.exists()


def test_composite_badge_places_badge_top_right(tmp_path):
    from PIL import Image
    from src.brand import composite_badge

    base_path = tmp_path / "base.png"
    img = Image.new("RGB", (1080, 1350), color=(0, 0, 0))
    img.save(base_path)

    logo_path = tmp_path / "logo.png"
    logo = Image.new("RGBA", (100, 100), color=(255, 0, 0, 255))
    logo.save(logo_path)

    composite_badge(base_path, logo_path)

    result_img = Image.open(base_path).convert("RGB")
    # Badge is 80x80, inset 20px from top-right
    # Center x = 1080 - 20 - 40 = 1020, top y = 20
    # Top of the white border ring should be white-ish
    badge_center_x = 1080 - 20 - 40
    badge_top_y = 20
    pixel = result_img.getpixel((badge_center_x, badge_top_y))
    assert pixel[0] > 200  # R channel white-ish (white border ring)
