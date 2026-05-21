import json
import sqlite3
import tempfile
from pathlib import Path
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
