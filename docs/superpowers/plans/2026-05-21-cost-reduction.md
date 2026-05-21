# Cost Reduction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut per-cycle LLM cost from $0.25–0.30 to ~$0.045 by removing the Agent SDK subprocess overhead from ReviewAgent/ReviseAgent and lowering max_tokens in carousel generation.

**Architecture:** Replace `claude_agent_sdk.query()` calls in `src/orchestrator.py` with direct `anthropic.messages.create()` calls (same pattern already used in `src/carousel_gen.py`). Make all orchestrator functions synchronous. Lower `max_tokens` in carousel_gen from 4096 to 2048.

**Tech Stack:** `anthropic` Python SDK (already a dependency), `pytest` for tests.

---

## File Map

| File | Change |
|---|---|
| `tests/test_orchestrator.py` | Rewrite mocks: `query` + async → `_client.messages.create` + sync |
| `src/orchestrator.py` | Replace SDK with direct API; remove async; Haiku for review (max_tokens=512); revise max_tokens=2048 |
| `src/carousel_gen.py` | `max_tokens` 4096 → 2048 |
| `cli.py` | Remove `asyncio.run()` from `cmd_run` |

---

### Task 1: Rewrite tests to match the new synchronous interface (they must FAIL first)

**Files:**
- Modify: `tests/test_orchestrator.py`

- [ ] **Step 1: Replace the entire test file with the synchronous version**

Replace `tests/test_orchestrator.py` with:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _make_response(text: str) -> MagicMock:
    """Create a mock anthropic.messages.create response with .content[0].text."""
    msg = MagicMock()
    msg.content = [MagicMock(text=text)]
    return msg


def test_save_reviewed_post_inserts_row(monkeypatch, tmp_path):
    """save_reviewed_post inserts a new row with review fields populated."""
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    from src.db import save_reviewed_post
    ok = save_reviewed_post(
        article_hash="abc123",
        article_url="https://example.com",
        article_title="Test Article",
        carousel_json={"slides": []},
        review_score=8.5,
    )
    assert ok is True

    with db_module.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM generated_posts WHERE article_hash = 'abc123'"
        ).fetchone()
    assert row is not None
    assert row["review_score"] == 8.5
    assert row["reviewed"] == 1
    assert row["status"] == "pending_review"


def test_save_reviewed_post_upserts_on_duplicate(monkeypatch, tmp_path):
    """save_reviewed_post updates review_score when article_hash already exists."""
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    from src.db import save_reviewed_post
    save_reviewed_post("dup", "https://x.com", "Title", {}, 7.0)
    ok = save_reviewed_post("dup", "https://x.com", "Title", {}, 9.0)
    assert ok is True

    with db_module.get_conn() as conn:
        row = conn.execute(
            "SELECT review_score FROM generated_posts WHERE article_hash = 'dup'"
        ).fetchone()
    assert row["review_score"] == 9.0


def test_run_review_returns_parsed_dict():
    """run_review calls messages.create and parses the JSON result."""
    review_json = '{"score": 8, "issues": ["weak CTA"], "suggestions": ["add urgency"]}'

    with patch("src.orchestrator._client") as mock_client:
        mock_client.messages.create.return_value = _make_response(review_json)

        from src.orchestrator import run_review
        post = {
            "article": {"title": "Test", "summary": "Summary"},
            "carousel": {"news_summary": "x", "total_slides": 8, "slides": []},
        }
        result = run_review(post)

    assert result["score"] == 8
    assert result["issues"] == ["weak CTA"]
    assert result["suggestions"] == ["add urgency"]


def test_run_review_returns_score5_on_bad_json():
    """run_review returns score=5 when the model returns non-JSON."""
    with patch("src.orchestrator._client") as mock_client:
        mock_client.messages.create.return_value = _make_response("not json at all")

        from src.orchestrator import run_review
        result = run_review({"article": {}, "carousel": {}})

    assert result["score"] == 5
    assert "issues" in result


def test_run_revise_returns_revised_carousel():
    """run_revise calls messages.create and returns a validated carousel dict."""
    revised = {
        "news_summary": "Revised summary",
        "total_slides": 8,
        "slides": [
            {
                "slide_number": i,
                "title": f"Slide {i}",
                "subtitle": "sub",
                "body": "body",
                "hashtags": "#tag",
                "image_prompt": "prompt",
            }
            for i in range(1, 9)
        ],
    }

    with patch("src.orchestrator._client") as mock_client:
        mock_client.messages.create.return_value = _make_response(json.dumps(revised))

        from src.orchestrator import run_revise
        result = run_revise(
            post={"article": {}, "carousel": {}},
            suggestions=["add urgency to slide 1"],
        )

    assert result["total_slides"] == 8
    assert len(result["slides"]) == 8


