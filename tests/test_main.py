import os
import pytest

os.environ.setdefault("DASHBOARD_PASSWORD", "testpass")
os.environ.setdefault("DASHBOARD_USERNAME", "admin")


# ── DB function tests ──────────────────────────────────────────────────────────

def test_get_post_counts_empty(tmp_path, monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    counts = db.get_post_counts()
    assert counts["pending_review"] == 0
    assert counts["image_ready"] == 0
    assert counts["approved"] == 0
    assert counts["rejected"] == 0
    assert counts["total"] == 0


def test_get_post_counts_reflects_status(tmp_path, monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.save_reviewed_post("h1", "http://x.com/1", "T1", {"slides": []}, 7.5)
    db.save_reviewed_post("h2", "http://x.com/2", "T2", {"slides": []}, 8.0)
    counts = db.get_post_counts()
    assert counts["pending_review"] == 2
    assert counts["total"] == 2


def test_approve_post_success(tmp_path, monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.save_reviewed_post("h1", "http://x.com/1", "T1", {"slides": []}, 7.5)
    post_id = db.get_pending_posts()[0]["id"]
    result = db.approve_post(post_id)
    assert result is True
    post = db.get_post(post_id)
    assert post["status"] == "approved"
    assert post["reviewed_at"] is not None


def test_approve_post_already_reviewed_returns_false(tmp_path, monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.save_reviewed_post("h1", "http://x.com/1", "T1", {"slides": []}, 7.5)
    post_id = db.get_pending_posts()[0]["id"]
    db.approve_post(post_id)
    result = db.approve_post(post_id)  # second call
    assert result is False


def test_reject_post_success(tmp_path, monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.save_reviewed_post("h1", "http://x.com/1", "T1", {"slides": []}, 7.5)
    post_id = db.get_pending_posts()[0]["id"]
    result = db.reject_post(post_id, "Caption too vague, no concrete numbers")
    assert result is True
    post = db.get_post(post_id)
    assert post["status"] == "rejected"
    assert post["rejection_reason"] == "Caption too vague, no concrete numbers"
    assert post["reviewed_at"] is not None


def test_reject_post_already_reviewed_returns_false(tmp_path, monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.save_reviewed_post("h1", "http://x.com/1", "T1", {"slides": []}, 7.5)
    post_id = db.get_pending_posts()[0]["id"]
    db.reject_post(post_id, "Caption too vague, no concrete numbers")
    result = db.reject_post(post_id, "Another reason entirely here")
    assert result is False


def test_get_review_queue_pagination(tmp_path, monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    for i in range(3):
        db.save_reviewed_post(f"h{i}", f"http://x.com/{i}", f"T{i}", {"slides": []}, 7.0)
    page1 = db.get_review_queue(limit=2, offset=0)
    page2 = db.get_review_queue(limit=2, offset=2)
    assert len(page1) == 2
    assert len(page2) == 1


def test_get_review_queue_excludes_approved(tmp_path, monkeypatch):
    import src.db as db
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    db.init_db()
    db.save_reviewed_post("h1", "http://x.com/1", "T1", {"slides": []}, 7.5)
    db.save_reviewed_post("h2", "http://x.com/2", "T2", {"slides": []}, 7.5)
    post_id = db.get_pending_posts()[0]["id"]
    db.approve_post(post_id)
    queue = db.get_review_queue()
    assert len(queue) == 1
    assert queue[0]["status"] == "pending_review"


# ── Web route tests ────────────────────────────────────────────────────────────

AUTH = ("admin", "testpass")


def test_health_returns_ok_without_auth(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_dashboard_requires_auth(client):
    resp = client.get("/")
    assert resp.status_code == 401


def test_dashboard_accessible_with_auth(client):
    resp = client.get("/", auth=AUTH)
    assert resp.status_code == 200


def test_dashboard_shows_pending_count(client, seed_post):
    resp = client.get("/", auth=AUTH)
    assert resp.status_code == 200
    assert "1 post waiting" in resp.text


def test_dashboard_shows_zero_counts_when_empty(client):
    resp = client.get("/", auth=AUTH)
    assert resp.status_code == 200
    assert "No posts pending review." in resp.text


def test_dashboard_cta_links_to_review(client, seed_post):
    resp = client.get("/", auth=AUTH)
    assert "/review" in resp.text


def test_review_queue_returns_200(client):
    resp = client.get("/review", auth=AUTH)
    assert resp.status_code == 200


def test_review_queue_shows_pending_post(client, seed_post):
    resp = client.get("/review", auth=AUTH)
    assert resp.status_code == 200
    assert "Test Article" in resp.text


def test_review_queue_empty_state(client):
    resp = client.get("/review", auth=AUTH)
    assert resp.status_code == 200
    assert "No posts pending review" in resp.text


def test_review_queue_pagination(client, tmp_db):
    import src.db as db
    for i in range(5):
        db.save_reviewed_post(
            f"hash{i}", f"http://x.com/{i}", f"Article {i}",
            {"slides": [{"caption": f"Caption {i}"}], "brand_domain": "test.com"},
            7.0,
        )
    resp = client.get("/review?page=1&per_page=3", auth=AUTH)
    assert resp.status_code == 200
    assert "Next" in resp.text  # has next page link

    resp2 = client.get("/review?page=2&per_page=3", auth=AUTH)
    assert resp2.status_code == 200
    assert "Previous" in resp2.text  # has previous page link


def test_review_queue_requires_auth(client):
    resp = client.get("/review")
    assert resp.status_code == 401


def test_review_queue_card_links_to_post_detail(client, seed_post):
    """Queue card title/thumbnail must wrap an <a href="/posts/{id}">."""
    resp = client.get("/review", auth=AUTH)
    assert resp.status_code == 200
    assert f'href="/posts/{seed_post}"' in resp.text


def test_approve_post_returns_empty_200(client, seed_post):
    resp = client.post(f"/review/{seed_post}/approve", auth=AUTH)
    assert resp.status_code == 200
    assert resp.content == b""


def test_approve_post_updates_db(client, seed_post):
    import src.db as db_module
    client.post(f"/review/{seed_post}/approve", auth=AUTH)
    post = db_module.get_post(seed_post)
    assert post["status"] == "approved"


def test_approve_post_twice_returns_409(client, seed_post):
    client.post(f"/review/{seed_post}/approve", auth=AUTH)
    resp = client.post(f"/review/{seed_post}/approve", auth=AUTH)
    assert resp.status_code == 409


def test_approve_nonexistent_post_returns_404(client):
    resp = client.post("/review/99999/approve", auth=AUTH)
    assert resp.status_code == 404


def test_approve_requires_auth(client, seed_post):
    resp = client.post(f"/review/{seed_post}/approve")
    assert resp.status_code == 401


def test_reject_panel_returns_html(client, seed_post):
    resp = client.get(f"/review/{seed_post}/reject-panel", auth=AUTH)
    assert resp.status_code == 200
    assert "textarea" in resp.text


def test_reject_panel_clear_returns_empty(client, seed_post):
    resp = client.get(f"/review/{seed_post}/reject-panel-clear", auth=AUTH)
    assert resp.status_code == 200
    assert resp.content == b""


def test_reject_post_success(client, seed_post):
    import src.db as db_module
    resp = client.post(
        f"/review/{seed_post}/reject",
        data={"reason": "Caption too vague, no concrete numbers or context"},
        auth=AUTH,
    )
    assert resp.status_code == 200
    assert resp.headers.get("HX-Retarget") == f"#post-{seed_post}"
    assert resp.headers.get("HX-Reswap") == "outerHTML"
    assert resp.content == b""
    post = db_module.get_post(seed_post)
    assert post["status"] == "rejected"
    assert "vague" in post["rejection_reason"]


def test_reject_post_short_reason_shows_error(client, seed_post):
    import src.db as db_module
    resp = client.post(
        f"/review/{seed_post}/reject",
        data={"reason": "bad"},
        auth=AUTH,
    )
    assert resp.status_code == 200
    assert "at least 10 characters" in resp.text
    post = db_module.get_post(seed_post)
    assert post["status"] == "pending_review"  # DB unchanged


def test_reject_post_twice_returns_409(client, seed_post):
    reason = "Caption too vague, no concrete numbers or context"
    client.post(f"/review/{seed_post}/reject", data={"reason": reason}, auth=AUTH)
    resp = client.post(f"/review/{seed_post}/reject", data={"reason": reason}, auth=AUTH)
    assert resp.status_code == 409


def test_reject_nonexistent_post_returns_404(client):
    resp = client.post(
        "/review/99999/reject",
        data={"reason": "Caption too vague, no concrete numbers"},
        auth=AUTH,
    )
    assert resp.status_code == 404


def test_reject_requires_auth(client, seed_post):
    resp = client.post(
        f"/review/{seed_post}/reject",
        data={"reason": "Caption too vague, no concrete numbers"},
    )
    assert resp.status_code == 401


# ── DB admin helper tests ────────────────────────────────────────────────────

def test_get_tables_returns_known_tables(tmp_db):
    import src.db as db_module
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    from src.main import _get_tables
    tables = _get_tables(conn)
    assert "sources" in tables
    assert "generated_posts" in tables
    assert "crashed_sources" in tables
    conn.close()


def test_get_columns_returns_column_dicts(tmp_db):
    import sqlite3
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    from src.main import _get_columns
    cols = _get_columns(conn, "sources")
    names = [c["name"] for c in cols]
    assert "id" in names
    assert "key" in names
    assert "url" in names
    conn.close()


def test_validate_table_raises_404_for_unknown(tmp_db):
    import sqlite3
    from src.main import _validate_table
    import pytest
    from fastapi import HTTPException
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    with pytest.raises(HTTPException) as exc_info:
        _validate_table(conn, "nonexistent_table")
    assert exc_info.value.status_code == 404
    conn.close()


def test_database_index_requires_auth(client):
    resp = client.get("/database")
    assert resp.status_code == 401


def test_database_index_returns_200(client):
    resp = client.get("/database", auth=AUTH)
    assert resp.status_code == 200


def test_database_index_lists_tables(client):
    resp = client.get("/database", auth=AUTH)
    assert "sources" in resp.text
    assert "generated_posts" in resp.text
    assert "crashed_sources" in resp.text


def test_database_table_view_returns_200(client, seed_source):
    resp = client.get("/database/sources", auth=AUTH)
    assert resp.status_code == 200


def test_database_table_view_shows_row_data(client, seed_source):
    resp = client.get("/database/sources", auth=AUTH)
    assert "techcrunch" in resp.text


def test_database_table_unknown_returns_404(client):
    resp = client.get("/database/nonexistent_table", auth=AUTH)
    assert resp.status_code == 404


def test_database_table_sql_injection_returns_404(client):
    resp = client.get("/database/sources%3BDROP%20TABLE%20sources", auth=AUTH)
    assert resp.status_code == 404


def test_database_table_pagination(client, tmp_db):
    import src.db as db_module
    for i in range(55):
        db_module.save_source(
            f"source_{i}",
            f"https://example{i}.com",
            {"method": "rss", "feed_url": f"https://example{i}.com/feed"},
        )
    resp = client.get("/database/sources?page=1", auth=AUTH)
    assert resp.status_code == 200
    assert "Next" in resp.text

    resp2 = client.get("/database/sources?page=2", auth=AUTH)
    assert resp2.status_code == 200
    assert "Previous" in resp2.text


def test_db_edit_panel_returns_form(client, seed_source):
    resp = client.get(f"/database/sources/{seed_source}/panel", auth=AUTH)
    assert resp.status_code == 200
    assert "<form" in resp.text
    assert "techcrunch" in resp.text  # row value present in form


def test_db_edit_panel_unknown_row_returns_404(client):
    resp = client.get("/database/sources/99999/panel", auth=AUTH)
    assert resp.status_code == 404


def test_db_create_panel_returns_empty_form(client):
    resp = client.get("/database/sources/create-panel", auth=AUTH)
    assert resp.status_code == 200
    assert "<form" in resp.text


def test_db_row_fragment_returns_tr(client, seed_source):
    resp = client.get(f"/database/sources/{seed_source}/row", auth=AUTH)
    assert resp.status_code == 200
    assert f'id="db-row-{seed_source}"' in resp.text


def test_db_update_row_returns_tr_fragment(client, seed_source):
    resp = client.put(
        f"/database/sources/{seed_source}",
        data={"key": "tc_updated", "url": "https://techcrunch.com", "method": "rss"},
        auth=AUTH,
    )
    assert resp.status_code == 200
    assert f'id="db-row-{seed_source}"' in resp.text
    assert resp.headers.get("HX-Retarget") == f"#db-row-{seed_source}"
    assert resp.headers.get("HX-Reswap") == "outerHTML"


def test_db_update_row_persists_to_db(client, seed_source):
    import src.db as db_module
    client.put(
        f"/database/sources/{seed_source}",
        data={"key": "tc_updated", "url": "https://techcrunch.com", "method": "rss"},
        auth=AUTH,
    )
    with db_module.get_conn() as conn:
        row = conn.execute("SELECT key FROM sources WHERE id = ?", (seed_source,)).fetchone()
    assert row["key"] == "tc_updated"


def test_db_update_unknown_column_returns_422(client, seed_source):
    resp = client.put(
        f"/database/sources/{seed_source}",
        data={"nonexistent_col": "value"},
        auth=AUTH,
    )
    assert resp.status_code == 422


def test_db_update_unknown_row_returns_404(client):
    resp = client.put(
        "/database/sources/99999",
        data={"key": "x", "url": "https://x.com", "method": "rss"},
        auth=AUTH,
    )
    assert resp.status_code == 404


def test_db_update_writes_audit_log(client, seed_source):
    client.put(
        f"/database/sources/{seed_source}",
        data={"key": "tc_audited", "url": "https://techcrunch.com", "method": "rss"},
        auth=AUTH,
    )
    import pathlib
    log_path = pathlib.Path("logs") / "db_audit.log"
    assert log_path.exists()
    content = log_path.read_text()
    assert "UPDATE" in content
    assert "sources" in content
    assert str(seed_source) in content


def test_db_create_row_returns_tr_fragment(client):
    resp = client.post(
        "/database/sources",
        data={"key": "wired", "url": "https://wired.com", "method": "rss"},
        auth=AUTH,
    )
    assert resp.status_code == 200
    assert 'id="db-row-' in resp.text
    assert resp.headers.get("HX-Retarget") == "#db-tbody"
    assert resp.headers.get("HX-Reswap") == "afterbegin"


def test_db_create_row_persists_to_db(client):
    import src.db as db_module
    client.post(
        "/database/sources",
        data={"key": "wired", "url": "https://wired.com", "method": "rss"},
        auth=AUTH,
    )
    with db_module.get_conn() as conn:
        row = conn.execute("SELECT key FROM sources WHERE key = 'wired'").fetchone()
    assert row is not None
    assert row["key"] == "wired"


def test_db_create_row_duplicate_returns_409(client, seed_source):
    resp = client.post(
        "/database/sources",
        data={"key": "techcrunch", "url": "https://techcrunch.com", "method": "rss"},
        auth=AUTH,
    )
    assert resp.status_code == 409


def test_db_create_row_writes_audit_log(client):
    client.post(
        "/database/sources",
        data={"key": "verge", "url": "https://theverge.com", "method": "rss"},
        auth=AUTH,
    )
    import pathlib
    log_path = pathlib.Path("logs") / "db_audit.log"
    content = log_path.read_text()
    assert "INSERT" in content
    assert "sources" in content
