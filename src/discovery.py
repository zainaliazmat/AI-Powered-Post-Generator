"""
Source discovery: given a URL, probe all 4 tiers to find the best fetch method.
Returns a config dict that can be passed to db.save_source().
"""

import logging
import re
from typing import Iterator, Literal, TypedDict
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .shared import _is_safe_url, extract_articles, fetch_html, fetch_rss, get_playwright_html, parse_rss_entries

logger = logging.getLogger(__name__)

RSSHUB_BASES = ["https://rsshub.app", "https://rss.shab.fun"]

# Paths to probe for native RSS feeds
_RSS_PATHS = ["/feed", "/rss", "/feed.xml", "/atom.xml", "/rss.xml", "/feed/rss", "/rss/feed"]

# Known RSSHub slugs for common domains
_RSSHUB_SLUGS: dict[str, str] = {
    "news.ycombinator.com": "hackernews/news",
    "ycombinator.com":      "hackernews/news",
    "infoq.com":            "infoq/articles",
    "openai.com":           "openai/blog",
    "sdtimes.com":          "sdtimes",
    "techcrunch.com":       "techcrunch",
    "arstechnica.com":      "arstechnica",
    "theverge.com":         "theverge",
    "wired.com":            "wired",
}

# Key aliases: maps domain fragment → short slug
_KEY_ALIASES: dict[str, str] = {
    "ycombinator": "hn",
    "google":      "google_news",
}


def url_to_key(url: str) -> str:
    """Derive a short source key from a URL. e.g. https://news.ycombinator.com → 'hn'"""
    host = urlparse(url).netloc.lower()
    # Strip common prefixes
    for prefix in ("www.", "news.", "blog.", "feeds."):
        if host.startswith(prefix):
            host = host[len(prefix):]
    # Take the registrable domain part (first component before TLD)
    slug = host.split(".")[0]
    slug = re.sub(r"[^a-z0-9_]", "_", slug)
    return _KEY_ALIASES.get(slug, slug)


def _rss_links_from_html(soup: BeautifulSoup, base: str) -> list[str]:
    """Return RSS/Atom URLs declared in HTML <head> <link> tags."""
    urls = []
    for tag in soup.find_all("link", rel="alternate"):
        t = tag.get("type", "")
        if "rss" in t or "atom" in t:
            href = tag.get("href", "")
            if href.startswith("/"):
                href = base.rstrip("/") + href
            if href:
                urls.append(href)
    return urls


def probe_rss(url: str) -> dict | None:
    """Tier 1: try the URL itself and common RSS paths. Stops at first feed with entries."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    # Directory of the given URL — e.g. https://openai.com/news/ → https://openai.com/news
    url_dir = url.rstrip("/")

    # Start with the URL itself in case a feed URL was passed directly
    candidates = [url]

    # Look for <link rel="alternate" type="application/rss+xml"> in the page HTML
    soup = fetch_html(url)
    if soup:
        candidates += _rss_links_from_html(soup, base)

    # Probe common paths relative to the URL's subdirectory FIRST,
    # then relative to the root domain.
    # This ensures https://openai.com/news/rss.xml is tried before
    # https://openai.com/rss.xml.
    for path in _RSS_PATHS:
        candidates.append(url_dir + path)   # e.g. /news/rss.xml
    for path in _RSS_PATHS:
        candidates.append(base + path)      # e.g. /rss.xml

    seen: set[str] = set()
    for feed_url in candidates:
        if feed_url in seen:
            continue
        seen.add(feed_url)
        entries = fetch_rss(feed_url)
        if entries:
            logger.info("[Discovery/RSS] Found feed: %s", feed_url)
            return {"method": "rss", "feed_url": feed_url}

    return None


def probe_rsshub(url: str) -> dict | None:
    """Tier 2: try known + guessed RSSHub slugs on public instances."""
    host = urlparse(url).netloc.lower()
    for prefix in ("www.", "news.", "blog."):
        if host.startswith(prefix):
            host = host[len(prefix):]

    # Known slug first, then guess from domain
    slug = None
    for domain, s in _RSSHUB_SLUGS.items():
        if domain in host:
            slug = s
            break
    if not slug:
        slug = host.split(".")[0]

    for base in RSSHUB_BASES:
        feed_url = f"{base}/{slug}"
        entries = fetch_rss(feed_url)
        if entries:
            logger.info("[Discovery/RSSHub] Found feed: %s", feed_url)
            return {"method": "rsshub", "feed_url": feed_url, "rsshub_slug": slug}

    return None


def probe_html(url: str) -> dict | None:
    """Tier 3: generic HTML scraping with requests."""
    soup = fetch_html(url)
    if not soup:
        return None
    articles = extract_articles(soup, url)
    if articles:
        logger.info("[Discovery/HTML] Found %d articles at %s", len(articles), url)
        return {"method": "html"}
    return None


def probe_playwright(url: str) -> dict | None:
    """Tier 4: headless Chromium for JS-rendered pages."""
    wait_for = "article, h2, h3, h4, a"
    html = get_playwright_html(url, wait_for=wait_for)
    if not html:
        return None
    articles = extract_articles(BeautifulSoup(html, "lxml"), url)
    if articles:
        logger.info("[Discovery/Playwright] Found %d articles at %s", len(articles), url)
        return {"method": "playwright", "pw_wait_for": wait_for}
    return None


class DiscoveryEvent(TypedDict, total=False):
    type: Literal["tier_start", "probe", "tier_end", "done", "error"]
    tier: Literal["rss", "rsshub", "html", "playwright"]
    url: str
    key: str
    ok: bool
    detail: str
    config: dict
    sample_titles: list[str]


def _rss_candidates(url: str) -> list[str]:
    """Return the candidate URLs probe_rss would try, in order.
    Mirrors the candidate-list construction at the top of probe_rss."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    url_dir = url.rstrip("/")
    candidates = [url]
    soup = fetch_html(url)
    if soup:
        candidates.extend(_rss_links_from_html(soup, base))
    for path in _RSS_PATHS:
        candidates.append(url_dir + path)
        if url_dir != base:
            candidates.append(base + path)
    # Dedup while preserving order
    seen, out = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _sample_titles_for(config: dict) -> list[str]:
    """Fetch up to 5 sample article titles for a successful config (best-effort)."""
    try:
        if config.get("method") in ("rss", "rsshub"):
            entries = fetch_rss(config["feed_url"])
            return [e.get("title", "")[:120] for e in entries[:5] if e.get("title")]
        if config.get("method") == "html":
            soup = fetch_html(config.get("url") or config.get("feed_url") or "")
            if soup:
                arts = extract_articles(soup, "", "preview")
                return [a.get("title", "")[:120] for a in arts[:5] if a.get("title")]
    except Exception as e:
        logger.warning("sample_titles failed: %s", e)
    return []


