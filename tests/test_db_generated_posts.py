import json
import pytest
from pathlib import Path

import src.db as db_module
db_module.DB_PATH = Path("/tmp/test_pipeline.db")

from src.db import init_db, save_generated_post, get_pending_posts


def setup_function():
    db_module.DB_PATH.unlink(missing_ok=True)
    init_db()


def test_save_and_retrieve_generated_post():
    carousel = {"news_summary": "AI is cool", "total_slides": 8, "slides": []}
    save_generated_post(
        article_hash="abc123",
        article_url="https://example.com/news",
        article_title="AI News",
        carousel_json=carousel,
    )
    rows = get_pending_posts()
    assert len(rows) == 1
    assert rows[0]["article_url"] == "https://example.com/news"
    assert rows[0]["status"] == "pending_review"
    assert json.loads(rows[0]["carousel_json"])["news_summary"] == "AI is cool"


def test_duplicate_hash_is_ignored():
    carousel = {"news_summary": "X", "total_slides": 8, "slides": []}
    save_generated_post("dupe", "https://a.com", "Title", carousel)
    save_generated_post("dupe", "https://a.com", "Title", carousel)
    rows = get_pending_posts()
    assert len(rows) == 1
