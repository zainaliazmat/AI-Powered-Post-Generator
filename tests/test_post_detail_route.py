"""Tests for GET /posts/{id} — the post detail page (Task 7)."""

import json

import pytest


_AUTH = ("admin", "testpass")


def _seed_post(article_hash="abc", status=None, review_score=8.0,
               carousel_overrides=None):
    import src.db as db
    carousel = {
        "total_slides": 2,
        "slides": [
            {"slide_number": 1, "title": "S1", "subtitle": "s1", "body": "b1",
             "hashtags": "#t", "image_prompt": "p1", "caption": "Caption-One"},
            {"slide_number": 2, "title": "S2", "subtitle": "s2", "body": "b2",
             "hashtags": "#t", "image_prompt": "p2", "caption": "Caption-Two"},
        ],
        "brand_domain": "openai.com",
    }
    if carousel_overrides:
        carousel.update(carousel_overrides)
    db.save_reviewed_post(
        article_hash=article_hash,
        article_url="https://x.com/article",
        article_title="Test Article: AI Drops",
        carousel_json=carousel,
        review_score=review_score,
    )
    with db.get_conn() as conn:
        post_id = conn.execute(
            "SELECT id FROM generated_posts WHERE article_hash=?",
            (article_hash,),
        ).fetchone()["id"]
        if status is not None:
            conn.execute(
                "UPDATE generated_posts SET status=? WHERE id=?", (status, post_id)
            )
    return post_id


# ---------- 404 / 200 basics ----------

def test_post_detail_returns_404_for_unknown_id(client):
    r = client.get("/posts/99999", auth=_AUTH)
    assert r.status_code == 404


@pytest.mark.parametrize("status", [
    "pending_review", "image_ready", "approved", "rejected", "published", "failed",
])
def test_post_detail_returns_200_for_each_status(client, status):
    post_id = _seed_post(article_hash=f"hash_{status}", status=status)
    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    assert r.status_code == 200
    assert "Test Article: AI Drops" in r.text


def test_post_detail_requires_auth(client):
    post_id = _seed_post()
    r = client.get(f"/posts/{post_id}")
    assert r.status_code == 401


# ---------- approve/reject visibility ----------

def test_post_detail_shows_approve_reject_for_pending(client):
    post_id = _seed_post(status="pending_review")
    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    assert "/review/" in r.text  # at least one form posts to /review/{id}/...
    assert "approve" in r.text.lower()


def test_post_detail_hides_approve_reject_for_approved(client):
    post_id = _seed_post(article_hash="z", status="approved")
    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    # Decision buttons should not be present for an already-approved post
    assert "/review/{}/approve".format(post_id) not in r.text


# ---------- lifecycle: placeholders ----------

def test_post_detail_renders_all_four_lifecycle_cards(client):
    """With no events at all, all four stage labels still appear."""
    post_id = _seed_post()
    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    body = r.text
    for stage in ("Generated", "Reviewed", "Revised", "Rendered"):
        assert stage in body, f"missing stage card: {stage}"


def test_post_detail_revised_shows_skipped_when_score_high_and_no_event(client):
    """Score ≥ 7 + no revise event → 'Skipped — score ≥ 7' (not muted)."""
    post_id = _seed_post(review_score=8.5)
    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    assert "Skipped" in r.text


# ---------- lifecycle: real event data ----------

def test_post_detail_renders_review_issues_and_suggestions(client):
    import src.db as db
    post_id = _seed_post()
    db.insert_post_event(
        post_id=post_id, run_id=None, stage="review", status="ok",
        prompt_vars={"title": "T", "summary": "S", "carousel_json": {}},
        output={"score": 6, "issues": ["weak hook XYZ"],
                "suggestions": ["add urgency ABC"]},
        duration_ms=12,
    )
    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    assert "weak hook XYZ" in r.text
    assert "add urgency ABC" in r.text


def test_post_detail_renders_revise_diff_fields(client):
    import src.db as db
    post_id = _seed_post(review_score=4.0)
    pre = {"total_slides": 1, "slides": [
        {"slide_number": 1, "title": "TITLE-BEFORE", "subtitle": "sub",
         "body": "body", "hashtags": "#x", "image_prompt": "p"},
    ]}
    post = {"total_slides": 1, "slides": [
        {"slide_number": 1, "title": "TITLE-AFTER", "subtitle": "sub",
         "body": "body", "hashtags": "#x", "image_prompt": "p"},
    ]}
    db.insert_post_event(
        post_id=post_id, run_id=None, stage="revise", status="ok",
        prompt_vars={"pre_carousel": pre, "suggestions": ["fix"]},
        output={"post_carousel": post, "attempts": 1, "fell_back": False},
        duration_ms=44,
    )
    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    assert "TITLE-BEFORE" in r.text
    assert "TITLE-AFTER" in r.text


