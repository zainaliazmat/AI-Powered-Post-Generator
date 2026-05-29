import pytest


def test_unsafe_url_yields_single_done_with_null_config(monkeypatch):
    from src.discovery import stream_discover
    events = list(stream_discover("http://localhost:8080/feed"))
    assert len(events) == 1
    assert events[0]["type"] == "done"
    assert events[0]["config"] is None
    assert "private" in events[0]["detail"].lower() or "local" in events[0]["detail"].lower()


def test_unsafe_link_local(monkeypatch):
    from src.discovery import stream_discover
    events = list(stream_discover("http://169.254.169.254/latest/meta-data"))
    assert events[-1]["type"] == "done"
    assert events[-1]["config"] is None


def test_invalid_scheme_yields_done_null(monkeypatch):
    from src.discovery import stream_discover
    events = list(stream_discover("file:///etc/passwd"))
    assert events[-1]["type"] == "done"
    assert events[-1]["config"] is None


def test_success_on_rss_tier(monkeypatch):
    """If probe_rss returns a config, stream stops there and emits done with config."""
    import src.discovery as disc

    monkeypatch.setattr(disc, "probe_rss",
        lambda url: {"method": "rss", "feed_url": "https://example.com/rss"})
    monkeypatch.setattr(disc, "_rss_candidates",
        lambda url: ["https://example.com/rss"])
    monkeypatch.setattr(disc, "probe_rsshub",   lambda url: None)
    monkeypatch.setattr(disc, "probe_html",     lambda url: None)
    monkeypatch.setattr(disc, "probe_playwright", lambda url: None)
    # Fixture sample-title fetch
    monkeypatch.setattr(disc, "_sample_titles_for",
        lambda config: ["Title A", "Title B", "Title C"])
    # Real fetch_rss would hit the network; stub it
    monkeypatch.setattr(disc, "fetch_rss", lambda u: [{"title": "ok"}])

    events = list(disc.stream_discover("https://example.com"))
    types = [e["type"] for e in events]
    assert "tier_start" in types
    assert "tier_end" in types
    done = events[-1]
    assert done["type"] == "done"
    assert done["config"] == {"method": "rss", "feed_url": "https://example.com/rss"}
    assert done["sample_titles"] == ["Title A", "Title B", "Title C"]
    # Later tiers were not started
    tier_starts = [e for e in events if e["type"] == "tier_start"]
    assert tier_starts[0]["tier"] == "rss"


def test_all_tiers_fail_yields_done_null(monkeypatch):
    import src.discovery as disc
    monkeypatch.setattr(disc, "probe_rss",         lambda url: None)
    monkeypatch.setattr(disc, "probe_rsshub",      lambda url: None)
    monkeypatch.setattr(disc, "probe_html",        lambda url: None)
    monkeypatch.setattr(disc, "probe_playwright",  lambda url: None)
    monkeypatch.setattr(disc, "_rss_candidates",   lambda url: ["https://example.com/rss"])
    monkeypatch.setattr(disc, "fetch_rss",         lambda u: [])

    events = list(disc.stream_discover("https://example.com"))
    assert events[-1]["type"] == "done"
    assert events[-1]["config"] is None


def test_discover_method_shim_still_returns_config(monkeypatch):
    """CLI uses discover_method; it must keep returning a config dict or None."""
    import src.discovery as disc
    monkeypatch.setattr(disc, "probe_rss",
        lambda url: {"method": "rss", "feed_url": "https://example.com/rss"})
    monkeypatch.setattr(disc, "_rss_candidates",
        lambda url: ["https://example.com/rss"])
    monkeypatch.setattr(disc, "fetch_rss", lambda u: [{"title": "ok"}])
    monkeypatch.setattr(disc, "_sample_titles_for", lambda c: [])
    result = disc.discover_method("https://example.com")
    assert result == {"method": "rss", "feed_url": "https://example.com/rss"}


def test_discover_method_shim_unsafe_url_returns_none(monkeypatch):
    from src.discovery import discover_method
    assert discover_method("http://127.0.0.1/feed") is None


def test_done_event_includes_url_and_derived_key_on_success(monkeypatch):
    """UI auto-fills the Save form from the done event — it must carry url + key."""
    import src.discovery as disc

    monkeypatch.setattr(disc, "probe_rss",
        lambda url: {"method": "rss", "feed_url": "https://feed.infoq.com/news/"})
    monkeypatch.setattr(disc, "_rss_candidates",
        lambda url: ["https://feed.infoq.com/news/"])
    monkeypatch.setattr(disc, "probe_rsshub",     lambda url: None)
    monkeypatch.setattr(disc, "probe_html",       lambda url: None)
    monkeypatch.setattr(disc, "probe_playwright", lambda url: None)
    monkeypatch.setattr(disc, "_sample_titles_for", lambda c: [])
    monkeypatch.setattr(disc, "fetch_rss",        lambda u: [{"title": "ok"}])

    events = list(disc.stream_discover("https://www.infoq.com/news/"))
    done = events[-1]
    assert done["type"] == "done"
    assert done["url"] == "https://www.infoq.com/news/"
    assert done["key"] == "infoq"


def test_done_event_echoes_url_and_key_even_when_all_tiers_fail(monkeypatch):
    """Manual-add fallback also pre-fills, so URL/key must be echoed on failure too."""
    import src.discovery as disc
    monkeypatch.setattr(disc, "probe_rss",         lambda url: None)
    monkeypatch.setattr(disc, "probe_rsshub",      lambda url: None)
    monkeypatch.setattr(disc, "probe_html",        lambda url: None)
    monkeypatch.setattr(disc, "probe_playwright",  lambda url: None)
    monkeypatch.setattr(disc, "_rss_candidates",   lambda url: [])

    events = list(disc.stream_discover("https://example.com/news"))
    done = events[-1]
    assert done["type"] == "done"
    assert done["config"] is None
    assert done["url"] == "https://example.com/news"
    assert done["key"] == "example"