def test_run_revise_falls_back_to_original_on_invalid_output():
    """run_revise returns original carousel when model output fails validation."""
    original_carousel = {
        "news_summary": "original",
        "total_slides": 8,
        "slides": [
            {
                "slide_number": i,
                "title": "t",
                "subtitle": "s",
                "body": "b",
                "hashtags": "#h",
                "image_prompt": "p",
            }
            for i in range(1, 9)
        ],
    }

    with patch("src.orchestrator._client") as mock_client:
        mock_client.messages.create.return_value = _make_response("not json")

        from src.orchestrator import run_revise
        result = run_revise(
            post={"article": {}, "carousel": original_carousel},
            suggestions=["fix it"],
        )

    assert result == original_carousel


def test_run_pipeline_stops_early_when_no_articles(tmp_path, monkeypatch):
    """run_pipeline exits after scrape step when latest_articles.json is empty."""
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "latest_articles.json").write_text("[]")

    monkeypatch.chdir(tmp_path)

    with patch("src.orchestrator.cmd_scrape"), \
         patch("src.orchestrator.cmd_dedup"), \
         patch("src.orchestrator.cmd_generate"):

        from src.orchestrator import run_pipeline
        import io, sys
        captured = io.StringIO()
        sys.stdout = captured
        run_pipeline()
        sys.stdout = sys.__stdout__
        output = captured.getvalue()

    assert "STOP" in output
```

- [ ] **Step 2: Run the new tests — verify they FAIL**

```bash
cd /home/zain-ali/Documents/Scraper
source venv/bin/activate
pytest tests/test_orchestrator.py -v 2>&1 | tail -30
```

Expected: `test_run_review_*`, `test_run_revise_*`, `test_run_pipeline_*` FAIL because the current orchestrator still uses the async SDK. The two `test_save_reviewed_post_*` tests should still PASS — if they fail, stop and investigate.

---

### Task 2: Rewrite `src/orchestrator.py` — direct Anthropic API, synchronous

**Files:**
- Modify: `src/orchestrator.py`

- [ ] **Step 1: Replace the entire file**

Replace `src/orchestrator.py` with:

```python
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
```

- [ ] **Step 2: Run all orchestrator tests — verify they all PASS**

```bash
pytest tests/test_orchestrator.py -v 2>&1 | tail -20
```

Expected: all 7 tests PASS. If any fail, check the mock patch path (`src.orchestrator._client`) and ensure the module cache is not stale (try `pytest --cache-clear`).

- [ ] **Step 3: Commit**

```bash
git add src/orchestrator.py tests/test_orchestrator.py
git commit -m "refactor: replace Agent SDK with direct Anthropic API in orchestrator

- run_review and run_revise now call anthropic.messages.create() directly
- ReviewAgent switched from Sonnet 4.6 to Haiku 4.5 (max_tokens=512)
- ReviseAgent max_tokens set to 2048 (was unbounded by SDK)
- All functions made synchronous (no more async/await)
- Removes ~5000-8000 token SDK subprocess overhead per review call
- Estimated cost reduction: ~90% per pipeline cycle"
```

---

### Task 3: Lower `max_tokens` in carousel generation

**Files:**
- Modify: `src/carousel_gen.py:119`

- [ ] **Step 1: Change `max_tokens` from 4096 to 2048**

In `src/carousel_gen.py`, find the `_call_api` method and change one line:

```python
# Before (line ~119):
                max_tokens=4096,

# After:
                max_tokens=2048,
```

- [ ] **Step 2: Run the carousel generation tests to verify nothing broke**

```bash
pytest tests/test_carousel_gen.py -v 2>&1 | tail -20
```

Expected: all tests PASS. (These tests mock the API so the token limit change doesn't affect them — the point is to confirm no regressions.)

- [ ] **Step 3: Commit**

```bash
git add src/carousel_gen.py
git commit -m "perf: lower carousel_gen max_tokens 4096 → 2048

Measured output is ~1200 tokens; 2048 gives 70% headroom with no truncation risk."
```

---

### Task 4: Remove `asyncio.run()` from `cli.py`

**Files:**
- Modify: `cli.py:280-285`

- [ ] **Step 1: Replace `cmd_run` with a synchronous version**

In `cli.py`, replace the `cmd_run` function (lines 280–285):

```python
# Before:
def cmd_run(force: bool = False) -> None:
    import asyncio
    from dotenv import load_dotenv
    load_dotenv()
    from src.orchestrator import run_pipeline
    asyncio.run(run_pipeline(force=force))

# After:
def cmd_run(force: bool = False) -> None:
    from dotenv import load_dotenv
    load_dotenv()
    from src.orchestrator import run_pipeline
    run_pipeline(force=force)
```

- [ ] **Step 2: Smoke-test the CLI entry point parses without error**

```bash
python cli.py --help 2>&1 | head -5
```

Expected: usage text printed, no import errors.

- [ ] **Step 3: Run the full test suite to confirm no regressions**

```bash
pytest -v 2>&1 | tail -30
```

Expected: all tests PASS.

- [ ] **Step 4: Commit**

```bash
git add cli.py
git commit -m "refactor: remove asyncio.run() from cmd_run — run_pipeline is now synchronous"
```
