import base64


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"admin:testpass").decode()}


def test_sources_page_renders(client, tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss", "feed_url": "x"})
    r = client.get("/sources", headers=_auth())
    assert r.status_code == 200
    assert "hn" in r.text


def test_sources_list_fragment_returns_only_table_body(client, tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss", "feed_url": "x"})
    r = client.get("/sources/list", headers=_auth())
    assert r.status_code == 200
    # Fragment must not include the full <html> shell
    assert "<html" not in r.text.lower()
    assert "hn" in r.text


def test_sources_list_filter_by_status(client, tmp_db):
    import src.db as db
    db.save_source("a", "https://a.example", {"method": "rss"})
    db.save_source("p", "https://p.example", {"method": "rss"})
    db.set_source_active("p", False)

    r = client.get("/sources/list?status=paused", headers=_auth())
    assert r.status_code == 200
    assert 'id="source-p"' in r.text
    assert 'id="source-a"' not in r.text


def test_sources_page_requires_auth(client, tmp_db):
    r = client.get("/sources")  # no auth header
    assert r.status_code == 401


def test_post_source_saves_manual_entry(client, tmp_db):
    payload = {"key": "manual", "url": "https://example.com/news",
               "config": {"method": "rss", "feed_url": "https://example.com/rss"}}
    r = client.post("/sources", json=payload, headers=_auth())
    assert r.status_code == 201
    import src.db as db
    assert db.get_source("manual") is not None


def test_post_source_returns_409_on_duplicate_key(client, tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss"})
    payload = {"key": "hn", "url": "https://news.ycombinator.com",
               "config": {"method": "rss"}}
    r = client.post("/sources", json=payload, headers=_auth())
    assert r.status_code == 409


def test_put_source_updates_config(client, tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com",
                   {"method": "rss", "feed_url": "https://old"})
    r = client.put("/sources/hn",
                   json={"feed_url": "https://new"},
                   headers=_auth())
    assert r.status_code == 200
    assert db.get_source("hn")["feed_url"] == "https://new"


def test_put_source_404(client, tmp_db):
    r = client.put("/sources/nope", json={"feed_url": "x"}, headers=_auth())
    assert r.status_code == 404


def test_delete_source(client, tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss"})
    r = client.delete("/sources/hn", headers=_auth())
    assert r.status_code == 200
    assert db.get_source("hn") is None


def test_toggle_active(client, tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss"})
    r = client.post("/sources/hn/toggle-active", headers=_auth())
    assert r.status_code == 200
    with db.get_conn() as conn:
        row = conn.execute("SELECT is_active FROM sources WHERE key='hn'").fetchone()
    assert row["is_active"] == 0
    # Toggle back
    client.post("/sources/hn/toggle-active", headers=_auth())
    with db.get_conn() as conn:
        row = conn.execute("SELECT is_active FROM sources WHERE key='hn'").fetchone()
    assert row["is_active"] == 1


def test_restore_crashed_source(client, tmp_db):
    import src.db as db
    db.save_source("flaky", "https://flaky.example", {"method": "rss"})
    db.mark_source_crashed("flaky", "Boom")
    r = client.post("/sources/flaky/restore", headers=_auth())
    assert r.status_code == 200
    assert db.get_source("flaky") is not None


def test_delete_crashed_source(client, tmp_db):
    import src.db as db
    db.save_source("flaky", "https://flaky.example", {"method": "rss"})
    db.mark_source_crashed("flaky", "Boom")
    r = client.delete("/sources/crashed/flaky", headers=_auth())
    assert r.status_code == 200


def test_bulk_delete(client, tmp_db):
    import src.db as db
    db.save_source("a", "https://a.example", {"method": "rss"})
    db.save_source("b", "https://b.example", {"method": "rss"})
    db.save_source("c", "https://c.example", {"method": "rss"})
    r = client.post("/sources/bulk-delete", json={"keys": ["a", "b"]}, headers=_auth())
    assert r.status_code == 200
    assert db.get_source("a") is None
    assert db.get_source("b") is None
    assert db.get_source("c") is not None


def test_test_fetch_does_not_update_last_fetched(client, tmp_db, monkeypatch):
    import src.db as db
    import src.main as main
    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss", "feed_url": "x"})
    # Capture last_fetched before
    before = db.get_source("hn")["last_fetched"]

    monkeypatch.setattr(main, "_test_fetch_articles",
        lambda key: [{"title": "Sample article", "url": "u", "date": "2026-05-25T00:00:00Z"}])

    r = client.post("/sources/hn/test-fetch", headers=_auth())
    assert r.status_code == 200
    after = db.get_source("hn")["last_fetched"]
    assert before == after


def test_discover_sse_emits_events(client, tmp_db, monkeypatch):
    """SSE endpoint serialises stream_discover events as text/event-stream."""
    import src.main as main
    fake_events = [
        {"type": "tier_start", "tier": "rss"},
        {"type": "probe", "tier": "rss", "url": "https://example.com/rss",
         "ok": True, "detail": "5 entries"},
        {"type": "tier_end", "tier": "rss", "ok": True},
        {"type": "done", "config": {"method": "rss", "feed_url": "https://example.com/rss"},
         "sample_titles": ["A", "B"]},
    ]
    monkeypatch.setattr(main, "stream_discover", lambda url: iter(fake_events))

    with client.stream("GET", "/sources/discover?url=https://example.com", headers=_auth()) as r:
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("text/event-stream")
        body = b"".join(r.iter_bytes()).decode()
    assert "event: discovery" in body
    assert "event: done" in body
    # Final event has the config JSON inline
    assert '"method": "rss"' in body or '"method":"rss"' in body


def test_discover_sse_unsafe_url_short_circuits(client, tmp_db):
    """Real call with a localhost URL — should produce a single done event."""
    with client.stream("GET", "/sources/discover?url=http://127.0.0.1/feed", headers=_auth()) as r:
        body = b"".join(r.iter_bytes()).decode()
    assert "event: done" in body
    assert '"config": null' in body or '"config":null' in body


def test_reprobe_sse_uses_existing_url(client, tmp_db, monkeypatch):
    import src.db as db
    import src.main as main
    db.save_source("hn", "https://news.ycombinator.com",
                   {"method": "rss", "feed_url": "https://old"})

    captured: list[str] = []
    def fake_stream(url):
        captured.append(url)
        return iter([{"type": "done", "config": None, "detail": "done"}])
    monkeypatch.setattr(main, "stream_discover", fake_stream)

    with client.stream("GET", "/sources/hn/reprobe", headers=_auth()) as r:
        body = b"".join(r.iter_bytes()).decode()
    assert r.status_code == 200
    assert captured == ["https://news.ycombinator.com"]
    assert "event: done" in body
