"""
M2 — Deduplication layer.

Merges duplicate news stories from different sources before scoring.

Integration example:
    from src.dedup import deduplicate_articles, print_duplicate_groups

    raw = json.loads(Path("data/latest_articles.json").read_text())
    unique = deduplicate_articles(raw)
    print_duplicate_groups(unique)   # optional debug
    # → pass unique to filter.py
"""

import logging
import re
from collections import defaultdict
from datetime import datetime, timezone
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOP_WORDS = frozenset({
    "a", "an", "the", "and", "of", "to", "for", "in", "on", "at",
    "with", "by", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "having", "does", "did", "doing",
})

_HN_PREFIX    = re.compile(r"^(Show HN|Ask HN|Tell HN|Launch HN):\s*", re.IGNORECASE)
_YEAR_SUFFIX  = re.compile(r"\s*\(\d{4}\)\s*$")
_FMT_SUFFIX   = re.compile(r"\s*\[(pdf|video|audio)\]\s*$", re.IGNORECASE)
_SRC_SUFFIX   = re.compile(
    r"\s*[\|–—\-]\s*(TechCrunch|InfoWorld|SD Times|InfoQ|Wired|Ars Technica|"
    r"The Verge|VentureBeat|MIT Technology Review|ZDNet|CNET|Engadget|"
    r"Reuters|BBC|Bloomberg|The Register|9to5Google|9to5Mac)\s*$",
    re.IGNORECASE,
)
_PUNCT        = re.compile(r"[^a-z0-9\s]")

# Second-level domain parts that are NOT the actual brand (country-code TLDs)
_GENERIC_SLD  = frozenset({"co", "com", "net", "org", "gov", "edu", "ac", "me"})

KNOWN_EVENTS = [
    "google i/o", "wwdc", "openai devday", "microsoft build",
    "aws re:invent", "google cloud next", "apple event",
    "meta connect", "nvidia gtc", "ces", "web summit",
    "techcrunch disrupt", "react conf", "vue conf", "rustconf",
    "pycon", "kubecon", "defcon", "black hat",
]

