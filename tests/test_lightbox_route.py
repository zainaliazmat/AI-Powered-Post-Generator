"""Tests for GET /posts/{id}/lightbox — the carousel lightbox (Task 8)."""

import json

import pytest


_AUTH = ("admin", "testpass")


def _seed_post_with_images(article_hash="lb_abc", slide_count=3, images=True):
    import src.db as db
    slides = []
    for i in range(slide_count):
        slides.append({
            "slide_number": i + 1,
            "title": f"S{i + 1}",
            "subtitle": f"sub{i + 1}",
            "body": f"body{i + 1}",
            "hashtags": "#x",
            "image_prompt": f"prompt-{i + 1}",
            "caption": f"caption-{i + 1}",
        })
    carousel = {"total_slides": slide_count, "slides": slides}
    db.save_reviewed_post(
        article_hash=article_hash,
        article_url="https://example.com/article",
        article_title="LB Article",
        carousel_json=carousel,
        review_score=8.0,
    )
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM generated_posts WHERE article_hash=?",
            (article_hash,),
        ).fetchone()
        post_id = row["id"]
        if images:
            image_paths = [f"data/images/{post_id}_{i + 1}.png" for i in range(slide_count)]
            conn.execute(
                "UPDATE generated_posts SET image_paths=? WHERE id=?",
                (json.dumps(image_paths), post_id),
            )
    return post_id


# ---------- existence / 404 ----------

def test_lightbox_returns_404_for_unknown_post(client):
    r = client.get("/posts/99999/lightbox", auth=_AUTH)
    assert r.status_code == 404


def test_lightbox_returns_404_when_post_has_zero_slides(client):
    """A post whose carousel parses to zero slides → 404."""
    import src.db as db
    db.save_reviewed_post(
        article_hash="empty_lb",
        article_url="https://example.com/empty",
        article_title="Empty",
        carousel_json={"total_slides": 0, "slides": []},
        review_score=8.0,
    )
    with db.get_conn() as conn:
        post_id = conn.execute(
            "SELECT id FROM generated_posts WHERE article_hash=?", ("empty_lb",),
        ).fetchone()["id"]
    r = client.get(f"/posts/{post_id}/lightbox", auth=_AUTH)
    assert r.status_code == 404


def test_lightbox_requires_auth(client):
    post_id = _seed_post_with_images()
    r = client.get(f"/posts/{post_id}/lightbox")
    assert r.status_code == 401


# ---------- slide clamping ----------

def test_lightbox_clamps_slide_zero_to_one(client):
    post_id = _seed_post_with_images(slide_count=3)
    r = client.get(f"/posts/{post_id}/lightbox?slide=0", auth=_AUTH)
    assert r.status_code == 200
    # Should render slide 1 (first slide) — counter shows "1 of 3"
    assert "1 of 3" in r.text or "1 / 3" in r.text


def test_lightbox_clamps_slide_above_max_to_last(client):
    post_id = _seed_post_with_images(slide_count=3)
    r = client.get(f"/posts/{post_id}/lightbox?slide=999", auth=_AUTH)
    assert r.status_code == 200
    assert "3 of 3" in r.text or "3 / 3" in r.text


def test_lightbox_renders_requested_slide(client):
    post_id = _seed_post_with_images(slide_count=4)
    r = client.get(f"/posts/{post_id}/lightbox?slide=2", auth=_AUTH)
    assert r.status_code == 200
    # The image for slide 2 should be referenced
    assert f"/static/images/{post_id}_2.png" in r.text


# ---------- HTMX vs standalone ----------

def test_lightbox_htmx_returns_overlay_partial_only(client):
    """HX-Request: true → partial (no <html>/<body>)."""
    post_id = _seed_post_with_images()
    r = client.get(
        f"/posts/{post_id}/lightbox?slide=1",
        auth=_AUTH,
        headers={"HX-Request": "true"},
    )
    assert r.status_code == 200
    body = r.text
    # Partial — no full document wrapper
    assert "<!DOCTYPE html>" not in body
    assert "<html" not in body.lower()
    # But the lightbox overlay markup is present
    assert "lightbox" in body.lower()


def test_lightbox_standalone_returns_full_document(client):
    """No HX-Request header → standalone page extending base.html."""
    post_id = _seed_post_with_images()
    r = client.get(f"/posts/{post_id}/lightbox?slide=1", auth=_AUTH)
    assert r.status_code == 200
    body = r.text
    # Full doc — has the <!DOCTYPE>, <html>, sidebar from base.html
    assert "<!DOCTYPE html>" in body
    assert "<html" in body.lower()


# ---------- navigation links ----------

def test_lightbox_prev_next_anchors_present(client):
    """Slide 2 of 4 → prev links to slide 1, next links to slide 3."""
    post_id = _seed_post_with_images(slide_count=4)
    r = client.get(f"/posts/{post_id}/lightbox?slide=2", auth=_AUTH)
    body = r.text
    assert f"/posts/{post_id}/lightbox?slide=1" in body
    assert f"/posts/{post_id}/lightbox?slide=3" in body


def test_lightbox_close_link_points_back_to_detail(client):
    post_id = _seed_post_with_images()
    r = client.get(f"/posts/{post_id}/lightbox?slide=1", auth=_AUTH)
    assert f"/posts/{post_id}" in r.text


def test_lightbox_slide_without_image_renders_placeholder(client):
    """If a slide has no image yet, the lightbox still renders."""
    post_id = _seed_post_with_images(slide_count=2, images=False)
    r = client.get(f"/posts/{post_id}/lightbox?slide=1", auth=_AUTH)
    assert r.status_code == 200
