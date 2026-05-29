def test_fetcher_skips_paused_sources(tmp_db, monkeypatch):
    import src.db as db
    import src.fetcher as fetcher

    db.save_source("active", "https://a.example", {"method": "rss", "feed_url": "https://a.example/rss"})
    db.save_source("paused", "https://p.example", {"method": "rss", "feed_url": "https://p.example/rss"})
    db.set_source_active("paused", False)

    fetched: list[str] = []
    def fake_fetch_source(key):
        fetched.append(key)
        return []
    monkeypatch.setattr(fetcher, "fetch_source", fake_fetch_source)

    fetcher.fetch_all()
    assert fetched == ["active"]


def test_fetcher_truncates_to_per_source_max(tmp_db, monkeypatch):
    import src.db as db
    import src.fetcher as fetcher
    from src.settings import save_settings

    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss", "feed_url": "x"})
    save_settings({"per_source_max": 3})

    def fake_fetch_source(key):
        return [{"title": f"a{i}", "date": "2026-05-25T00:00:00Z", "url": f"u{i}"} for i in range(10)]
    monkeypatch.setattr(fetcher, "fetch_source", fake_fetch_source)

    results, _ = fetcher.fetch_all()
    assert len(results["hn"]) == 3


def test_fetcher_records_article_count(tmp_db, monkeypatch):
    import src.db as db
    import src.fetcher as fetcher

    db.save_source("hn", "https://news.ycombinator.com", {"method": "rss", "feed_url": "x"})

    def fake_fetch_source(key):
        return [{"title": "a", "date": "2026-05-25T00:00:00Z", "url": "u"}] * 4
    monkeypatch.setattr(fetcher, "fetch_source", fake_fetch_source)

    fetcher.fetch_all()
    src = db.get_source("hn")
    assert src["last_article_count"] == 4


def test_is_within_hours_uses_settings_default(tmp_db, monkeypatch):
    """Calling is_within_hours with no hours argument uses settings.time_window_hours."""
    from src.settings import save_settings
    from src.shared import is_within_hours

    save_settings({"time_window_hours": 1})
    # 30min ago — inside 1h window
    import datetime as dt
    recent = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)).isoformat()
    old    = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)).isoformat()
    assert is_within_hours(recent) is True
    assert is_within_hours(old) is False
