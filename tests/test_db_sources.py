def test_sources_has_is_active_column(tmp_db):
    import src.db as db
    with db.get_conn() as conn:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(sources)")}
    assert "is_active" in cols
    assert "last_article_count" in cols


def test_existing_sources_default_to_active(tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {
        "method": "rss",
        "feed_url": "https://news.ycombinator.com/rss",
    })
    with db.get_conn() as conn:
        row = conn.execute("SELECT is_active FROM sources WHERE key='hn'").fetchone()
    assert row["is_active"] == 1


def test_migration_is_idempotent(tmp_db):
    import src.db as db
    db.migrate_sources_columns()  # second call — should not error
    db.migrate_sources_columns()  # third


def test_set_source_active_toggles(tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {
        "method": "rss", "feed_url": "https://news.ycombinator.com/rss",
    })
    assert db.set_source_active("hn", False) is True
    with db.get_conn() as conn:
        row = conn.execute("SELECT is_active FROM sources WHERE key='hn'").fetchone()
    assert row["is_active"] == 0
    assert db.set_source_active("hn", True) is True


def test_set_source_active_returns_false_for_unknown(tmp_db):
    import src.db as db
    assert db.set_source_active("nope", False) is False


def test_update_source_config(tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {
        "method": "rss", "feed_url": "https://news.ycombinator.com/rss",
    })
    ok = db.update_source_config("hn", {"feed_url": "https://news.ycombinator.com/rss.xml"})
    assert ok is True
    src = db.get_source("hn")
    assert src["feed_url"] == "https://news.ycombinator.com/rss.xml"


def test_update_source_config_ignores_unknown_fields(tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss"})
    # Should silently drop "key" — key edits not allowed (spec non-goal)
    db.update_source_config("hn", {"key": "renamed", "feed_url": "x"})
    src = db.get_source("hn")
    assert src["key"] == "hn"
    assert src["feed_url"] == "x"


def test_delete_source(tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss"})
    assert db.delete_source("hn") is True
    assert db.get_source("hn") is None
    assert db.delete_source("hn") is False  # already gone


def test_delete_crashed_source(tmp_db):
    import src.db as db
    db.save_source("flaky", "https://flaky.example", {"method": "rss"})
    db.mark_source_crashed("flaky", "BoomError: blew up")
    assert db.delete_crashed_source("flaky") is True
    with db.get_conn() as conn:
        row = conn.execute("SELECT 1 FROM crashed_sources WHERE key='flaky'").fetchone()
    assert row is None


def test_set_source_article_count(tmp_db):
    import src.db as db
    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss"})
    db.set_source_article_count("hn", 7)
    src = db.get_source("hn")
    assert src["last_article_count"] == 7


def test_list_sources_with_health_merges_active_paused_crashed(tmp_db):
    import src.db as db
    db.save_source("active",  "https://a.example", {"method": "rss"})
    db.save_source("paused",  "https://p.example", {"method": "rss"})
    db.save_source("flaky",   "https://f.example", {"method": "rss"})
    db.set_source_active("paused", False)
    db.mark_source_crashed("flaky", "BoomError: blew up")

    rows = db.list_sources_with_health()
    by_key = {r["key"]: r for r in rows}
    assert by_key["active"]["status"] == "active"
    assert by_key["paused"]["status"] == "paused"
    assert by_key["flaky"]["status"] == "crashed"
    assert by_key["flaky"]["error_msg"] == "BoomError: blew up"
    assert by_key["active"]["error_msg"] is None