SOURCE_PRIORITY: dict[str, int] = {
    # Official company blogs
    "openai": 1, "anthropic": 2, "google": 3, "deepmind": 3,
    # Established tech news
    "infoworld": 4, "sdtimes": 4, "infoq": 4, "techcrunch": 4,
    # Community / repos
    "hn": 5, "ycombinator": 5, "github": 5, "arxiv": 5,
    # Authoritative general news
    "bbc": 6, "reuters": 6, "wsj": 6, "bloomberg": 6,
    # General tech press
    "theverge": 7, "wired": 7, "arstechnica": 7, "engadget": 7,
    # Opinion / blogs
    "medium": 8, "substack": 8,
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_source_from_url(url: str) -> str:
    """Return a short source key from a URL (e.g. 'openai' from 'blog.openai.com')."""
    if not url:
        return "unknown"
    try:
        host = urlparse(url).netloc.lower()
        host = re.sub(r"^(www\.|m\.)", "", host)
        parts = host.split(".")
        # Handle country-code TLDs: bbc.co.uk → ["bbc", "co", "uk"]
        if len(parts) >= 3 and parts[-2] in _GENERIC_SLD:
            return parts[-3]
        if len(parts) >= 2:
            return parts[-2]
        return parts[0] if parts else "unknown"
    except Exception:
        return "unknown"


def clean_title(title: str) -> str:
    t = _HN_PREFIX.sub("", title)
    t = _YEAR_SUFFIX.sub("", t)
    t = _FMT_SUFFIX.sub("", t)
    t = _SRC_SUFFIX.sub("", t)
    t = t.lower()
    t = _PUNCT.sub("", t)
    return " ".join(w for w in t.split() if w not in STOP_WORDS)


def jaccard(a: str, b: str) -> float:
    if not a and not b:
        return 1.0  # both empty after cleaning → identical
    sa, sb = set(a.split()), set(b.split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def detect_event(title: str) -> str:
    t = title.lower()
    for event in KNOWN_EVENTS:
        if event in t:
            return event
    return ""


def _source_priority(source: str) -> int:
    return SOURCE_PRIORITY.get((source or "").lower(), 9)


def _parse_date(date_str: str) -> datetime:
    if not date_str:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        pass
    try:
        dt = datetime.fromisoformat(date_str[:19])
        return dt.replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        pass
    logger.debug("Failed to parse date: %s", date_str)
    return datetime.min.replace(tzinfo=timezone.utc)


def _find(parent: list, i: int) -> int:
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _union(parent: list, i: int, j: int) -> None:
    ri, rj = _find(parent, i), _find(parent, j)
    if ri != rj:
        parent[rj] = ri


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def merge_group(group: list) -> dict:
    """Merge a duplicate group into one enriched article, preserving all data."""
    if len(group) > 50:
        logger.warning(
            "Unusually large duplicate group (%d articles): %s",
            len(group), group[0].get("title", "?")[:80],
        )

    sorted_group = sorted(
        group,
        key=lambda a: (
            _source_priority(a.get("source", "")),
            -_parse_date(a.get("date", "")).timestamp(),
        ),
    )
    base = dict(sorted_group[0])

    # Most recent date
    base["date"] = max(
        (a.get("date", "") for a in group),
        key=lambda d: _parse_date(d),
        default=base.get("date", ""),
    )

    # Longest non-empty summary
    base["summary"] = max(
        (a.get("summary", "") for a in group), key=len, default=""
    )

    # Most recent scraped_at
    base["scraped_at"] = max(
        (a.get("scraped_at", "") for a in group if a.get("scraped_at")),
        default=base.get("scraped_at", ""),
    )

    # First non-empty author
    base["author"] = next(
        (a.get("author", "") for a in group if a.get("author")), ""
    )

    # Union of categories (ordered, deduped)
    cats: list = []
    for a in group:
        cats.extend(a.get("categories", []))
    base["categories"] = list(dict.fromkeys(cats))

    # HN fields from the first HN article
    hn = next((a for a in group if a.get("source") == "hn"), None)
    if hn:
        base["rank"]         = hn.get("rank", "")
        base["points"]       = hn.get("points", "")
        base["hn_discussion"] = hn.get("hn_discussion", "")

    # Reading time: first non-empty
    base["reading_time"] = next(
        (a.get("reading_time", "") for a in group if a.get("reading_time")), ""
    )

    # Generic catch-all: preserve any extra fields from non-base articles
    known = {
        "title", "url", "summary", "date", "source", "scraped_at",
        "author", "rank", "points", "hn_discussion", "categories",
        "reading_time", "date_unknown",
    }
    all_keys = {k for a in group for k in a}
    for key in all_keys - known:
        if key not in base or base[key] in (None, "", []):
            val = next(
                (a[key] for a in group if a.get(key) not in (None, "", [])),
                None,
            )
            if val is not None:
                base[key] = val

    # Provenance
    base["deduplicated"]      = len(group) > 1
    base["duplicate_count"]   = len(group)
    base["merged_from_urls"]  = [a.get("url", "") for a in group]
    base["duplicate_titles"]  = [
        a["title"] for a in group
        if a.get("title") and a["title"] != base.get("title")
    ]
    base["duplicate_urls"]    = [
        a["url"] for a in group
        if a.get("url") and a["url"] != base.get("url")
    ]
    base["duplicate_sources"] = list({
        a.get("source", "") for a in group
        if a.get("source") and a.get("source") != base.get("source")
    })

    # Merge confidence: max Jaccard across all pairs in this group
    if len(group) > 1:
        titles_c = [clean_title(a.get("title", "")) for a in group]
        max_sim = max(
            jaccard(titles_c[i], titles_c[j])
            for i in range(len(group))
            for j in range(i + 1, len(group))
        )
        size_bonus = min(0.1, (len(group) - 1) * 0.05)
        base["merge_confidence"] = round(min(1.0, max_sim + size_bonus), 3)
    else:
        base["merge_confidence"] = 1.0

    return base


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def deduplicate_articles(articles: list, dry_run: bool = False) -> list:
    """
    Deduplicate articles by title similarity.

    Returns merged unique articles. Each kept article gains provenance fields:
    duplicate_count, duplicate_titles, duplicate_urls, duplicate_sources,
    merged_from_urls, merge_confidence.

    Same-event articles (Google I/O, WWDC, …) are tagged with related_event
    but are NOT deduplicated.

    dry_run=True: returns the original articles unchanged and prints what
    would have been merged (useful for threshold tuning).
    """
    valid = [a for a in articles if (a.get("title") or "").strip()]
    if not valid:
        return []

    # Ensure every article has a source field
    tagged: list = []
    for a in valid:
        article = dict(a)
        if not article.get("source"):
            article["source"] = extract_source_from_url(article.get("url", ""))
        ev = detect_event(article.get("title", ""))
        if ev:
            article["related_event"] = ev
        tagged.append(article)

    cleaned = [clean_title(a.get("title", "")) for a in tagged]
    n = len(tagged)
    parent = list(range(n))
    borderline: list = []

    for i in range(n):
        for j in range(i + 1, n):
            # Never merge same-event articles
            ev_i = tagged[i].get("related_event", "")
            ev_j = tagged[j].get("related_event", "")
            if ev_i and ev_j and ev_i == ev_j:
                continue

            # Skip pairs published more than 12 h apart
            dt_i = _parse_date(tagged[i].get("date", ""))
            dt_j = _parse_date(tagged[j].get("date", ""))
            if abs((dt_i - dt_j).total_seconds()) > 43_200:
                continue

            wi   = len(cleaned[i].split())
            wj   = len(cleaned[j].split())
            short = wi < 5 or wj < 5
            high  = 0.55 if short else 0.65
            low   = 0.35 if short else 0.45

            j_score = jaccard(cleaned[i], cleaned[j])
            if j_score >= high:
                _union(parent, i, j)
            elif j_score >= low:
                borderline.append((i, j))

    # TF-IDF second pass for borderline pairs
    if borderline:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity as cos_sim

            vec = TfidfVectorizer().fit_transform(cleaned)
            for i, j in borderline:
                if float(cos_sim(vec[i], vec[j])[0][0]) >= 0.7:
                    _union(parent, i, j)
        except ImportError:
            logger.debug("sklearn not available — using Jaccard-only deduplication")

    groups: dict = defaultdict(list)
    for i in range(n):
        groups[_find(parent, i)].append(tagged[i])

    if dry_run:
        _print_dry_run(groups)
        return list(valid)  # original articles, unchanged

    return [merge_group(g) for g in groups.values()]


# ---------------------------------------------------------------------------
# Debug helpers
# ---------------------------------------------------------------------------

def _print_dry_run(groups: dict) -> None:
    multi = {r: g for r, g in groups.items() if len(g) > 1}
    total_orig = sum(len(g) for g in groups.values())

    print(f"\n{'=' * 62}")
    print("DEDUP DRY-RUN (no merging applied)")
    print(f"{'=' * 62}")
    print(f"  Would reduce: {total_orig} → {len(groups)} articles")
    print(f"  Duplicate groups: {len(multi)}")
    for i, g in enumerate(multi.values(), 1):
        print(f"\n  Group {i}  ({len(g)} articles)")
        for a in g:
            print(f"    [{a.get('source','?'):12}]  {a.get('title','')[:70]}")
    print(f"{'=' * 62}\n")


def print_duplicate_groups(articles: list) -> None:
    """Print a summary of merged duplicate groups."""
    duped     = [a for a in articles if a.get("duplicate_count", 1) > 1]
    total_orig = sum(a.get("duplicate_count", 1) for a in articles)

    print(f"\n{'=' * 62}")
    print("DEDUPLICATION SUMMARY")
    print(f"{'=' * 62}")
    print(f"  Original articles : {total_orig}")
    print(f"  After dedup       : {len(articles)}")
    if total_orig:
        pct = (1 - len(articles) / total_orig) * 100
        print(f"  Reduction         : {pct:.1f}%")
    print(f"  Duplicate groups  : {len(duped)}")

    for i, a in enumerate(duped, 1):
        count = a.get("duplicate_count", 1)
        conf  = a.get("merge_confidence", "?")
        print(f"\n  Group {i}  ({count} merged, confidence={conf})")
        print(f"    KEPT  [{a.get('source','?'):12}]  {a['title']}")
        for t in a.get("duplicate_titles", []):
            print(f"    DROP               {t}")
        if a.get("related_event"):
            print(f"    EVENT: {a['related_event']}")

    print(f"{'=' * 62}\n")
