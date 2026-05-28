"""
Instagram pipeline — CLI.

Usage:
    python cli.py --run                          # full pipeline (recommended)
    python cli.py --run --force                  # full pipeline, force-refresh carousels
    python cli.py --scrape                       # scrape all sources → data/latest_articles.json
    python cli.py --dedup                        # deduplicate → data/deduped_articles.json
    python cli.py --generate                     # generate carousels from deduped articles
    python cli.py --add --url https://...        # discover and add a new source
    python cli.py --list                         # list all active sources
    python cli.py --crashed                      # list crashed/broken sources
    python cli.py --fix <key>                    # restore a crashed source to the active list
    python cli.py --source <key>                 # test-fetch a single source (prints, no JSON)
    python cli.py --tables                        # list all DB tables with row counts
    python cli.py --posts                         # list all generated posts (status, score, images)
    python cli.py --db-images                     # list posts that have generated images
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt = "%(asctime)s %(levelname)-8s %(name)s — %(message)s"
    logging.basicConfig(format=fmt, datefmt="%H:%M:%S", level=level, stream=sys.stdout)
    Path("logs").mkdir(exist_ok=True)
    fh = logging.FileHandler("logs/fetcher.log")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt))
    logging.getLogger().addHandler(fh)


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_scrape() -> None:
    from src.fetcher import fetch_all
    from src.shared import is_within_hours

    results, crashes = fetch_all()

    # Collect articles, applying 12h age filter
    all_articles = []
    per_source_stats: dict[str, tuple[int, int, int]] = {}  # key → (kept, old, undated)

    for key, articles in results.items():
        fresh, old, undated = [], [], []
        for a in articles:
            verdict = is_within_hours(a.get("date", ""))
            if verdict is True:
                fresh.append(a)
            elif verdict is None:
                undated.append(a)
            else:
                old.append(a)

        for a in fresh:
            all_articles.append(dict(a))
        for a in undated:
            row = dict(a)
            row["date_unknown"] = True
            all_articles.append(row)

        per_source_stats[key] = (len(fresh) + len(undated), len(old), len(undated))

    # Fetch og:image for each article (used by Pillow renderer)
    from src.shared import fetch_og_image
    for article in all_articles:
        article["og_image_url"] = fetch_og_image(article.get("url", ""))

    # Write JSON
    out_path = Path("data/latest_articles.json")
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(
        json.dumps(all_articles, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Summary
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    width = max((len(k) for k in {**results, **crashes}), default=8) + 2
    print(f"\nScrape summary — {now}")
    print("─" * (width + 30))
    for key, (kept, old_count, undated_count) in per_source_stats.items():
        if kept == 0 and old_count == 0 and undated_count == 0:
            note = "0 articles  (empty — source OK)"
        else:
            note = f"{kept} articles"
            extras = []
            if old_count:
                extras.append(f"{old_count} older than 12h dropped")
            if undated_count:
                extras.append(f"{undated_count} undated, flagged")
            if extras:
                note += f"  ({', '.join(extras)})"
        print(f"  {key:<{width}} {note}")
    for key, err in crashes.items():
        short_err = err[:60] + "…" if len(err) > 60 else err
        print(f"  {key:<{width}} CRASHED → moved to crashed list  ({short_err})")

    total = len(all_articles)
    print("─" * (width + 30))
    print(f"  Total: {total} articles → {out_path}\n")
    if crashes:
        print(f"  {len(crashes)} source(s) crashed. Run --crashed to review.\n")


def cmd_add(url: str) -> None:
    from src.db import get_source, save_source
    from src.discovery import discover_method, url_to_key
    from src.fetcher import fetch_source

    key = url_to_key(url)
    print(f"Key: {key}")

    existing = get_source(key)
    if existing:
        print(f"Source '{key}' already exists (method: {existing['method']}). Nothing changed.")
        return

    print(f"Probing {url} ...")
    config = discover_method(url)
    if not config:
        print(f"\nFailed — no working fetch method found for {url}")
        sys.exit(1)

    save_source(key, url, config)
    print(f"Saved '{key}' — method: {config['method']}", end="")
    if config.get("feed_url"):
        print(f"  feed: {config['feed_url']}", end="")
    print()

    print("\nFetching sample articles...")
    try:
        articles = fetch_source(key)
        if articles:
            print(f"{len(articles)} articles found. First 5:\n")
            for a in articles[:5]:
                print(f"  • {a['title'][:90]}")
        else:
            print("(no articles returned — source saved, try --source to debug)")
    except Exception as e:
        print(f"(sample fetch failed: {e})")


def cmd_source(key: str) -> None:
    from src.fetcher import fetch_source

    try:
        articles = fetch_source(key)
    except KeyError as e:
        print(str(e))
        sys.exit(1)

    print(f"\n{len(articles)} article(s) from '{key}'")
    if articles:
        print(json.dumps({k: v for k, v in articles[0].items() if k != "source"}, indent=2, ensure_ascii=False))


def cmd_list() -> None:
    from src.db import get_all_sources

    rows = get_all_sources()
    if not rows:
        print("No active sources. Add one with: python cli.py --add --url <url>")
        return

    print(
        f"\n{'Key':<16} {'Method':<12} {'Active':<8} {'Articles':<10} "
        f"{'Last fetched':<26} URL"
    )
    print("─" * 100)
    for r in rows:
        last = r.get("last_fetched") or "never"
        active = "yes" if r.get("is_active") else "no"
        count = r.get("last_article_count")
        count_str = str(count) if count is not None else "—"
        print(
            f"  {r['key']:<14} {r['method']:<12} {active:<8} {count_str:<10} "
            f"{str(last)[:24]:<24} {r['url']}"
        )


def cmd_crashed() -> None:
    from src.db import get_crashed_sources

    rows = get_crashed_sources()
    if not rows:
        print("No crashed sources.")
        return

    print(f"\n{'Key':<16} {'Crashed at':<26} {'Error'}")
    print("─" * 90)
    for r in rows:
        err = (r.get("error_msg") or "")[:50]
        print(f"  {r['key']:<14} {str(r['crashed_at'])[:24]:<24} {err}")
    print(f"\n  {len(rows)} crashed source(s). Fix the issue then run: python cli.py --fix <key>")


def cmd_dedup() -> None:
    from src.dedup import deduplicate_articles, print_duplicate_groups

    in_path = Path("data/latest_articles.json")
    if not in_path.exists():
        print("data/latest_articles.json not found. Run --scrape first.")
        sys.exit(1)

    articles = json.loads(in_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(articles)} articles from {in_path}")

    deduped = deduplicate_articles(articles)
    print_duplicate_groups(deduped)

    out_path = Path("data/deduped_articles.json")
    out_path.write_text(
        json.dumps(deduped, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Written {len(deduped)} unique articles → {out_path}\n")


def cmd_generate(force_refresh: bool = False) -> None:
    from src.carousel_gen import ClaudeCarouselGenerator
    from src.db import save_generated_post

    in_path = Path("data/deduped_articles.json")
    if not in_path.exists():
        print("data/deduped_articles.json not found. Run --dedup first.")
        sys.exit(1)

    articles = json.loads(in_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(articles)} articles from {in_path}")

    gen = ClaudeCarouselGenerator()
    results = gen.batch_generate(articles, force_refresh=force_refresh)

    saved, skipped, failed = 0, 0, 0
    all_posts = []

    for r in results:
        if not r["success"]:
            failed += 1
            continue
        article = r["article"]
        carousel = r["carousel"]
        carousel["og_image_url"] = article.get("og_image_url", "")
        ok = save_generated_post(
            article_hash=gen._cache_key(article),
            article_url=article.get("url", ""),
            article_title=article.get("title", ""),
            carousel_json=carousel,
        )
        if ok:
            saved += 1
        else:
            skipped += 1
        all_posts.append({"article": article, "carousel": carousel})

    out_path = Path("data/generated_posts.json")
    out_path.write_text(
        json.dumps(all_posts, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    report = gen.get_usage_report()
    print(f"\nGeneration complete:")
    print(f"  Saved    : {saved} new posts → generated_posts table")
    print(f"  Skipped  : {skipped} (already in DB)")
    print(f"  Failed   : {failed}")
    print(f"  API calls: {report['total_calls']}")
    print(f"  Cost     : {report['estimated_cost_usd_formatted']}")
    print(f"  Output   : {out_path}\n")


def cmd_fix(key: str) -> None:
    from src.db import restore_source

    ok = restore_source(key)
    if ok:
        print(f"Source '{key}' restored to active list. Run --scrape to retry.")
    else:
        print(f"Source '{key}' not found in crashed list. Run --crashed to see what's there.")


def cmd_images(post_id: int) -> None:
    from src.db import get_conn, save_image_paths
    from src.ImageGen.image_gen import generate_for_post

    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, carousel_json, status FROM generated_posts WHERE id = ?",
            (post_id,),
        ).fetchone()

    if not row:
        print(f"Post {post_id} not found in DB.")
        return

    carousel = json.loads(row["carousel_json"])
    brand_domain = carousel.get("brand_domain")

    print(f"Generating images for post {post_id} (brand: {brand_domain or 'none'})...")
    paths = generate_for_post(
        post_id=post_id,
        carousel=carousel,
        brand_domain=brand_domain,
    )

    str_paths = [str(p) for p in paths]
    save_image_paths(post_id, str_paths)

    print(f"\nGenerated {len(paths)} image(s):")
    for p in str_paths:
        print(f"  {p}")


def cmd_tables() -> None:
    from src.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()

    if not rows:
        print("No tables found in data/pipeline.db")
        return

    print(f"\nTables in data/pipeline.db ({len(rows)} total):")
    print("─" * 40)
    for r in rows:
        count = 0
        with get_conn() as conn:
            count = conn.execute(f"SELECT COUNT(*) FROM [{r['name']}]").fetchone()[0]
        print(f"  {r['name']:<30} {count} rows")


def cmd_posts() -> None:
    from src.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, status, review_score, image_paths, created_at, article_title "
            "FROM generated_posts ORDER BY id DESC"
        ).fetchall()

    if not rows:
        print("No generated posts in DB.")
        return

    print(f"\n{'ID':<6} {'Status':<14} {'Score':<7} {'Imgs':<6} {'Created':<22} Title")
    print("─" * 100)
    for r in rows:
        score = f"{r['review_score']:.1f}" if r["review_score"] is not None else "—"
        imgs = len(json.loads(r["image_paths"])) if r["image_paths"] else 0
        created = (r["created_at"] or "")[:19].replace("T", " ")
        title = (r["article_title"] or "")[:48]
        print(f"  {r['id']:<4} {r['status']:<14} {score:<7} {imgs:<6} {created:<22} {title}")
    print(f"\n  {len(rows)} post(s) total.")


def cmd_db_images() -> None:
    from src.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT id, article_title, status, image_paths "
            "FROM generated_posts WHERE image_paths IS NOT NULL ORDER BY id DESC"
        ).fetchall()

    if not rows:
        print("No posts with images in DB.")
        return

    print(f"\n{'ID':<6} {'Status':<14} {'Images':<6} Title")
    print("─" * 90)
    for r in rows:
        paths = json.loads(r["image_paths"])
        title = (r["article_title"] or "")[:50]
        print(f"  {r['id']:<4} {r['status']:<14} {len(paths):<6} {title}")
        for p in paths:
            print(f"         {p}")
    print(f"\n  {len(rows)} post(s) with images.")


def cmd_run(force: bool = False, run_id: int | None = None) -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from src.orchestrator import run_pipeline
    run_pipeline(force=force, run_id=run_id)


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Instagram Pipeline — Scraper CLI",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python cli.py --scrape\n"
            "  python cli.py --add --url https://news.ycombinator.com\n"
            "  python cli.py --list\n"
            "  python cli.py --crashed\n"
            "  python cli.py --fix hn\n"
            "  python cli.py --source hn"
        ),
    )
    parser.add_argument("--scrape",  action="store_true", help="Scrape all active sources → data/latest_articles.json")
    parser.add_argument("--dedup",   action="store_true", help="Deduplicate latest_articles.json → data/deduped_articles.json")
    parser.add_argument("--generate",      action="store_true", help="Generate carousels from deduped_articles.json → generated_posts table")
    parser.add_argument("--force-refresh", action="store_true", help="Ignore carousel cache and regenerate (use with --generate)")
    parser.add_argument("--run",   action="store_true", help="Run full pipeline: scrape → dedup → generate → review → save → images")
    parser.add_argument("--force", action="store_true", help="Force-refresh carousel generation (use with --run)")
    parser.add_argument("--run-id", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--add",     action="store_true", help="Discover and save a new source")
    parser.add_argument("--url",     type=str,            help="URL for --add")
    parser.add_argument("--list",    action="store_true", help="List all active sources")
    parser.add_argument("--crashed", action="store_true", help="List crashed/broken sources")
    parser.add_argument("--fix",     type=str,            metavar="KEY", help="Restore a crashed source to the active list")
    parser.add_argument("--source",  type=str,            help="Test-fetch a single source (prints, no JSON)")
    parser.add_argument("--images",    type=int,            metavar="POST_ID", help="Generate images for a post by DB id")
    parser.add_argument("--tables",    action="store_true", help="List all tables in data/pipeline.db with row counts")
    parser.add_argument("--posts",     action="store_true", help="List all generated posts with status, score, and image count")
    parser.add_argument("--db-images", action="store_true", help="List all posts that have generated images in DB")
    parser.add_argument("--verbose",   action="store_true", help="Enable DEBUG logging")
    args = parser.parse_args()

    setup_logging(args.verbose)

    if args.run:
        cmd_run(force=args.force, run_id=args.run_id)
    elif args.scrape:
        cmd_scrape()
    elif args.dedup:
        cmd_dedup()
    elif args.generate:
        from dotenv import load_dotenv
        load_dotenv()
        cmd_generate(force_refresh=args.force_refresh)
    elif args.add:
        if not args.url:
            print("--add requires --url. Example: python cli.py --add --url https://sdtimes.com")
            sys.exit(1)
        cmd_add(args.url)
    elif args.list:
        cmd_list()
    elif args.crashed:
        cmd_crashed()
    elif args.fix:
        cmd_fix(args.fix)
    elif args.source:
        cmd_source(args.source)
    elif args.images:
        from dotenv import load_dotenv
        load_dotenv()
        cmd_images(args.images)
    elif args.tables:
        cmd_tables()
    elif args.posts:
        cmd_posts()
    elif args.db_images:
        cmd_db_images()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
