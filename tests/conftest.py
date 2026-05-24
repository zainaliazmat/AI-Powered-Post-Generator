import os

import pytest

# Set before any src imports so require_auth reads correct values
os.environ.setdefault("DASHBOARD_PASSWORD", "testpass")
os.environ.setdefault("DASHBOARD_USERNAME", "admin")


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a throwaway temp file and initialize schema."""
    import src.db as db_module

    db_path = tmp_path / "test_pipeline.db"
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    db_module.init_db()
    yield db_path


@pytest.fixture()
def client(tmp_db):
    """FastAPI TestClient with a clean test database."""
    from fastapi.testclient import TestClient

    from src.main import app

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def seed_post(tmp_db):
    """Insert one pending_review post; return its integer id."""
    import src.db as db

    db.save_reviewed_post(
        article_hash="abc123",
        article_url="https://techcrunch.com/test-article",
        article_title="Test Article: AI Breakthrough Changes Everything",
        carousel_json={
            "slides": [{"caption": "OpenAI just dropped something massive 🚀"}],
            "brand_domain": "techcrunch.com",
        },
        review_score=7.8,
    )
    return db.get_pending_posts()[0]["id"]


@pytest.fixture()
def seed_source(tmp_db):
    """Insert one source row; return its integer id."""
    import src.db as db_module

    db_module.save_source(
        "techcrunch",
        "https://techcrunch.com",
        {"method": "rss", "feed_url": "https://techcrunch.com/feed"},
    )
    with db_module.get_conn() as conn:
        row = conn.execute("SELECT id FROM sources WHERE key = 'techcrunch'").fetchone()
    return row["id"]
