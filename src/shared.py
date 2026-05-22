import logging
import random
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

from .models import Article

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "Referer":         "https://www.google.com/",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def polite_sleep(min_s: float = 2.0, max_s: float = 5.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def is_within_hours(date_str: str, hours: int = 18) -> bool | None:
    """
    True  = article is within the age window
    False = article is older than the window
    None  = date is missing or unparseable (caller keeps article and flags it)
    """
    if not date_str or not date_str.strip():
        return None
    iso = parse_date(date_str)
    if iso is None:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)
    except ValueError:
        return None


def parse_date(date_str: str) -> str | None:
    """Convert common date string formats to UTC ISO 8601. Returns None if unparseable."""
    if not date_str or not date_str.strip():
        return None
    s = date_str.strip()
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat()
        except ValueError:
            continue
    return None


def feedparser_date(entry) -> str:
    """Extract an ISO 8601 date string from a feedparser entry."""
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    if t:
        try:
            return datetime(*t[:6], tzinfo=timezone.utc).isoformat()
        except Exception:
            pass
    raw = entry.get("published", entry.get("updated", ""))
    return parse_date(raw) or raw


def fetch_html(url: str, retries: int = 3) -> BeautifulSoup | None:
    """GET a URL with exponential-backoff retries. Returns parsed soup or None."""
    delay = 1.0
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "lxml")
        except requests.HTTPError as e:
            logger.warning("[HTTP %s] %s (attempt %d/%d)", e.response.status_code, url, attempt, retries)
        except requests.RequestException as e:
            logger.warning("[Request error] %s — %s (attempt %d/%d)", url, e, attempt, retries)
        if attempt < retries:
            time.sleep(delay)
            delay *= 2
    return None


def fetch_rss(url: str) -> list:
    """Fetch and parse an RSS/Atom feed. Returns feedparser entry list (may be empty)."""
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": HEADERS["User-Agent"]})
        if feed.bozo and not feed.entries:
            logger.debug("[RSS] Empty/malformed feed: %s", url)
            return []
        logger.debug("[RSS] %d entries from %s", len(feed.entries), url)
        return feed.entries
    except Exception as e:
        logger.error("[RSS] Failed to fetch %s: %s", url, e)
        return []


def get_playwright_html(
    url: str,
    wait_for: str | None = None,
    locale: str = "en-US",
) -> str | None:
    """Launch headless Chromium and return the rendered page HTML."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error("[Playwright] Not installed. Run: pip install playwright && playwright install chromium")
        return None

    logger.info("[Playwright] Launching for: %s", url[:80])
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=HEADERS["User-Agent"],
                locale=locale,
                viewport={"width": 1280, "height": 800},
            )
            page = ctx.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)
            if wait_for:
                try:
                    page.wait_for_selector(wait_for, timeout=8000)
                except Exception:
                    pass
            html = page.content()
            browser.close()
        return html
    except Exception as e:
        logger.error("[Playwright] Error for %s: %s", url, e)
        return None


# ---------------------------------------------------------------------------
# Generic article extractors (used by fetcher and discovery)
# ---------------------------------------------------------------------------

def parse_rss_entries(entries, source_key: str = "") -> list[Article]:
    """Convert feedparser entries to a list of Article dicts."""
    articles: list[Article] = []
    for entry in entries:
        summary = ""
        if entry.get("summary"):
            summary = BeautifulSoup(entry.summary, "lxml").get_text(strip=True)[:300]

        categories = [t.term for t in entry.get("tags", [])] if entry.get("tags") else []

        # Google News RSS puts the outlet name in entry.source.title
        author = entry.get("author", "")
        if not author and hasattr(entry, "source") and entry.source:
            author = getattr(entry.source, "title", "")

        articles.append(Article(
            title=entry.get("title", ""),
            url=entry.get("link", ""),
            summary=summary,
            date=feedparser_date(entry),
            source=source_key,
            scraped_at=now_iso(),
            author=author,
            rank="",
            points="",
            hn_discussion="",
            categories=categories,
            reading_time="",
        ))
    return articles


def extract_articles(soup: BeautifulSoup, page_url: str, source_key: str = "") -> list[Article]:
    """
    Generic HTML article extractor. Works on most news listing pages.
    Priority: <article> tags → news-card selectors → h2/h3/h4 heading links.
    """
    parsed = urlparse(page_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    articles: list[Article] = []
    seen: set[str] = set()

    # Find article containers in priority order
    containers: list = (
        soup.select("article")
        or soup.select(
            ".post, [class*='post-'], [class*='article-'], "
            "[class*='news-item'], [class*='entry-'], [class*='card']"
        )
    )
    # Fall back to heading links when no containers are found
    heading_links: list = [] if containers else soup.select("h2 > a[href], h3 > a[href], h4 > a[href]")
    items = containers or heading_links

    for item in items:
        link = item if item.name == "a" else item.find("a", href=True)
        if not link:
            continue

        href = link.get("href", "")
        if not href:
            continue
        if href.startswith("/"):
            href = base + href
        elif not href.startswith("http"):
            continue
        if href in seen:
            continue

        # Prefer heading text over link text (avoids grabbing nav/category text)
        title = ""
        if item.name != "a":
            h = item.select_one("h1, h2, h3, h4")
            if h:
                title = h.get_text(strip=True)
        if not title:
            title = link.get_text(strip=True)
        if len(title) < 15:
            continue

        seen.add(href)

        # Metadata context: walk up one level for containers found via link
        ctx = item if item.name != "a" else (item.parent or item)

        date_str = ""
        d = ctx.select_one("time, [datetime], [class*='date'], [class*='Date']")
        if d:
            date_str = d.get("datetime", "") or d.get_text(strip=True)

        author = ""
        a_el = ctx.select_one(".author, .byline, [class*='author'], [rel='author']")
        if a_el:
            author = a_el.get_text(strip=True)

        summary = ""
        for p in ctx.find_all("p", limit=3):
            text = p.get_text(strip=True)
            if text and text != title and len(text) > 30:
                summary = text[:300]
                break

        categories = list(dict.fromkeys(
            c.get_text(strip=True)
            for c in ctx.select(
                "[rel='category tag'] a, .cat-links a, "
                "[class*='category'] a, [class*='tag'] a"
            )
            if c.get_text(strip=True)
        ))

        articles.append(Article(
            title=title,
            url=href,
            summary=summary,
            date=date_str,
            source=source_key,
            scraped_at=now_iso(),
            author=author,
            rank="",
            points="",
            hn_discussion="",
            categories=categories,
            reading_time="",
        ))

    return articles