def test_post_detail_renders_image_event_safe_path_as_url(client):
    import src.db as db
    post_id = _seed_post()
    db.insert_post_event(
        post_id=post_id, run_id=None, stage="images", status="ok",
        prompt_vars={"renderer": "pillow", "brand_domain": "openai.com"},
        output={"slides": [
            {"idx": 1, "prompt": "p1", "enriched_prompt": None,
             "path": "data/images/42_1.png", "status": "ok"},
        ]},
        duration_ms=200,
    )
    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    assert "pillow" in r.text
    assert "/static/images/42_1.png" in r.text


def test_post_detail_renders_unsafe_image_path_as_inert_code(client):
    """A path like ../../etc/passwd must NOT appear inside a src= attribute."""
    import src.db as db
    post_id = _seed_post()
    db.insert_post_event(
        post_id=post_id, run_id=None, stage="images", status="ok",
        prompt_vars={"renderer": "pillow", "brand_domain": None},
        output={"slides": [
            {"idx": 1, "prompt": "p1", "enriched_prompt": None,
             "path": "../../etc/passwd", "status": "ok"},
        ]},
        duration_ms=10,
    )
    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    assert r.status_code == 200
    # The path may appear as inert text but never inside src="..."
    assert 'src="../../etc/passwd"' not in r.text
    assert "src='../../etc/passwd'" not in r.text


# ---------- robustness ----------

def test_post_detail_renders_when_event_output_is_malformed_json(client):
    """A post_events row with bad JSON in output must not 500."""
    import src.db as db
    post_id = _seed_post()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO post_events "
            "(post_id, run_id, stage, status, prompt_vars, output, duration_ms) "
            "VALUES (?, ?, 'review', 'ok', ?, ?, ?)",
            (post_id, None, "{bad json", "{also bad", 5),
        )
    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    assert r.status_code == 200


def test_post_detail_shows_run_link_when_run_id_set(client):
    import src.db as db
    post_id = _seed_post()
    with db.get_conn() as conn:
        run_id = conn.execute(
            "INSERT INTO pipeline_runs (trigger, status, started_at) "
            "VALUES ('cli','ok','t')"
        ).lastrowid
    db.set_post_run_id(post_id, run_id)

    r = client.get(f"/posts/{post_id}", auth=_AUTH)
    assert f"/pipeline/runs/{run_id}" in r.text


# ---------- approve/reject redirect_to ----------

def test_approve_with_redirect_to_review_sets_hx_redirect_header(client):
    """POST /review/{id}/approve with redirect_to=/review → HX-Redirect: /review."""
    post_id = _seed_post(status="pending_review")
    r = client.post(
        f"/review/{post_id}/approve",
        data={"redirect_to": "/review"},
        auth=_AUTH,
    )
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == "/review"


def test_approve_without_redirect_to_returns_no_redirect_header(client):
    """No redirect_to field → no HX-Redirect (existing queue-card behavior)."""
    post_id = _seed_post(status="pending_review")
    r = client.post(f"/review/{post_id}/approve", auth=_AUTH)
    assert r.status_code == 200
    assert "HX-Redirect" not in r.headers


def test_approve_with_unwhitelisted_redirect_to_is_ignored(client):
    """Open-redirect attempt → no HX-Redirect (silently falls back)."""
    post_id = _seed_post(status="pending_review")
    r = client.post(
        f"/review/{post_id}/approve",
        data={"redirect_to": "https://evil.com/phish"},
        auth=_AUTH,
    )
    assert r.status_code == 200
    assert "HX-Redirect" not in r.headers


def test_approve_with_post_specific_redirect_to_is_allowed(client):
    """redirect_to=/posts/{id} (the same post) is whitelisted."""
    post_id = _seed_post(status="pending_review")
    r = client.post(
        f"/review/{post_id}/approve",
        data={"redirect_to": f"/posts/{post_id}"},
        auth=_AUTH,
    )
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == f"/posts/{post_id}"


def test_reject_with_redirect_to_sets_hx_redirect_header(client):
    """Reject with redirect_to=/review → HX-Redirect: /review."""
    post_id = _seed_post(status="pending_review")
    r = client.post(
        f"/review/{post_id}/reject",
        data={"reason": "not interesting enough yet", "redirect_to": "/review"},
        auth=_AUTH,
    )
    assert r.status_code == 200
    assert r.headers.get("HX-Redirect") == "/review"


def test_reject_with_short_reason_does_not_set_hx_redirect(client):
    """Validation failure on reject → no redirect (panel re-renders)."""
    post_id = _seed_post(status="pending_review")
    r = client.post(
        f"/review/{post_id}/reject",
        data={"reason": "too short", "redirect_to": "/review"},
        auth=_AUTH,
    )
    assert r.status_code == 200
    assert "HX-Redirect" not in r.headers
