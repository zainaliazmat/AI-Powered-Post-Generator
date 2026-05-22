"""
Source discovery: given a URL, probe all 4 tiers to find the best fetch method.
Returns a config dict that can be passed to db.save_source().
"""

import logging
import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from .shared import extract_articles, fetch_html, fetch_rss, get_playwright_html, parse_rss_entries

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


def discover_method(url: str) -> dict | None:
    """
    Probe a URL through all 4 tiers. Returns the first config that works, or None.
    Does NOT write to the database — that's the caller's job.
    """
    logger.info("[Discovery] Starting probe for %s", url)
    for probe in (probe_rss, probe_rsshub, probe_html, probe_playwright):
        result = probe(url)
        if result:
            return result
    logger.error("[Discovery] All tiers failed for %s", url)
    return None
