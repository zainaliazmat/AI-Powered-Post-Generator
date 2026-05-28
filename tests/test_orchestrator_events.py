"""Tests for orchestrator → post_events event capture (Task 5)."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_litellm_response(content: str) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    return resp


SAMPLE_CAROUSEL = {
    "news_summary": "Test summary",
    "total_slides": 4,
    "slides": [
        {"slide_number": i + 1, "title": f"S{i + 1}", "subtitle": "x", "body": "x",
         "hashtags": "#t", "image_prompt": f"p{i + 1}"} for i in range(4)
    ],
}


def _post(title: str = "T", url: str = "https://x.com/a") -> dict:
    return {
        "article": {"title": title, "url": url, "summary": "S"},
        "carousel": SAMPLE_CAROUSEL,
    }


def _base_state(**overrides):
    state = {
        "force": False, "articles_count": 1, "unique_count": 1,
        "posts": [], "review_results": [], "reviewed_posts": [],
        "saved_count": 0, "stop_reason": None,
    }
    state.update(overrides)
    return state


# ---------- review node ----------

def test_review_node_buffers_one_event_per_post():
    review_json = json.dumps({"score": 8, "issues": ["w"], "suggestions": ["s"]})
    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response(review_json)), \
         patch("src.orchestrator.time.sleep"):
        from src.orchestrator import _review_node, _article_hash
        post = _post()
        result = _review_node(_base_state(posts=[post]))

    buf = result.get("event_buffer", [])
    assert len(buf) == 1
    e = buf[0]
    assert e["article_hash"] == _article_hash(post["article"])
    assert e["stage"] == "review"
    assert e["status"] == "ok"
    assert e["output"]["score"] == 8
    assert e["output"]["issues"] == ["w"]
    assert e["output"]["suggestions"] == ["s"]
    assert e["prompt_vars"]["title"] == "T"
    assert e["prompt_vars"]["summary"] == "S"
    assert e["prompt_vars"]["carousel_json"] == SAMPLE_CAROUSEL
    assert isinstance(e["duration_ms"], int)
    assert e["duration_ms"] >= 0


def test_review_node_buffers_events_for_multiple_posts():
    review_json = json.dumps({"score": 7, "issues": [], "suggestions": []})
    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response(review_json)), \
         patch("src.orchestrator.time.sleep"):
        from src.orchestrator import _review_node
        posts = [_post(title=f"P{i}", url=f"https://x.com/{i}") for i in range(3)]
        result = _review_node(_base_state(posts=posts))

    assert len(result["event_buffer"]) == 3
    assert {e["stage"] for e in result["event_buffer"]} == {"review"}


# ---------- revise node ----------

def test_revise_node_buffers_event_only_for_low_score_posts():
    low = _post(title="LOW", url="https://x.com/low")
    high = _post(title="HIGH", url="https://x.com/high")
    review_results = [
        {"post": low,  "score": 4.0, "suggestions": ["fix"]},
        {"post": high, "score": 9.0, "suggestions": []},
    ]
    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response(json.dumps(SAMPLE_CAROUSEL))):
        from src.orchestrator import _revise_node, _article_hash
        result = _revise_node(_base_state(review_results=review_results))

    buf = result.get("event_buffer", [])
    assert len(buf) == 1
    e = buf[0]
    assert e["article_hash"] == _article_hash(low["article"])
    assert e["stage"] == "revise"
    assert e["status"] == "ok"
    assert e["output"]["fell_back"] is False
    assert e["output"]["attempts"] >= 1
    assert e["output"]["post_carousel"]["total_slides"] == 4
    assert e["prompt_vars"]["pre_carousel"] == SAMPLE_CAROUSEL
    assert e["prompt_vars"]["suggestions"] == ["fix"]


def test_revise_node_marks_fell_back_when_llm_outputs_invalid():
    low = _post()
    review_results = [{"post": low, "score": 4.0, "suggestions": ["fix"]}]
    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response("not json")):
        from src.orchestrator import _revise_node
        result = _revise_node(_base_state(review_results=review_results))

    buf = result["event_buffer"]
    assert len(buf) == 1
    assert buf[0]["output"]["fell_back"] is True
    # post_carousel is the un-modified original because revise gave up
    assert buf[0]["output"]["post_carousel"] == SAMPLE_CAROUSEL


def test_revise_node_preserves_inbound_event_buffer():
    """When _revise_node runs, it must extend the existing event_buffer
    (populated by _review_node), not overwrite it."""
    low = _post()
    review_results = [{"post": low, "score": 4.0, "suggestions": ["fix"]}]
    seed = [{"article_hash": "AAA", "stage": "review", "status": "ok",
             "prompt_vars": {}, "output": {}, "duration_ms": 0}]
    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response(json.dumps(SAMPLE_CAROUSEL))):
        from src.orchestrator import _revise_node
        result = _revise_node(_base_state(
            review_results=review_results, event_buffer=seed))

    buf = result["event_buffer"]
    assert len(buf) == 2
    # original review event preserved
    assert buf[0]["article_hash"] == "AAA"
    # new revise event appended
    assert buf[1]["stage"] == "revise"


# ---------- save_draft node ----------

def test_save_draft_node_flushes_buffer_and_sets_run_id(tmp_db):
    import src.db as db_module
    with db_module.get_conn() as conn:
        run_id = conn.execute(
            "INSERT INTO pipeline_runs (trigger, status, started_at) "
            "VALUES ('cli','ok','t')"
        ).lastrowid

    from src.orchestrator import _save_draft_node, _article_hash
    post = _post()
    article_hash = _article_hash(post["article"])
    buf = [
        {"article_hash": article_hash, "stage": "review", "status": "ok",
         "prompt_vars": {"x": 1}, "output": {"score": 4}, "duration_ms": 11},
        {"article_hash": article_hash, "stage": "revise", "status": "ok",
         "prompt_vars": {"y": 2}, "output": {"fell_back": False}, "duration_ms": 22},
    ]
    state = _base_state(
        run_id=run_id,
        reviewed_posts=[{"post": post, "score": 4.0}],
        event_buffer=buf,
    )
    result = _save_draft_node(state)

    assert result["event_buffer"] == []
    assert len(result["saved_post_ids"]) == 1
    post_id = result["saved_post_ids"][0]

    events = db_module.get_post_events(post_id)
    assert len(events) == 2
    assert {e["stage"] for e in events} == {"review", "revise"}
    # run_id is propagated
    for e in events:
        assert e["run_id"] == run_id
    # generated_posts.run_id is set
    with db_module.get_conn() as conn:
        row = conn.execute(
            "SELECT run_id FROM generated_posts WHERE id = ?", (post_id,)
        ).fetchone()
    assert row["run_id"] == run_id


def test_save_draft_node_drops_orphan_buffered_events(tmp_db):
    """If a buffered event's article_hash doesn't match any saved post,
    it is dropped (and the buffer ends empty), not retained."""
    import src.db as db_module
    with db_module.get_conn() as conn:
        run_id = conn.execute(
            "INSERT INTO pipeline_runs (trigger, status, started_at) "
            "VALUES ('cli','ok','t')"
        ).lastrowid

    from src.orchestrator import _save_draft_node
    post = _post()
    state = _base_state(
        run_id=run_id,
        reviewed_posts=[{"post": post, "score": 8.0}],
        event_buffer=[
            {"article_hash": "DOES-NOT-MATCH", "stage": "review", "status": "ok",
             "prompt_vars": {}, "output": {}, "duration_ms": 0},
        ],
    )
    result = _save_draft_node(state)
    assert result["event_buffer"] == []


def test_save_draft_node_survives_insert_event_failure(tmp_db):
    """If db.insert_post_event raises, the pipeline still completes and
    generated_posts rows are still written."""
    import src.db as db_module
    with db_module.get_conn() as conn:
        run_id = conn.execute(
            "INSERT INTO pipeline_runs (trigger, status, started_at) "
            "VALUES ('cli','ok','t')"
        ).lastrowid

    from src.orchestrator import _save_draft_node, _article_hash
    post = _post()
    state = _base_state(
        run_id=run_id,
        reviewed_posts=[{"post": post, "score": 8.0}],
        event_buffer=[
            {"article_hash": _article_hash(post["article"]),
             "stage": "review", "status": "ok",
             "prompt_vars": {}, "output": {}, "duration_ms": 0},
        ],
    )
    with patch("src.orchestrator.db.insert_post_event",
               side_effect=RuntimeError("boom")):
        result = _save_draft_node(state)

    assert result["saved_count"] == 1
    assert len(result["saved_post_ids"]) == 1


def test_save_draft_node_falls_back_to_review_results_when_revise_skipped(tmp_db):
    """When the conditional edge skipped revise (reviewed_posts empty),
    save_draft must still flush review events using review_results."""
    import src.db as db_module
    with db_module.get_conn() as conn:
        run_id = conn.execute(
            "INSERT INTO pipeline_runs (trigger, status, started_at) "
            "VALUES ('cli','ok','t')"
        ).lastrowid

    from src.orchestrator import _save_draft_node, _article_hash
    post = _post()
    article_hash = _article_hash(post["article"])
    state = _base_state(
        run_id=run_id,
        reviewed_posts=[],
        review_results=[{"post": post, "score": 9.0, "suggestions": []}],
        event_buffer=[
            {"article_hash": article_hash, "stage": "review", "status": "ok",
             "prompt_vars": {}, "output": {"score": 9}, "duration_ms": 5},
        ],
    )
    result = _save_draft_node(state)
    assert result["saved_count"] == 1
    post_id = result["saved_post_ids"][0]
    events = db_module.get_post_events(post_id)
    assert len(events) == 1
    assert events[0]["stage"] == "review"


# ---------- images node ----------

def test_images_node_writes_one_event_per_rendered_post(tmp_db):
    import src.db as db_module
    article_hash = "imgevthash"
    db_module.save_reviewed_post(
        article_hash=article_hash,
        article_url="https://x.com/a",
        article_title="T",
        carousel_json={
            "slides": [
                {"slide_number": 1, "title": "S1", "image_prompt": "p1"},
                {"slide_number": 2, "title": "S2", "image_prompt": "p2"},
            ],
            "total_slides": 2,
            "brand_domain": "openai.com",
        },
        review_score=8.0,
    )
    with db_module.get_conn() as conn:
        post_id = conn.execute(
            "SELECT id FROM generated_posts WHERE article_hash=?",
            (article_hash,),
        ).fetchone()["id"]
        run_id = conn.execute(
            "INSERT INTO pipeline_runs (trigger, status, started_at) "
            "VALUES ('cli','ok','t')"
        ).lastrowid

    fake_slide_events = [
        {"idx": 1, "prompt": "p1", "enriched_prompt": None,
         "path": "data/images/x_1.png", "status": "ok"},
        {"idx": 2, "prompt": "p2", "enriched_prompt": None,
         "path": "data/images/x_2.png", "status": "ok"},
    ]
    fake_paths = [Path("data/images/x_1.png"), Path("data/images/x_2.png")]
    with patch("src.ImageGen.generate_for_post_with_events",
               return_value=(fake_paths, fake_slide_events)):
        from src.orchestrator import _images_node
        state = _base_state(run_id=run_id, saved_post_ids=[post_id])
        result = _images_node(state)

    assert result["images_count"] == 1
    events = db_module.get_post_events(post_id)
    assert len(events) == 1
    e = events[0]
    assert e["stage"] == "images"
    assert e["status"] == "ok"
    pv = json.loads(e["prompt_vars"])
    assert pv["renderer"] in {"pillow", "flux"}
    assert pv["brand_domain"] == "openai.com"
    out = json.loads(e["output"])
    assert out["slides"] == fake_slide_events


def test_images_node_survives_insert_event_failure(tmp_db):
    """db.insert_post_event raising must not break the images node."""
    import src.db as db_module
    db_module.save_reviewed_post(
        article_hash="h", article_url="u", article_title="T",
        carousel_json={
            "slides": [{"slide_number": 1, "title": "S", "image_prompt": "p"}],
            "total_slides": 1,
        },
        review_score=8.0,
    )
    with db_module.get_conn() as conn:
        post_id = conn.execute(
            "SELECT id FROM generated_posts WHERE article_hash='h'"
        ).fetchone()["id"]
        run_id = conn.execute(
            "INSERT INTO pipeline_runs (trigger, status, started_at) "
            "VALUES ('cli','ok','t')"
        ).lastrowid

    with patch("src.ImageGen.generate_for_post_with_events",
               return_value=([Path("data/images/x_1.png")],
                             [{"idx": 1, "prompt": "p", "enriched_prompt": None,
                               "path": "data/images/x_1.png", "status": "ok"}])), \
         patch("src.orchestrator.db.insert_post_event",
               side_effect=RuntimeError("boom")):
        from src.orchestrator import _images_node
        result = _images_node(_base_state(run_id=run_id, saved_post_ids=[post_id]))

    assert result["images_count"] == 1
