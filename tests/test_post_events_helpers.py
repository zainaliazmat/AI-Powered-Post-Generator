import json


def _seed_post(article_hash: str = "h1") -> int:
    import src.db as db
    db.save_reviewed_post(article_hash, "https://x.com/a", "T", {"slides": []}, 8.0)
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT id FROM generated_posts WHERE article_hash=?", (article_hash,)
        ).fetchone()["id"]


def test_insert_post_event_round_trip(tmp_db):
    import src.db as db
    post_id = _seed_post()
    db.insert_post_event(
        post_id=post_id, run_id=None, stage="review", status="ok",
        prompt_vars={"title": "X", "summary": "Y"},
        output={"score": 8, "issues": []},
        duration_ms=1234,
    )
    events = db.get_post_events(post_id)
    assert len(events) == 1
    e = events[0]
    assert e["stage"] == "review"
    assert e["status"] == "ok"
    assert e["duration_ms"] == 1234
    assert json.loads(e["prompt_vars"]) == {"title": "X", "summary": "Y"}
    assert json.loads(e["output"]) == {"score": 8, "issues": []}


def test_get_post_events_orders_by_created_at_ascending(tmp_db):
    import src.db as db
    post_id = _seed_post()
    for stage in ("review", "revise", "images"):
        db.insert_post_event(
            post_id=post_id, run_id=None, stage=stage, status="ok",
            prompt_vars={}, output={}, duration_ms=0,
        )
    stages = [e["stage"] for e in db.get_post_events(post_id)]
    assert stages == ["review", "revise", "images"]


def test_get_post_events_returns_empty_list_for_post_with_no_events(tmp_db):
    import src.db as db
    post_id = _seed_post()
    assert db.get_post_events(post_id) == []


def test_set_post_run_id_updates_row(tmp_db):
    import src.db as db
    post_id = _seed_post()
    with db.get_conn() as conn:
        run_id = conn.execute(
            "INSERT INTO pipeline_runs (trigger, status, started_at) VALUES ('cli','ok','t')"
        ).lastrowid
    db.set_post_run_id(post_id, run_id)
    with db.get_conn() as conn:
        assert conn.execute(
            "SELECT run_id FROM generated_posts WHERE id=?", (post_id,)
        ).fetchone()["run_id"] == run_id
