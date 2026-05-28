import json
import os
from unittest.mock import patch, MagicMock

import pytest


os.environ.setdefault("DASHBOARD_PASSWORD", "testpass")
os.environ.setdefault("DASHBOARD_USERNAME", "admin")


SAMPLE_CAROUSEL = {
    "news_summary": "Test",
    "total_slides": 8,
    "slides": [
        {
            "slide_number": i,
            "title": f"S{i}",
            "subtitle": "s",
            "body": "b",
            "hashtags": "#t",
            "image_prompt": "p",
        } for i in range(1, 9)
    ],
}


def _base_state(**overrides):
    state = {
        "run_id": None,
        "force": False, "articles_count": 1, "unique_count": 1,
        "posts": [], "review_results": [], "reviewed_posts": [],
        "saved_count": 0, "saved_post_ids": [], "images_count": 0,
        "stop_reason": None,
    }
    state.update(overrides)
    return state


# ── _step wrapper ───────────────────────────────────────────────────────────

def test_step_writes_running_then_ok_on_clean_exit(tmp_db):
    import src.db as db
    from src.orchestrator import _step

    run_id = db.create_pipeline_run(trigger="cli")
    with _step(run_id, "scrape"):
        steps = {s["node"]: s for s in db.get_run_steps(run_id)}
        assert steps["scrape"]["status"] == "running"
        assert steps["scrape"]["started_at"] is not None

    steps = {s["node"]: s for s in db.get_run_steps(run_id)}
    assert steps["scrape"]["status"] == "ok"
    assert steps["scrape"]["finished_at"] is not None


def test_step_writes_failed_on_exception(tmp_db):
    import src.db as db
    from src.orchestrator import _step

    run_id = db.create_pipeline_run(trigger="cli")
    with pytest.raises(RuntimeError):
        with _step(run_id, "review"):
            raise RuntimeError("boom")

    steps = {s["node"]: s for s in db.get_run_steps(run_id)}
    assert steps["review"]["status"] == "failed"
    assert "boom" in steps["review"]["error"]


def test_step_is_noop_when_run_id_is_none():
    from src.orchestrator import _step
    # Just must not raise
    with _step(None, "scrape"):
        pass


# ── _route_after_review (pure) ──────────────────────────────────────────────

def test_route_after_review_routes_to_revise_when_low_score():
    from src.orchestrator import _route_after_review
    state = _base_state(review_results=[
        {"post": {}, "score": 5.0, "suggestions": []},
        {"post": {}, "score": 9.0, "suggestions": []},
    ])
    assert _route_after_review(state) == "revise"


def test_route_after_review_routes_to_save_draft_when_all_high():
    from src.orchestrator import _route_after_review
    state = _base_state(review_results=[
        {"post": {}, "score": 8.0, "suggestions": []},
        {"post": {}, "score": 9.0, "suggestions": []},
    ])
    assert _route_after_review(state) == "save_draft"


def test_route_after_review_writes_nothing_to_db(tmp_db):
    """The conditional edge is pure — must not touch the DB."""
    import src.db as db
    from src.orchestrator import _route_after_review

    run_id = db.create_pipeline_run(trigger="cli")
    before = {s["node"]: s["status"] for s in db.get_run_steps(run_id)}

    _route_after_review(_base_state(
        run_id=run_id,
        review_results=[{"post": {}, "score": 4.0, "suggestions": []}],
    ))

    after = {s["node"]: s["status"] for s in db.get_run_steps(run_id)}
    assert before == after


# ── _save_draft_node ────────────────────────────────────────────────────────

def test_save_draft_writes_revise_skipped_when_arriving_without_revised(tmp_db):
    import src.db as db
    from src.orchestrator import _save_draft_node

    run_id = db.create_pipeline_run(trigger="cli")
    post = {"article": {"title": "T", "url": "https://x.com", "summary": "S"},
            "carousel": SAMPLE_CAROUSEL}

    _save_draft_node(_base_state(
        run_id=run_id,
        reviewed_posts=[],
        review_results=[{"post": post, "score": 9.0, "suggestions": []}],
    ))

    steps = {s["node"]: s for s in db.get_run_steps(run_id)}
    assert steps["revise"]["status"] == "skipped"
    assert steps["revise"]["finished_at"] is not None


def test_save_draft_returns_saved_post_ids(tmp_db):
    from src.orchestrator import _save_draft_node

    post = {"article": {"title": "T", "url": "https://x.com", "summary": "S"},
            "carousel": SAMPLE_CAROUSEL}

    result = _save_draft_node(_base_state(
        reviewed_posts=[{"post": post, "score": 8.0}],
    ))
    assert result["saved_count"] == 1
    assert len(result["saved_post_ids"]) == 1
    assert isinstance(result["saved_post_ids"][0], int)


