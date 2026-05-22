import logging

from bs4 import BeautifulSoup

from .db import get_all_sources, get_source, mark_source_crashed, update_last_fetched
from .models import Article
from .shared import extract_articles, fetch_html, fetch_rss, get_playwright_html, parse_rss_entries

logger = logging.getLogger(__name__)


def fetch_source(key: str) -> list[Article]:
    """
    Fetch articles for a source that has already been added via --add.
    Loads method config from DB; dispatches to the appropriate fetch strategy.
    Raises on unrecoverable errors — caller decides whether to mark as crashed.
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

    update_last_fetched(key)
    logger.info("[%s] Fetched %d articles", key, len(articles))
    return articles


def fetch_all() -> tuple[dict[str, list[Article]], dict[str, str]]:
    """
    Fetch every active source.

    Returns:
        results  — {key: articles} for sources that ran (empty list = OK, source works)
        crashes  — {key: error_message} for sources that raised; these have been moved
                   to the crashed_sources table automatically
    """
    sources = get_all_sources()
    if not sources:
        logger.warning("No sources in DB. Add one with: python cli.py --add --url <url>")
        return {}, {}

    results: dict[str, list[Article]] = {}
    crashes: dict[str, str] = {}

    for row in sources:
        key = row["key"]
        try:
            results[key] = fetch_source(key)
        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error("[%s] CRASHED — %s", key, error_msg)
            mark_source_crashed(key, error_msg)
            crashes[key] = error_msg

    return results, crashes
