import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_response(text: str) -> MagicMock:
    """Create a mock anthropic.messages.create response with .content[0].text."""
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


def test_save_reviewed_post_inserts_row(tmp_path):
    """save_reviewed_post inserts a new row with review fields populated."""
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    from src.db import save_reviewed_post
    ok = save_reviewed_post(
        article_hash="abc123",
        article_url="https://example.com",
        article_title="Test Article",
        carousel_json={"slides": []},
        review_score=8.5,
    )
    assert ok is True

    with db_module.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM generated_posts WHERE article_hash = 'abc123'"
        ).fetchone()
    assert row is not None
    assert row["review_score"] == 8.5
    assert row["reviewed"] == 1
    assert row["status"] == "pending_review"


def test_save_reviewed_post_upserts_on_duplicate(tmp_path):
    """save_reviewed_post updates review_score when article_hash already exists."""
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    from src.db import save_reviewed_post
    save_reviewed_post("dup", "https://x.com", "Title", {}, 7.0)
    ok = save_reviewed_post("dup", "https://x.com", "Title", {}, 9.0)
    assert ok is True

    with db_module.get_conn() as conn:
        row = conn.execute(
            "SELECT review_score FROM generated_posts WHERE article_hash = 'dup'"
        ).fetchone()
    assert row["review_score"] == 9.0


def test_run_review_returns_parsed_dict():
    """run_review calls messages.create and parses the JSON result."""
    review_json = '{"score": 8, "issues": ["weak CTA"], "suggestions": ["add urgency"]}'

    with patch("src.orchestrator._client") as mock_client:
        mock_client.messages.create.return_value = _make_response(review_json)

        from src.orchestrator import run_review
        post = {
            "article": {"title": "Test", "summary": "Summary"},
            "carousel": {"news_summary": "x", "total_slides": 8, "slides": []},
        }
        result = run_review(post)

    assert result["score"] == 8
    assert result["issues"] == ["weak CTA"]
    assert result["suggestions"] == ["add urgency"]


def test_run_review_returns_score5_on_bad_json():
    """run_review returns score=5 when the model returns non-JSON."""
    with patch("src.orchestrator._client") as mock_client:
        mock_client.messages.create.return_value = _make_response("not json at all")

        from src.orchestrator import run_review
        result = run_review({"article": {}, "carousel": {}})

    assert result["score"] == 5
    assert "issues" in result


def test_run_revise_returns_revised_carousel():
    """run_revise calls messages.create and returns a validated carousel dict."""
    revised = {
        "news_summary": "Revised summary",
        "total_slides": 8,
        "slides": [
            {
                "slide_number": i,
                "title": f"Slide {i}",
                "subtitle": "sub",
                "body": "body",
                "hashtags": "#tag",
                "image_prompt": "prompt",
            }
            for i in range(1, 9)
        ],
    }

    with patch("src.orchestrator._client") as mock_client:
        mock_client.messages.create.return_value = _make_response(json.dumps(revised))

        from src.orchestrator import run_revise
        result = run_revise(
            post={"article": {}, "carousel": {}},
            suggestions=["add urgency to slide 1"],
        )

    assert result["total_slides"] == 8
    assert len(result["slides"]) == 8


def test_run_revise_falls_back_to_original_on_invalid_output():
    """run_revise returns original carousel when model output fails validation."""
    original_carousel = {
        "news_summary": "original",
        "total_slides": 8,
        "slides": [
            {
                "slide_number": i,
                "title": "t",
                "subtitle": "s",
                "body": "b",
                "hashtags": "#h",
                "image_prompt": "p",
            }
            for i in range(1, 9)
        ],
    }

    with patch("src.orchestrator._client") as mock_client:
        mock_client.messages.create.return_value = _make_response("not json")

        from src.orchestrator import run_revise
        result = run_revise(
            post={"article": {}, "carousel": original_carousel},
            suggestions=["fix it"],
        )

    assert result == original_carousel


def test_run_pipeline_stops_early_when_no_articles(tmp_path, monkeypatch):
    """run_pipeline exits after scrape step when latest_articles.json is empty."""
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "latest_articles.json").write_text("[]")

    monkeypatch.chdir(tmp_path)

    with patch("src.orchestrator.cmd_scrape"), \
         patch("src.orchestrator.cmd_dedup"), \
         patch("src.orchestrator.cmd_generate"):

        from src.orchestrator import run_pipeline
        import io, sys
        captured = io.StringIO()
        sys.stdout = captured
        run_pipeline()
        sys.stdout = sys.__stdout__
        output = captured.getvalue()

    assert "STOP" in output
