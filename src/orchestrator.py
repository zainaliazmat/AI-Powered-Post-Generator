import hashlib
import json
import logging
import sys
import time
from pathlib import Path

import anthropic
from dotenv import load_dotenv

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cli import cmd_scrape, cmd_dedup, cmd_generate  # noqa: E402

from .carousel_gen import ClaudeCarouselGenerator
from .db import save_reviewed_post

logger = logging.getLogger(__name__)

_client = anthropic.Anthropic()


def _strip_markdown_fences(text: str) -> str:
    """Strip ```json ... ``` or ``` ... ``` wrappers if present."""
    text = text.strip()
    if text.startswith("```"):
        text = text[text.index("\n") + 1:] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[: text.rfind("```")]
    return text.strip()


_REVIEW_PROMPT = """\
You are an adversarial content reviewer. Find weaknesses, not strengths.
Return ONLY valid JSON: {{"score": <1-10>, "issues": ["..."], "suggestions": ["..."]}}
No markdown. No commentary outside the JSON. Score 7+ = publish-ready.

CAROUSEL TO REVIEW:
Article: {title}
Summary: {summary}

{carousel_json}

SCORE ON:
- Hook strength (slide 1 grabs attention instantly)
- Factual accuracy vs article summary
- Slide flow (logical progression 1→8)
- CTA quality (slide 8 drives engagement)
- Writing rules: max 15 words/sentence, max 2 emojis/slide"""

_REVISE_PROMPT = """\
You are a carousel editor. Apply the given suggestions to the carousel JSON.
Return ONLY the revised carousel JSON in the exact same schema. No other text.

ORIGINAL CAROUSEL:
{carousel_json}

SUGGESTIONS TO APPLY:
{suggestions}"""


def run_review(post: dict) -> dict:
    """Call Haiku directly to review one post. Returns {score, issues, suggestions}."""
    article = post.get("article", {})
    carousel = post.get("carousel", {})

    prompt = _REVIEW_PROMPT.format(
        title=article.get("title", ""),
        summary=article.get("summary", ""),
        carousel_json=json.dumps(carousel, indent=2),
    )

    try:
        resp = _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
        )
        result_text = _strip_markdown_fences(resp.content[0].text)
        return json.loads(result_text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("ReviewAgent returned non-JSON: %s — defaulting to score=5", e)
        return {"score": 5, "issues": ["parse error"], "suggestions": []}
    except Exception as e:
        logger.error("ReviewAgent failed: %s — defaulting to score=5", e)
        return {"score": 5, "issues": [str(e)], "suggestions": []}


def run_revise(post: dict, suggestions: list) -> dict:
    """Call Haiku directly to revise one post. Returns revised carousel or original."""
    original_carousel = post.get("carousel", {})

    prompt = _REVISE_PROMPT.format(
        carousel_json=json.dumps(original_carousel, indent=2),
        suggestions=json.dumps(suggestions, indent=2),
    )

    try:
        resp = _client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        result_text = _strip_markdown_fences(resp.content[0].text)
        revised = json.loads(result_text)
        ClaudeCarouselGenerator._validate_carousel(None, revised)
        return revised
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("ReviseAgent returned invalid output: %s — keeping original", e)
        return original_carousel
    except Exception as e:
        logger.error("ReviseAgent failed: %s — keeping original", e)
        return original_carousel


def _article_hash(article: dict) -> str:
    content = (
        f"{article.get('title', '')}"
        f"{article.get('url', '')}"
        f"{article.get('summary', '')}"
    )
    return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()


def run_pipeline(force: bool = False) -> None:
    """Orchestrate full pipeline: scrape → dedup → generate → review → revise → save."""
    load_dotenv()

    logger.info("Step 1/6: Scraping sources...")
    cmd_scrape()
    articles_path = Path("data/latest_articles.json")
    if not articles_path.exists():
        print("STOP: scrape failed — data/latest_articles.json not found")
        return
    articles = json.loads(articles_path.read_text(encoding="utf-8"))
    if not articles:
        print("STOP: no new articles scraped")
        return
    print(f"Scraped: {len(articles)}")

    logger.info("Step 2/6: Deduplicating...")
    cmd_dedup()
    deduped_path = Path("data/deduped_articles.json")
    if not deduped_path.exists():
        print("STOP: dedup failed — data/deduped_articles.json not found")
        return
    deduped = json.loads(deduped_path.read_text(encoding="utf-8"))
    if not deduped:
        print("STOP: all articles were duplicates")
        return
    print(f"Unique: {len(deduped)}")

    logger.info("Step 3/6: Generating carousels...")
    cmd_generate(force_refresh=force)
    posts_path = Path("data/generated_posts.json")
    if not posts_path.exists():
        print("STOP: generation failed — data/generated_posts.json not found")
        return
    posts = json.loads(posts_path.read_text(encoding="utf-8"))
    if not posts:
        print("STOP: no carousels generated")
        return
    print(f"Generated: {len(posts)}")

    logger.info("Step 4-5/6: Reviewing and revising carousels...")
    approved = []
    for i, post in enumerate(posts):
        title = post.get("article", {}).get("title", "")[:50]
        logger.info("Reviewing post %d/%d: %s", i + 1, len(posts), title)
        review = run_review(post)
        score = float(review.get("score", 5))

        if score >= 7:
            logger.info("  Score %.1f approved", score)
            approved.append({"post": post, "score": score})
        else:
            logger.info("  Score %.1f — revising...", score)
            revised_carousel = run_revise(post, review.get("suggestions", []))
            post = {**post, "carousel": revised_carousel}
            approved.append({"post": post, "score": score})

        if i < len(posts) - 1:
            time.sleep(1)

    logger.info("Step 6/6: Saving %d posts to database...", len(approved))
    saved = 0
    for item in approved:
        post = item["post"]
        article = post.get("article", {})
        ok = save_reviewed_post(
            article_hash=_article_hash(article),
            article_url=article.get("url", ""),
            article_title=article.get("title", ""),
            carousel_json=post.get("carousel", {}),
            review_score=item["score"],
        )
        if ok:
            saved += 1

    print(
        f"\nPipeline complete:\n"
        f"  Scraped  : {len(articles)}\n"
        f"  Unique   : {len(deduped)}\n"
        f"  Generated: {len(posts)}\n"
        f"  Approved : {len(approved)}\n"
        f"  Saved    : {saved}"
    )
