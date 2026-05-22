import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_litellm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


SAMPLE_CAROUSEL = {
    "news_summary": "Test summary",
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


# ---------- DB tests (unchanged) ----------

def test_save_reviewed_post_inserts_row(tmp_path):
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


def test_save_image_paths_updates_row(tmp_path):
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    from src.db import save_reviewed_post, save_image_paths
    save_reviewed_post("img_hash", "https://x.com", "Title", {}, 8.0)

    with db_module.get_conn() as conn:
        post_id = conn.execute(
            "SELECT id FROM generated_posts WHERE article_hash = 'img_hash'"
        ).fetchone()["id"]

    ok = save_image_paths(post_id, ["/data/images/1_1.png", "/data/images/1_2.png"])
    assert ok is True

    with db_module.get_conn() as conn:
        row = conn.execute(
            "SELECT image_paths, status FROM generated_posts WHERE id = ?", (post_id,)
        ).fetchone()
    import json
    assert json.loads(row["image_paths"]) == ["/data/images/1_1.png", "/data/images/1_2.png"]
    assert row["status"] == "image_ready"


# ---------- run_review tests ----------

def test_run_review_returns_parsed_dict():
    review_json = '{"score": 8, "issues": ["weak CTA"], "suggestions": ["add urgency"]}'
    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response(review_json)):
        from src.orchestrator import run_review
        result = run_review({
            "article": {"title": "Test", "summary": "Summary"},
            "carousel": {"news_summary": "x", "total_slides": 8, "slides": []},
        })
    assert result["score"] == 8
    assert result["issues"] == ["weak CTA"]
    assert result["suggestions"] == ["add urgency"]


def test_run_review_returns_score5_on_bad_json():
    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response("not json at all")):
        from src.orchestrator import run_review
        result = run_review({"article": {}, "carousel": {}})
    assert result["score"] == 5
    assert "issues" in result


# ---------- run_revise tests ----------

def test_run_revise_returns_revised_carousel():
    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response(json.dumps(SAMPLE_CAROUSEL))):
        from src.orchestrator import run_revise
        result = run_revise(
            post={"article": {}, "carousel": SAMPLE_CAROUSEL},
            suggestions=["add urgency to slide 1"],
        )
    assert result["total_slides"] == 8
    assert len(result["slides"]) == 8


def test_run_revise_falls_back_to_original_on_invalid_output():
    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response("not json")):
        from src.orchestrator import run_revise
        result = run_revise(
            post={"article": {}, "carousel": SAMPLE_CAROUSEL},
            suggestions=["fix it"],
        )
    assert result == SAMPLE_CAROUSEL


# ---------- LangGraph node tests ----------

def test_scrape_node_sets_article_count(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    articles = [{"title": "A"}, {"title": "B"}]
    (data_dir / "latest_articles.json").write_text(json.dumps(articles))

    with patch("src.orchestrator.cmd_scrape"):
        from src.orchestrator import _scrape_node
        result = _scrape_node({"force": False, "articles_count": 0, "unique_count": 0,
                                "posts": [], "review_results": [], "reviewed_posts": [], "saved_count": 0,
                                "stop_reason": None})
    assert result["articles_count"] == 2
    assert result.get("stop_reason") is None


def test_scrape_node_sets_stop_reason_when_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "latest_articles.json").write_text("[]")

    with patch("src.orchestrator.cmd_scrape"):
        from src.orchestrator import _scrape_node
        result = _scrape_node({"force": False, "articles_count": 0, "unique_count": 0,
                                "posts": [], "review_results": [], "reviewed_posts": [], "saved_count": 0,
                                "stop_reason": None})
    assert result["stop_reason"] is not None
    assert result["articles_count"] == 0


def _base_state(**overrides):
    state = {
        "force": False, "articles_count": 1, "unique_count": 1,
        "posts": [], "review_results": [], "reviewed_posts": [],
        "saved_count": 0, "stop_reason": None,
    }
    state.update(overrides)
    return state


def test_review_node_stores_scores():
    review_json = json.dumps({"score": 9, "issues": [], "suggestions": []})
    post = {"article": {"title": "T", "summary": "S"}, "carousel": SAMPLE_CAROUSEL}

    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response(review_json)):
        with patch("src.orchestrator.time.sleep"):
            from src.orchestrator import _review_node
            result = _review_node(_base_state(posts=[post]))

    assert len(result["review_results"]) == 1
    assert result["review_results"][0]["score"] == 9.0


def test_revise_node_rewrites_low_score_post():
    post = {"article": {"title": "T", "summary": "S"}, "carousel": SAMPLE_CAROUSEL}
    review_results = [{"post": post, "score": 4.0, "suggestions": ["fix hook"]}]

    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response(json.dumps(SAMPLE_CAROUSEL))):
        from src.orchestrator import _revise_node
        result = _revise_node(_base_state(review_results=review_results))

    assert len(result["reviewed_posts"]) == 1
    assert result["reviewed_posts"][0]["score"] == 4.0


def test_revise_node_passes_through_high_score_post():
    post = {"article": {"title": "T", "summary": "S"}, "carousel": SAMPLE_CAROUSEL}
    review_results = [{"post": post, "score": 8.0, "suggestions": []}]

    from src.orchestrator import _revise_node
    result = _revise_node(_base_state(review_results=review_results))

    assert len(result["reviewed_posts"]) == 1
    assert result["reviewed_posts"][0]["score"] == 8.0


def test_save_draft_node_uses_reviewed_posts_when_available(tmp_path):
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    post = {"article": {"title": "T", "url": "https://x.com", "summary": "S"}, "carousel": SAMPLE_CAROUSEL}
    reviewed_posts = [{"post": post, "score": 8.0}]
    # review_results also present but should be ignored
    review_results = [{"post": post, "score": 8.0, "suggestions": []}]

    from src.orchestrator import _save_draft_node
    result = _save_draft_node(_base_state(
        reviewed_posts=reviewed_posts,
        review_results=review_results,
    ))
    assert result["saved_count"] == 1


def test_save_draft_node_falls_back_to_review_results_when_revise_skipped(tmp_path):
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    post = {"article": {"title": "T", "url": "https://x.com", "summary": "S"}, "carousel": SAMPLE_CAROUSEL}
    # reviewed_posts is empty (revise was skipped via conditional edge)
    review_results = [{"post": post, "score": 9.0, "suggestions": []}]

    from src.orchestrator import _save_draft_node
    result = _save_draft_node(_base_state(
        reviewed_posts=[],
        review_results=review_results,
    ))
    assert result["saved_count"] == 1
