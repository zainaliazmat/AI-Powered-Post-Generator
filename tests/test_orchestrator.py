import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest


def test_save_reviewed_post_inserts_row(monkeypatch, tmp_path):
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


def test_save_reviewed_post_skips_duplicate(monkeypatch, tmp_path):
    """save_reviewed_post returns False when article_hash already exists."""
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    from src.db import save_reviewed_post
    save_reviewed_post("dup", "https://x.com", "Title", {}, 7.0)
    ok = save_reviewed_post("dup", "https://x.com", "Title", {}, 7.0)
    assert ok is False


async def _async_gen(*items):
    for item in items:
        yield item


def test_run_review_returns_parsed_dict():
    """run_review spawns a query() call and parses the JSON result."""
    review_json = '{"score": 8, "issues": ["weak CTA"], "suggestions": ["add urgency"]}'

    # Use a real class as the ResultMessage sentinel so isinstance() works correctly.
    class _FakeResultMessage:
        def __init__(self, text):
            self.subtype = "success"
            self.result = text

    fake_msg = _FakeResultMessage(review_json)

    with patch("src.orchestrator.query") as mock_query, \
         patch("src.orchestrator.ResultMessage", _FakeResultMessage):

        mock_query.return_value = _async_gen(fake_msg)

        from src.orchestrator import run_review
        post = {
            "article": {"title": "Test", "summary": "Summary"},
            "carousel": {"news_summary": "x", "total_slides": 8, "slides": []},
        }
        result = asyncio.run(run_review(post))

    assert result["score"] == 8
    assert result["issues"] == ["weak CTA"]
    assert result["suggestions"] == ["add urgency"]


def test_run_review_returns_score5_on_bad_json():
    """run_review returns score=5 when the model returns non-JSON."""
    class _FakeResultMessage:
        def __init__(self, text):
            self.subtype = "success"
            self.result = text

    fake_msg = _FakeResultMessage("not json at all")

    with patch("src.orchestrator.query") as mock_query, \
         patch("src.orchestrator.ResultMessage", _FakeResultMessage):

        mock_query.return_value = _async_gen(fake_msg)

        from src.orchestrator import run_review
        result = asyncio.run(run_review({"article": {}, "carousel": {}}))

    assert result["score"] == 5
    assert "issues" in result