def test_save_draft_skipped_write_not_triggered_when_revise_ran(tmp_db):
    import src.db as db
    from src.orchestrator import _save_draft_node

    run_id = db.create_pipeline_run(trigger="cli")
    db.update_run_step(run_id, "revise", status="ok",
                       started_at=db.now_iso(), finished_at=db.now_iso())

    post = {"article": {"title": "T", "url": "https://x.com", "summary": "S"},
            "carousel": SAMPLE_CAROUSEL}
    _save_draft_node(_base_state(
        run_id=run_id,
        reviewed_posts=[{"post": post, "score": 6.0}],  # came from revise
    ))

    steps = {s["node"]: s for s in db.get_run_steps(run_id)}
    assert steps["revise"]["status"] == "ok"  # untouched


# ── _images_node ────────────────────────────────────────────────────────────

def test_images_node_with_empty_ids_writes_no_posts(tmp_db):
    import src.db as db
    from src.orchestrator import _images_node

    run_id = db.create_pipeline_run(trigger="cli")
    result = _images_node(_base_state(run_id=run_id, saved_post_ids=[]))
    assert result["images_count"] == 0

    steps = {s["node"]: s for s in db.get_run_steps(run_id)}
    assert steps["images"]["status"] == "ok"
    assert "no posts" in (steps["images"]["progress"] or "")


def test_images_node_calls_generate_for_post_per_id(tmp_db):
    import src.db as db
    from src.orchestrator import _images_node

    run_id = db.create_pipeline_run(trigger="cli")
    db.save_reviewed_post("h1", "https://x.com/1", "T1",
                          {"slides": [], "brand_domain": "x.com"}, 8.0)
    db.save_reviewed_post("h2", "https://x.com/2", "T2",
                          {"slides": [], "brand_domain": "y.com"}, 8.0)
    ids = [r["id"] for r in db.get_pending_posts()]

    with patch("src.ImageGen.generate_for_post_with_events",
               return_value=([], [])) as gen:
        with patch("src.db.save_image_paths"):
            result = _images_node(_base_state(run_id=run_id, saved_post_ids=ids))

    assert gen.call_count == 2
    assert result["images_count"] == 2


# ── run_pipeline lifecycle ──────────────────────────────────────────────────

def test_run_pipeline_creates_run_row_when_no_id_passed(tmp_db, tmp_path, monkeypatch):
    import src.db as db
    from src.orchestrator import run_pipeline

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "latest_articles.json").write_text("[]")

    with patch("src.orchestrator.cmd_scrape"):
        run_pipeline(force=False)

    runs = db.get_recent_runs(limit=5)
    assert len(runs) == 1
    assert runs[0]["trigger"] == "cli"
    assert runs[0]["status"] == "stopped"
    assert "no new articles" in (runs[0]["stop_reason"] or "")


def test_run_pipeline_uses_provided_run_id(tmp_db, tmp_path, monkeypatch):
    import src.db as db
    from src.orchestrator import run_pipeline

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "latest_articles.json").write_text("[]")

    run_id = db.create_pipeline_run(trigger="dashboard")
    with patch("src.orchestrator.cmd_scrape"):
        run_pipeline(force=False, run_id=run_id)

    runs = db.get_recent_runs(limit=5)
    assert len(runs) == 1
    assert runs[0]["id"] == run_id
    assert runs[0]["trigger"] == "dashboard"


def test_run_pipeline_aborts_when_another_run_active(tmp_db, monkeypatch):
    import src.db as db
    from src.orchestrator import run_pipeline

    db.create_pipeline_run(trigger="cli")

    with pytest.raises(SystemExit) as exc:
        run_pipeline(force=False)
    assert exc.value.code == 2

    # Only the originally claimed run should exist.
    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0]
    assert count == 1


def test_run_pipeline_marks_failed_on_uncaught(tmp_db, tmp_path, monkeypatch):
    import src.db as db
    from src.orchestrator import run_pipeline

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    with patch("src.orchestrator.cmd_scrape", side_effect=RuntimeError("scrape blew up")):
        with pytest.raises(RuntimeError):
            run_pipeline(force=False)

    runs = db.get_recent_runs(limit=1)
    assert runs[0]["status"] == "failed"
    assert "scrape blew up" in (runs[0]["error"] or "")


# ── node wrapping regression: return shape unchanged ────────────────────────

def test_scrape_node_with_run_id_still_returns_articles_count(tmp_db, tmp_path, monkeypatch):
    import src.db as db
    from src.orchestrator import _scrape_node

    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "latest_articles.json").write_text(json.dumps([{"title": "A"}]))

    run_id = db.create_pipeline_run(trigger="cli")
    with patch("src.orchestrator.cmd_scrape"):
        result = _scrape_node(_base_state(run_id=run_id))
    assert result["articles_count"] == 1
    assert result.get("stop_reason") is None
