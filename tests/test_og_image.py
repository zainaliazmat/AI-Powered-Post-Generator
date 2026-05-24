from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_html_response(html: str):
    resp = MagicMock()
    resp.status_code = 200
    resp.text = html
    return resp


# ---------------------------------------------------------------------------
# Basic behaviour
# ---------------------------------------------------------------------------

def test_fetch_og_image_returns_og_image_url():
    html = (
        '<html><head>'
        '<meta property="og:image" content="https://example.com/img.png"/>'
        '</head></html>'
    )
    with patch("src.shared.requests.get", return_value=_mock_html_response(html)):
        from src.shared import fetch_og_image
        assert fetch_og_image("https://example.com/article") == "https://example.com/img.png"


def test_fetch_og_image_falls_back_to_twitter_image():
    html = (
        '<html><head>'
        '<meta name="twitter:image" content="https://example.com/tw.png"/>'
        '</head></html>'
    )
    with patch("src.shared.requests.get", return_value=_mock_html_response(html)):
        from src.shared import fetch_og_image
        assert fetch_og_image("https://example.com/article") == "https://example.com/tw.png"


def test_fetch_og_image_prefers_og_over_twitter():
    html = (
        '<html><head>'
        '<meta property="og:image" content="https://example.com/og.png"/>'
        '<meta name="twitter:image" content="https://example.com/tw.png"/>'
        '</head></html>'
    )
    with patch("src.shared.requests.get", return_value=_mock_html_response(html)):
        from src.shared import fetch_og_image
        assert fetch_og_image("https://example.com/article") == "https://example.com/og.png"


def test_fetch_og_image_returns_empty_when_no_tag():
    html = "<html><head><title>No image here</title></head></html>"
    with patch("src.shared.requests.get", return_value=_mock_html_response(html)):
        from src.shared import fetch_og_image
        assert fetch_og_image("https://example.com/article") == ""


def test_fetch_og_image_returns_empty_on_network_error():
    with patch("src.shared.requests.get", side_effect=Exception("Connection refused")):
        from src.shared import fetch_og_image
        assert fetch_og_image("https://example.com/article") == ""


def test_fetch_og_image_returns_empty_on_non_200():
    resp = MagicMock()
    resp.status_code = 404
    with patch("src.shared.requests.get", return_value=resp):
        from src.shared import fetch_og_image
        assert fetch_og_image("https://example.com/article") == ""


# ---------------------------------------------------------------------------
# SSRF guards — no network call should ever be made for these
# ---------------------------------------------------------------------------

def test_fetch_og_image_rejects_file_scheme():
    with patch("src.shared.requests.get") as mock_get:
        from src.shared import fetch_og_image
        result = fetch_og_image("file:///etc/passwd")
    assert result == ""
    mock_get.assert_not_called()


def test_fetch_og_image_rejects_ftp_scheme():
    with patch("src.shared.requests.get") as mock_get:
        from src.shared import fetch_og_image
        result = fetch_og_image("ftp://example.com/file")
    assert result == ""
    mock_get.assert_not_called()


def test_fetch_og_image_rejects_localhost():
    with patch("src.shared.requests.get") as mock_get:
        from src.shared import fetch_og_image
        result = fetch_og_image("http://localhost/internal")
    assert result == ""
    mock_get.assert_not_called()


def test_fetch_og_image_rejects_loopback_ip():
    with patch("src.shared.requests.get") as mock_get:
        from src.shared import fetch_og_image
        result = fetch_og_image("http://127.0.0.1/internal")
    assert result == ""
    mock_get.assert_not_called()


def test_fetch_og_image_rejects_aws_metadata_ip():
    with patch("src.shared.requests.get") as mock_get:
        from src.shared import fetch_og_image
        result = fetch_og_image("http://169.254.169.254/latest/meta-data/")
    assert result == ""
    mock_get.assert_not_called()


def test_fetch_og_image_rejects_dotlocal():
    with patch("src.shared.requests.get") as mock_get:
        from src.shared import fetch_og_image
        result = fetch_og_image("http://myservice.local/data")
    assert result == ""
    mock_get.assert_not_called()


def test_fetch_og_image_rejects_zero_ip():
    with patch("src.shared.requests.get") as mock_get:
        from src.shared import fetch_og_image
        result = fetch_og_image("http://0.0.0.0/admin")
    assert result == ""
    mock_get.assert_not_called()


def test_fetch_og_image_allows_normal_https_url():
    """Verify safe URL does reach the network (regression guard)."""
    html = '<meta property="og:image" content="https://cdn.example.com/img.png"/>'
    with patch("src.shared.requests.get", return_value=_mock_html_response(html)) as mock_get:
        from src.shared import fetch_og_image
        fetch_og_image("https://techcrunch.com/article")
    mock_get.assert_called_once()
