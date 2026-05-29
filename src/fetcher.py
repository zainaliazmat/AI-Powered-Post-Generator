import logging

from bs4 import BeautifulSoup

from .db import (
    get_active_sources,
    get_source,
    mark_source_crashed,
    set_source_article_count,
    update_last_fetched,
)
from .models import Article
from .settings import get_settings
from .shared import extract_articles, fetch_html, fetch_rss, get_playwright_html, parse_rss_entries

logger = logging.getLogger(__name__)


def fetch_source(key: str, *, dry_run: bool = False) -> list[Article]:
    """
    Fetch articles for a source that has already been added via --add.
    Loads method config from DB; dispatches to the appropriate fetch strategy.
    Raises on unrecoverable errors — caller decides whether to mark as crashed.

    If dry_run=True, no DB writes (no update_last_fetched). Used by the
    dashboard's test-fetch button.
    """
    source = get_source(key)
    if not source:
        raise KeyError(
            f"Source '{key}' not found in DB. Add it with: python cli.py --add --url <url>"
        )

    method = source["method"]
    url = source["url"]
    articles: list[Article] = []

    if method in ("rss", "rsshub"):
        entries = fetch_rss(source["feed_url"])
        articles = parse_rss_entries(entries, key)

    elif method == "html":
        soup = fetch_html(url)
        if soup:
            articles = extract_articles(soup, url, key)

    elif method == "playwright":
        html = get_playwright_html(url, source.get("pw_wait_for"))
        if html:
            articles = extract_articles(BeautifulSoup(html, "lxml"), url, key)

    else:
        raise ValueError(f"[{key}] Unknown method '{method}'")

    if not dry_run:
        update_last_fetched(key)
    logger.info("[%s] Fetched %d articles%s", key, len(articles), " (dry run)" if dry_run else "")
    return articles


def fetch_all() -> tuple[dict[str, list[Article]], dict[str, str]]:
    """
    Fetch every active source.

    Returns:
        results  — {key: articles} for sources that ran (empty list = OK, source works)
        crashes  — {key: error_message} for sources that raised; these have been moved
                   to the crashed_sources table automatically
    """
    sources = get_active_sources()
    if not sources:
        logger.warning("No active sources in DB. Add or unpause one.")
        return {}, {}

    per_source_max = get_settings()["per_source_max"]

    results: dict[str, list[Article]] = {}
    crashes: dict[str, str] = {}

    for row in sources:
        key = row["key"]
        try:
            arts = fetch_source(key)
            arts.sort(key=lambda a: a.get("date") or "", reverse=True)
            arts = arts[:per_source_max]
            results[key] = arts
            set_source_article_count(key, len(arts))
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error("[%s] CRASHED — %s", key, error_msg)
            mark_source_crashed(key, error_msg)
            crashes[key] = error_msg

    return results, crashes
