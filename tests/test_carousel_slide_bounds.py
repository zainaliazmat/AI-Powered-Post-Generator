import pytest


def test_validator_accepts_slide_count_within_range(tmp_db):
    from src.carousel_gen import ClaudeCarouselGenerator
    from src.settings import save_settings
    save_settings({"min_slides": 3, "max_slides": 5})
    gen = ClaudeCarouselGenerator()
    carousel = {
        "news_summary": "ok",
        "total_slides": 4,
        "slides": [{"slide_number": i + 1, "title": f"t{i}", "subtitle": "s",
                    "body": "b", "hashtags": "#x", "image_prompt": "p"} for i in range(4)],
    }
    gen._validate_carousel(carousel)  # should not raise


def test_validator_rejects_under_min(tmp_db):
    from src.carousel_gen import ClaudeCarouselGenerator
    from src.settings import save_settings
    save_settings({"min_slides": 3, "max_slides": 5})
    gen = ClaudeCarouselGenerator()
    carousel = {
        "news_summary": "ok",
        "total_slides": 2,
        "slides": [{"slide_number": i + 1, "title": f"t{i}", "subtitle": "s",
                    "body": "b", "hashtags": "#x", "image_prompt": "p"} for i in range(2)],
    }
    with pytest.raises(ValueError) as exc:
        gen._validate_carousel(carousel)
    assert "slides" in str(exc.value)


def test_validator_rejects_over_max(tmp_db):
    from src.carousel_gen import ClaudeCarouselGenerator
    from src.settings import save_settings
    save_settings({"min_slides": 3, "max_slides": 5})
    gen = ClaudeCarouselGenerator()
    carousel = {
        "news_summary": "ok",
        "total_slides": 7,
        "slides": [{"slide_number": i + 1, "title": f"t{i}", "subtitle": "s",
                    "body": "b", "hashtags": "#x", "image_prompt": "p"} for i in range(7)],
    }
    with pytest.raises(ValueError) as exc:
        gen._validate_carousel(carousel)
    assert "slides" in str(exc.value)


def test_prompt_includes_current_min_max(tmp_db):
    """The constructed user prompt must mention the active min/max values."""
    from src.carousel_gen import ClaudeCarouselGenerator
    from src.settings import save_settings
    save_settings({"min_slides": 2, "max_slides": 9})
    gen = ClaudeCarouselGenerator()
    prompt = gen._build_user_prompt({
        "title": "t", "source": "s", "summary": "x", "url": "u",
    })
    assert "2" in prompt and "9" in prompt
