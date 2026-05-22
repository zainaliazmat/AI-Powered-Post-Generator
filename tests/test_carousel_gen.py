import json
import pytest
from unittest.mock import MagicMock, patch
from src.carousel_gen import ClaudeCarouselGenerator


SAMPLE_ARTICLE = {
    "title": "NVIDIA gives free API access to 80+ AI models",
    "url": "https://nvidia.com/news/api",
    "summary": "NVIDIA is providing free API access to 80+ premium AI models.",
    "date": "2026-05-21T10:00:00+00:00",
    "source": "nvidia",
    "author": "",
    "categories": ["AI", "Developer Tools"],
}

SAMPLE_CAROUSEL = {
    "news_summary": "NVIDIA opens free access to 80+ AI models for developers.",
    "total_slides": 5,
    "slides": [
        {
            "slide_number": i,
            "title": f"Slide {i} title",
            "subtitle": f"Subtitle {i}",
            "body": f"Body text for slide {i}.",
            "hashtags": "#AI #NVIDIA #Tech",
            "image_prompt": "Futuristic data center, neon cyan on dark background, 4K",
        }
        for i in range(1, 6)
    ],
}

SAMPLE_CAROUSEL_WITH_BRAND = {
    "brand_domain": "nvidia.com",
    "news_summary": "NVIDIA opens free access to 80+ AI models for developers.",
    "total_slides": 5,
    "slides": [
        {
            "slide_number": i,
            "title": f"Slide {i} title",
            "subtitle": f"Subtitle {i}",
            "body": f"Body text for slide {i}.",
            "hashtags": "#AI #NVIDIA #Tech",
            "image_prompt": "Futuristic data center, neon cyan on dark background, 4K",
        }
        for i in range(1, 6)
    ],
}

SAMPLE_CAROUSEL_NO_BRAND = {
    "brand_domain": None,
    "news_summary": "NVIDIA opens free access to 80+ AI models for developers.",
    "total_slides": 5,
    "slides": [
        {
            "slide_number": i,
            "title": f"Slide {i} title",
            "subtitle": f"Subtitle {i}",
            "body": f"Body text for slide {i}.",
            "hashtags": "#AI #NVIDIA #Tech",
            "image_prompt": "Futuristic data center, neon cyan on dark background, 4K",
        }
        for i in range(1, 6)
    ],
}


def make_mock_litellm_response(content: str) -> MagicMock:
    """Build a MagicMock that looks like a litellm.completion() response."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.usage.prompt_tokens = 1000
    resp.usage.completion_tokens = 2000
    return resp


def test_generate_carousel_returns_valid_structure(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    with patch("src.carousel_gen.litellm.completion",
               return_value=make_mock_litellm_response(json.dumps(SAMPLE_CAROUSEL))):
        result = gen.generate_carousel(SAMPLE_ARTICLE)
    assert "news_summary" in result
    assert "slides" in result
    assert len(result["slides"]) == result["total_slides"]
    assert result["slides"][0]["slide_number"] == 1


def test_generate_carousel_uses_cache(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    with patch("src.carousel_gen.litellm.completion",
               return_value=make_mock_litellm_response(json.dumps(SAMPLE_CAROUSEL))) as mock_api:
        gen.generate_carousel(SAMPLE_ARTICLE)
        gen.generate_carousel(SAMPLE_ARTICLE)
    assert mock_api.call_count == 1


def test_generate_carousel_force_refresh(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    with patch("src.carousel_gen.litellm.completion",
               return_value=make_mock_litellm_response(json.dumps(SAMPLE_CAROUSEL))) as mock_api:
        gen.generate_carousel(SAMPLE_ARTICLE)
        gen.generate_carousel(SAMPLE_ARTICLE, force_refresh=True)
    assert mock_api.call_count == 2


def test_extract_json_strips_code_fences(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    wrapped = f"```json\n{json.dumps(SAMPLE_CAROUSEL)}\n```"
    result = gen._extract_json(wrapped)
    assert result["total_slides"] == 5


def test_usage_stats_track_tokens(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    with patch("src.carousel_gen.litellm.completion",
               return_value=make_mock_litellm_response(json.dumps(SAMPLE_CAROUSEL))):
        gen.generate_carousel(SAMPLE_ARTICLE)
    stats = gen.get_usage_report()
    assert stats["total_input_tokens"] == 1000
    assert stats["total_output_tokens"] == 2000
    assert stats["estimated_cost_usd"] >= 0


def test_validate_raises_on_missing_slide_field(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    slides_with_bad_slide = [
        {**SAMPLE_CAROUSEL["slides"][0], **{"slide_number": i}} for i in range(1, 6)
    ]
    slides_with_bad_slide[3] = {"slide_number": 4}
    bad_carousel = {**SAMPLE_CAROUSEL, "slides": slides_with_bad_slide}
    with pytest.raises(ValueError, match="missing field"):
        gen._validate_carousel(bad_carousel)


def test_low_confidence_flagged_for_empty_summary(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    article = {**SAMPLE_ARTICLE, "summary": "Comments"}
    with patch("src.carousel_gen.litellm.completion",
               return_value=make_mock_litellm_response(json.dumps(SAMPLE_CAROUSEL))):
        result = gen.generate_carousel(article)
    assert result.get("low_confidence") is True


def test_build_prompt_handles_braces_in_title(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    article = {**SAMPLE_ARTICLE, "title": "Meta's {Llama 4} model drops today"}
    prompt = gen._build_prompt(article)
    assert "Llama 4" in prompt


def test_generate_carousel_includes_brand_domain(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    with patch("src.carousel_gen.litellm.completion",
               return_value=make_mock_litellm_response(json.dumps(SAMPLE_CAROUSEL_WITH_BRAND))):
        result = gen.generate_carousel(SAMPLE_ARTICLE)
    assert "brand_domain" in result
    assert result["brand_domain"] == "nvidia.com"


def test_generate_carousel_brand_domain_can_be_null(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    with patch("src.carousel_gen.litellm.completion",
               return_value=make_mock_litellm_response(json.dumps(SAMPLE_CAROUSEL_NO_BRAND))):
        result = gen.generate_carousel(SAMPLE_ARTICLE)
    assert result.get("brand_domain") is None