def _probe_rss_stream(url: str) -> Iterator[DiscoveryEvent]:
    """Yield a `probe` event per candidate, then a `tier_end`."""
    yield {"type": "tier_start", "tier": "rss"}
    winning: dict | None = None
    for candidate in _rss_candidates(url):
        entries = []
        try:
            entries = fetch_rss(candidate)
        except Exception as e:
            yield {"type": "probe", "tier": "rss", "url": candidate, "ok": False,
                   "detail": f"{type(e).__name__}: {e}"}
            continue
        if entries:
            yield {"type": "probe", "tier": "rss", "url": candidate, "ok": True,
                   "detail": f"{len(entries)} entries"}
            winning = {"method": "rss", "feed_url": candidate}
            break
        yield {"type": "probe", "tier": "rss", "url": candidate, "ok": False,
               "detail": "no entries"}
    yield {"type": "tier_end", "tier": "rss",
           "ok": winning is not None,
           "detail": "" if winning else "no candidate matched",
           **({"config": winning} if winning else {})}


def _probe_rsshub_stream(url: str) -> Iterator[DiscoveryEvent]:
    yield {"type": "tier_start", "tier": "rsshub"}
    config = probe_rsshub(url)
    if config:
        yield {"type": "probe", "tier": "rsshub", "url": config.get("feed_url", ""),
               "ok": True, "detail": "matched"}
    yield {"type": "tier_end", "tier": "rsshub",
           "ok": config is not None,
           "detail": "" if config else "no rsshub mapping",
           **({"config": config} if config else {})}


def _probe_html_stream(url: str) -> Iterator[DiscoveryEvent]:
    yield {"type": "tier_start", "tier": "html"}
    config = probe_html(url)
    yield {"type": "probe", "tier": "html", "url": url,
           "ok": config is not None,
           "detail": f"extracted {config.get('article_count', 0)} articles" if config else "no articles extracted"}
    yield {"type": "tier_end", "tier": "html",
           "ok": config is not None,
           **({"config": config} if config else {})}


def _probe_playwright_stream(url: str) -> Iterator[DiscoveryEvent]:
    yield {"type": "tier_start", "tier": "playwright"}
    try:
        config = probe_playwright(url)
    except Exception as e:
        yield {"type": "probe", "tier": "playwright", "url": url, "ok": False,
               "detail": f"{type(e).__name__}: {e}"}
        config = None
    else:
        yield {"type": "probe", "tier": "playwright", "url": url,
               "ok": config is not None,
               "detail": "rendered + extracted" if config else "extracted nothing"}
    yield {"type": "tier_end", "tier": "playwright",
           "ok": config is not None,
           **({"config": config} if config else {})}


_TIER_STREAMS = (
    ("rss", _probe_rss_stream),
    ("rsshub", _probe_rsshub_stream),
    ("html", _probe_html_stream),
    ("playwright", _probe_playwright_stream),
)


def stream_discover(url: str) -> Iterator[DiscoveryEvent]:
    """Probe URL through 4 tiers, yielding events. Always ends with a single `done`.

    The `done` event echoes back `url` and a suggested `key` (derived via
    `url_to_key`) so the UI can auto-fill the Save-source form.
    """
    suggested_key = url_to_key(url)

    if not _is_safe_url(url):
        yield {"type": "done", "config": None,
               "detail": "URL points to a private or local address.",
               "url": url, "key": suggested_key}
        return

    final_config: dict | None = None
    used_tier: str | None = None

    for tier, stream_fn in _TIER_STREAMS:
        if final_config is not None:
            yield {"type": "tier_end", "tier": tier, "ok": False, "skipped": True,
                   "detail": f"skipped — {used_tier} won"}
            continue
        for evt in stream_fn(url):
            if evt["type"] == "tier_end" and evt.get("config"):
                final_config = evt["config"]
                used_tier = tier
            yield evt

    sample_titles = _sample_titles_for(final_config) if final_config else []
    yield {
        "type": "done",
        "config": final_config,
        "sample_titles": sample_titles,
        "detail": f"matched on tier '{used_tier}'" if used_tier else "all tiers failed",
        "url": url,
        "key": suggested_key,
    }


def discover_method(url: str) -> dict | None:
    """Legacy shim — drains stream_discover and returns the final config (or None)."""
    final_config: dict | None = None
    for evt in stream_discover(url):
        if evt["type"] == "done":
            final_config = evt.get("config")
    return final_config
