# Brand-Aware Image Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate Instagram carousel images that look like real brand press assets — Flux.1-schnell images enriched with brand-accurate prompts, with actual brand logos composited as a corner badge via Clearbit + Pillow.

**Architecture:** `carousel_gen.py` (Haiku) now outputs a `brand_domain` field. `image_gen.py` calls `brand.py` to enrich prompts and fetch Clearbit logos, calls Flux.1-schnell, then composites a circular badge. The orchestrator's `review_revise` node is split into `review` + `revise` for single-responsibility.

**Tech Stack:** Pillow, Replicate (Flux.1-schnell), Clearbit Logo API (free), LiteLLM, LangGraph, SQLite (stdlib), requests

---

## File Map

| File | Change | Responsibility |
|---|---|---|
| `src/db.py` | Modify | Add `image_paths` column + `save_image_paths()` + `migrate_image_columns()` |
| `src/orchestrator.py` | Modify | Split `review_revise` → `review` + `revise` nodes; add `review_results` to state |
| `src/carousel_gen.py` | Modify | Add `brand_domain` extraction to prompt + JSON schema |
| `src/brand.py` | Create | `fetch_logo`, `enrich_prompt`, `composite_badge` |
| `src/image_gen.py` | Create | `generate_for_post`, `generate_for_slide`, `_pillow_text_card` |
| `cli.py` | Modify | Add `--images <post_id>` command for manual image gen |
| `tests/test_brand.py` | Create | Tests for all three `brand.py` functions |
| `tests/test_image_gen.py` | Create | Tests for `image_gen.py` functions |
| `tests/test_carousel_gen.py` | Modify | Add test for `brand_domain` in carousel output |
| `tests/test_orchestrator.py` | Modify | Replace `review_revise` tests with `review` + `revise` node tests |

---

## Task 1: DB — Add image_paths column and helpers

**Files:**
- Modify: `src/db.py`
- Test: `tests/test_orchestrator.py` (existing DB tests cover migration pattern)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_orchestrator.py`:

```python
def test_save_image_paths_updates_row(tmp_path):
    import src.db as db_module
    db_module.DB_PATH = tmp_path / "test.db"
    db_module.init_db()

    from src.db import save_reviewed_post, save_image_paths
    save_reviewed_post("img_hash", "https://x.com", "Title", {}, 8.0)

    with db_module.get_conn() as conn:
        post_id = conn.execute(
            "SELECT id FROM generated_posts WHERE article_hash = 'img_hash'"
        ).fetchone()["id"]

    ok = save_image_paths(post_id, ["/data/images/1_1.png", "/data/images/1_2.png"])
    assert ok is True

    with db_module.get_conn() as conn:
        row = conn.execute(
            "SELECT image_paths, status FROM generated_posts WHERE id = ?", (post_id,)
        ).fetchone()
    import json
    assert json.loads(row["image_paths"]) == ["/data/images/1_1.png", "/data/images/1_2.png"]
    assert row["status"] == "image_ready"
```

- [ ] **Step 2: Run test to confirm it fails**

```bash
pytest tests/test_orchestrator.py::test_save_image_paths_updates_row -v
```

Expected: `ImportError` or `AttributeError` — `save_image_paths` doesn't exist yet.

- [ ] **Step 3: Add `migrate_image_columns` and `save_image_paths` to `src/db.py`**

After the existing `migrate_review_columns` function, add:

```python
def migrate_image_columns() -> None:
    """Add image_paths column if it doesn't exist. Safe to call repeatedly."""
    with get_conn() as conn:
        existing = {
            row[1]
            for row in conn.execute("PRAGMA table_info(generated_posts)").fetchall()
        }
        if "image_paths" not in existing:
            conn.execute(
                "ALTER TABLE generated_posts ADD COLUMN image_paths TEXT"
            )
            logger.info("Added image_paths column to generated_posts")


def save_image_paths(post_id: int, image_paths: list[str]) -> bool:
    """Set image_paths and mark post as image_ready. Returns True always."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE generated_posts SET image_paths = ?, status = 'image_ready' WHERE id = ?",
            (json.dumps(image_paths), post_id),
        )
    return True
```

Also add a call to `migrate_image_columns()` inside `init_db()`, after the existing `migrate_review_columns()` call:

```python
def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sources ( ... );  # keep existing
            CREATE TABLE IF NOT EXISTS crashed_sources ( ... );  # keep existing
            CREATE TABLE IF NOT EXISTS generated_posts ( ... );  # keep existing
        """)
    migrate_review_columns()
    migrate_image_columns()   # ← add this line
```

- [ ] **Step 4: Run test to confirm it passes**

```bash
pytest tests/test_orchestrator.py::test_save_image_paths_updates_row -v
```

Expected: PASS

- [ ] **Step 5: Run the full existing test suite to check for regressions**

```bash
pytest tests/ -v
```

Expected: all previously passing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add src/db.py tests/test_orchestrator.py
git commit -m "feat(db): add image_paths column and save_image_paths helper"
```

---

## Task 2: Orchestrator — split review_revise into review + revise nodes

**Files:**
- Modify: `src/orchestrator.py`
- Modify: `tests/test_orchestrator.py`

- [ ] **Step 1: Write failing tests for the new node signatures**

Replace the two `test_review_revise_node_*` tests in `tests/test_orchestrator.py` with:

```python
def _base_state(**overrides):
    state = {
        "force": False, "articles_count": 1, "unique_count": 1,
        "posts": [], "review_results": [], "reviewed_posts": [],
        "saved_count": 0, "stop_reason": None,
    }
    state.update(overrides)
    return state


def test_review_node_stores_scores():
    review_json = json.dumps({"score": 9, "issues": [], "suggestions": []})
    post = {"article": {"title": "T", "summary": "S"}, "carousel": SAMPLE_CAROUSEL}

    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response(review_json)):
        with patch("src.orchestrator.time.sleep"):
            from src.orchestrator import _review_node
            result = _review_node(_base_state(posts=[post]))

    assert len(result["review_results"]) == 1
    assert result["review_results"][0]["score"] == 9.0


def test_revise_node_rewrites_low_score_post():
    post = {"article": {"title": "T", "summary": "S"}, "carousel": SAMPLE_CAROUSEL}
    review_results = [{"post": post, "score": 4.0, "suggestions": ["fix hook"]}]

    with patch("src.orchestrator.litellm.completion",
               return_value=_make_litellm_response(json.dumps(SAMPLE_CAROUSEL))):
        from src.orchestrator import _revise_node
        result = _revise_node(_base_state(review_results=review_results))

    assert len(result["reviewed_posts"]) == 1
    assert result["reviewed_posts"][0]["score"] == 4.0


def test_revise_node_passes_through_high_score_post():
    post = {"article": {"title": "T", "summary": "S"}, "carousel": SAMPLE_CAROUSEL}
    review_results = [{"post": post, "score": 8.0, "suggestions": []}]

    from src.orchestrator import _revise_node
    result = _revise_node(_base_state(review_results=review_results))

    assert len(result["reviewed_posts"]) == 1
    assert result["reviewed_posts"][0]["score"] == 8.0
```

- [ ] **Step 2: Run new tests to confirm they fail**

```bash
pytest tests/test_orchestrator.py::test_review_node_stores_scores tests/test_orchestrator.py::test_revise_node_rewrites_low_score_post tests/test_orchestrator.py::test_revise_node_passes_through_high_score_post -v
```

Expected: `ImportError` — `_review_node` and `_revise_node` don't exist yet.

- [ ] **Step 3: Update `PipelineState` in `src/orchestrator.py`**

Add `review_results` field:

```python
class PipelineState(TypedDict):
    force: bool
    articles_count: int
    unique_count: int
    posts: list
    review_results: list       # [{post, score, suggestions}, ...]
    reviewed_posts: list
    saved_count: int
    stop_reason: str | None
```

- [ ] **Step 4: Replace `_review_revise_node` with two separate nodes in `src/orchestrator.py`**

Delete `_review_revise_node` entirely. Add these two functions in its place:

```python
def _review_node(state: PipelineState) -> dict:
    posts = state["posts"]
    results = []
    for i, post in enumerate(posts):
        title = post.get("article", {}).get("title", "")[:50]
        logger.info("Reviewing post %d/%d: %s", i + 1, len(posts), title)
        print(f"  Reviewing {i + 1}/{len(posts)}: {title}...", flush=True)
        review = run_review(post)
        score = float(review.get("score", 5))
        results.append({
            "post": post,
            "score": score,
            "suggestions": review.get("suggestions", []),
        })
        if i < len(posts) - 1:
            time.sleep(1)
    return {"review_results": results}


def _revise_node(state: PipelineState) -> dict:
    reviewed = []
    for item in state["review_results"]:
        post = item["post"]
        score = item["score"]
        title = post.get("article", {}).get("title", "")[:50]
        if score < 7:
            logger.info("  Score %.1f — revising: %s", score, title)
            print(f"    Score {score:.0f} — revising...", flush=True)
            revised_carousel = run_revise(post, item["suggestions"])
            post = {**post, "carousel": revised_carousel}
        else:
            logger.info("  Score %.1f — approved: %s", score, title)
            print(f"    Score {score:.0f} — approved", flush=True)
        reviewed.append({"post": post, "score": score})
    return {"reviewed_posts": reviewed}
```

- [ ] **Step 5: Rename `_save_node` → `_save_draft_node` and update it to handle both paths**

```python
def _save_draft_node(state: PipelineState) -> dict:
    # Use reviewed_posts if revise ran; otherwise convert from review_results directly
    posts_to_save = state.get("reviewed_posts") or [
        {"post": r["post"], "score": r["score"]}
        for r in state.get("review_results", [])
    ]
    saved = 0
    for item in posts_to_save:
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
    return {"saved_count": saved}
```

- [ ] **Step 6: Update `_build_graph` to use the new nodes**

```python
def _build_graph(checkpointer) -> object:
    builder = StateGraph(PipelineState)
    builder.add_node("scrape", _scrape_node)
    builder.add_node("dedup", _dedup_node)
    builder.add_node("generate", _generate_node)
    builder.add_node("review", _review_node)
    builder.add_node("revise", _revise_node)
    builder.add_node("save_draft", _save_draft_node)

    builder.set_entry_point("scrape")
    builder.add_conditional_edges("scrape", lambda s: END if s.get("stop_reason") else "dedup")
    builder.add_conditional_edges("dedup", lambda s: END if s.get("stop_reason") else "generate")
    builder.add_conditional_edges("generate", lambda s: END if s.get("stop_reason") else "review")
    builder.add_conditional_edges(
        "review",
        lambda s: "revise" if any(r["score"] < 7 for r in s.get("review_results", [])) else "save_draft",
    )
    builder.add_edge("revise", "save_draft")
    builder.add_edge("save_draft", END)

    return builder.compile(checkpointer=checkpointer)
```

- [ ] **Step 7: Update the initial state in `run_pipeline` to include `review_results`**

```python
initial_state: PipelineState = {
    "force": force,
    "articles_count": 0,
    "unique_count": 0,
    "posts": [],
    "review_results": [],     # ← add this
    "reviewed_posts": [],
    "saved_count": 0,
    "stop_reason": None,
}
```

Also update the streaming print block — replace `review_revise` with `review` and `revise`:

```python
elif node_name == "review" and node_state.get("review_results"):
    print(f"  ✓ Reviewed {len(node_state['review_results'])} posts")
elif node_name == "revise" and node_state.get("reviewed_posts"):
    print(f"  ✓ Revised {len(node_state['reviewed_posts'])} posts")
elif node_name == "save_draft":
    print(f"  ✓ Saved {node_state.get('saved_count', 0)} to DB")
```

- [ ] **Step 8: Run new tests to confirm they pass**

```bash
pytest tests/test_orchestrator.py -v
```

Expected: all tests pass (new ones + existing DB and run_review/run_revise tests).

- [ ] **Step 9: Commit**

```bash
git add src/orchestrator.py tests/test_orchestrator.py
git commit -m "refactor(orchestrator): split review_revise into review + revise nodes"
```

---

## Task 3: carousel_gen — add brand_domain extraction

**Files:**
- Modify: `src/carousel_gen.py`
- Modify: `tests/test_carousel_gen.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_carousel_gen.py`:

```python
SAMPLE_CAROUSEL_WITH_BRAND = {
    **SAMPLE_CAROUSEL,
    "brand_domain": "nvidia.com",
}

SAMPLE_CAROUSEL_NO_BRAND = {
    **SAMPLE_CAROUSEL,
    "brand_domain": None,
}


def test_generate_carousel_includes_brand_domain(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    with patch("src.carousel_gen.litellm.completion",
               return_value=make_mock_litellm_response(json.dumps(SAMPLE_CAROUSEL_WITH_BRAND))):
        result = gen.generate_carousel(SAMPLE_ARTICLE)
    assert "brand_domain" in result
    assert result["brand_domain"] == "nvidia.com"


def test_generate_carousel_brand_domain_can_be_null(tmp_path):
    gen = ClaudeCarouselGenerator(cache_dir=str(tmp_path))
    with patch("src.carousel_gen.litellm.completion",
               return_value=make_mock_litellm_response(json.dumps(SAMPLE_CAROUSEL_NO_BRAND))):
        result = gen.generate_carousel(SAMPLE_ARTICLE)
    assert result.get("brand_domain") is None
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_carousel_gen.py::test_generate_carousel_includes_brand_domain tests/test_carousel_gen.py::test_generate_carousel_brand_domain_can_be_null -v
```

Expected: FAIL — `brand_domain` is not currently in the carousel JSON.

- [ ] **Step 3: Update `_PROMPT_TEMPLATE` in `src/carousel_gen.py`**

Append the following brand extraction block to the end of `_PROMPT_TEMPLATE` (before the final `## CRITICAL` line):

```python
_PROMPT_TEMPLATE = """\
...existing content...

## BRAND EXTRACTION RULES:
Identify the SINGLE most relevant company this article focuses on.

Rules:
- If article announces something by one company → use that company's primary domain
- For subsidiaries, use the parent company domain
  (e.g. "Google DeepMind" → "google.com", "Microsoft GitHub Copilot" → "microsoft.com")
- If two companies are equally featured (e.g. "NVIDIA and AMD partner") → set null
- If article is about a person, technology, or concept with no clear company → set null
- Use the company's primary .com domain (not regional, not product-specific)

Examples:
"OpenAI releases GPT-5" → "openai.com"
"Microsoft GitHub Copilot update" → "microsoft.com"
"Jensen Huang keynote at GTC" → "nvidia.com"
"Python 3.13 released" → null

Add "brand_domain" as a top-level field in your JSON output (string or null).

## CRITICAL: Return ONLY valid JSON. Start with {{ and end with }}."""
```

Also update the JSON schema comment in `_PROMPT_TEMPLATE` to show `brand_domain` at the top level:

```python
## OUTPUT FORMAT (MUST BE VALID JSON — NO OTHER TEXT):
{{
  "brand_domain": "nvidia.com",
  "news_summary": "One powerful sentence summarizing the biggest takeaway",
  "total_slides": <2-6>,
  "slides": [ ... ]
}}
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_carousel_gen.py -v
```

Expected: all pass. The mock already returns `brand_domain` in `SAMPLE_CAROUSEL_WITH_BRAND`, so the generator forwards it through.

- [ ] **Step 5: Commit**

```bash
git add src/carousel_gen.py tests/test_carousel_gen.py
git commit -m "feat(carousel_gen): extract brand_domain from article via Haiku"
```

---

## Task 4: brand.py — fetch_logo

**Files:**
- Create: `src/brand.py`
- Create: `tests/test_brand.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_brand.py`:

```python
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_fetch_logo_downloads_and_caches(tmp_path):
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # minimal fake PNG header

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png

    with patch("src.brand._BRAND_ASSETS_DIR", tmp_path):
        with patch("src.brand.requests.get", return_value=mock_resp):
            from src.brand import fetch_logo
            result = fetch_logo("nvidia.com")

    assert result is not None
    assert result.exists()
    assert result.read_bytes() == fake_png


def test_fetch_logo_returns_cached_on_second_call(tmp_path):
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png

    with patch("src.brand._BRAND_ASSETS_DIR", tmp_path):
        with patch("src.brand.requests.get", return_value=mock_resp) as mock_get:
            from src.brand import fetch_logo
            fetch_logo("nvidia.com")
            fetch_logo("nvidia.com")

    assert mock_get.call_count == 1  # network called only once


def test_fetch_logo_returns_none_on_404(tmp_path):
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("src.brand._BRAND_ASSETS_DIR", tmp_path):
        with patch("src.brand.requests.get", return_value=mock_resp):
            from src.brand import fetch_logo
            result = fetch_logo("unknown-brand-xyz.com")

    assert result is None


def test_fetch_logo_refreshes_stale_cache(tmp_path):
    fake_png_old = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    fake_png_new = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

    cache_file = tmp_path / "nvidia.com.png"
    cache_file.write_bytes(fake_png_old)
    # Set mtime to 91 days ago
    old_mtime = time.time() - (91 * 86400)
    import os
    os.utime(cache_file, (old_mtime, old_mtime))

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = fake_png_new

    with patch("src.brand._BRAND_ASSETS_DIR", tmp_path):
        with patch("src.brand.requests.get", return_value=mock_resp):
            from src.brand import fetch_logo
            result = fetch_logo("nvidia.com")

    assert result is not None
    assert result.read_bytes() == fake_png_new
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
pytest tests/test_brand.py -v
```

Expected: `ModuleNotFoundError` — `src/brand.py` doesn't exist yet.

- [ ] **Step 3: Create `src/brand.py` with `fetch_logo` and helpers**

```python
import logging
import os
import re
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_BRAND_ASSETS_DIR = Path("data/brand_assets")

_BRAND_STYLES: dict[str, dict] = {
    "nvidia.com": {
        "colors": ["#76B900"],
        "style_cue": "dark background with NVIDIA green (#76B900) accents, GPU hardware and server arrays, photorealistic corporate photography",
    },
    "google.com": {
        "colors": ["#4285F4", "#EA4335", "#FBBC05", "#34A853"],
        "style_cue": "clean minimalist white background, Google multicolor accents, professional campus photography",
    },
    "microsoft.com": {
        "colors": ["#F25022", "#7FBA00", "#00A4EF", "#FFB900"],
        "style_cue": "blue-heavy professional corporate photography, Windows aesthetic, clean modern office",
    },
    "apple.com": {
        "colors": ["#000000", "#FFFFFF"],
        "style_cue": "stark white or black background, product-focused, minimalist, high-gloss photography",
    },
    "meta.com": {
        "colors": ["#0866FF"],
        "style_cue": "blue gradient background, social connectivity or VR/AR imagery, modern corporate",
    },
    "openai.com": {
        "colors": ["#000000"],
        "style_cue": "dark minimal background, abstract AI neural network patterns, clean professional",
    },
    "amazon.com": {
        "colors": ["#FF9900"],
        "style_cue": "orange accents on dark background, logistics and cloud infrastructure, AWS server rooms",
    },
    "tesla.com": {
        "colors": ["#CC0000"],
        "style_cue": "red accents, electric vehicles on clean backgrounds, minimalist product photography",
    },
    "samsung.com": {
        "colors": ["#1428A0"],
        "style_cue": "deep Samsung blue, consumer electronics and device hardware, clean studio photography",
    },
    "intel.com": {
        "colors": ["#0071C5"],
        "style_cue": "Intel blue accents, silicon chip and processor close-ups, clean corporate photography",
    },
}

_CLEANUP_PATTERNS = [
    r"cyberpunk",
    r"neon\b",
    r"futuristic",
    r"cinematic lighting",
    r"4K resolution",
    r"\b4K\b",
]

_DEFAULT_STYLE = "corporate tech brand, professional photography style, clean background"


def _download_logo(domain: str, dest: Path) -> bool:
    try:
        resp = requests.get(
            f"https://logo.clearbit.com/{domain}",
            timeout=5,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code == 200:
            dest.write_bytes(resp.content)
            return True
        logger.warning("Clearbit returned %d for %s", resp.status_code, domain)
        return False
    except Exception as e:
        logger.warning("Clearbit fetch failed for %s: %s", domain, e)
        return False


def fetch_logo(domain: str, max_age_days: int = 90) -> Path | None:
    _BRAND_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = _BRAND_ASSETS_DIR / f"{domain}.png"

    if cache_path.exists():
        age_seconds = time.time() - cache_path.stat().st_mtime
        if age_seconds < max_age_days * 86400:
            return cache_path
        logger.info("Logo cache stale for %s — refreshing", domain)
        if _download_logo(domain, cache_path):
            return cache_path
        logger.warning("Logo refresh failed for %s — using stale cache", domain)
        return cache_path

    return cache_path if _download_logo(domain, cache_path) else None
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_brand.py::test_fetch_logo_downloads_and_caches tests/test_brand.py::test_fetch_logo_returns_cached_on_second_call tests/test_brand.py::test_fetch_logo_returns_none_on_404 tests/test_brand.py::test_fetch_logo_refreshes_stale_cache -v
```

Expected: all 4 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/brand.py tests/test_brand.py
git commit -m "feat(brand): add fetch_logo with Clearbit + 90-day cache"
```

---

## Task 5: brand.py — enrich_prompt

**Files:**
- Modify: `src/brand.py`
- Modify: `tests/test_brand.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brand.py`:

```python
def test_enrich_prompt_returns_unchanged_for_none_domain():
    from src.brand import enrich_prompt
    base = "Futuristic data center, neon cyan on dark background, 4K"
    assert enrich_prompt(base, None) == base


def test_enrich_prompt_injects_known_brand_style():
    from src.brand import enrich_prompt
    result = enrich_prompt("Some image prompt here", "nvidia.com")
    assert "#76B900" in result
    assert "GPU" in result or "dark background" in result


def test_enrich_prompt_strips_cyberpunk_terms():
    from src.brand import enrich_prompt
    base = "Cyberpunk scene with neon lights, futuristic city, 4K resolution"
    result = enrich_prompt(base, "google.com")
    assert "cyberpunk" not in result.lower()
    assert "futuristic" not in result.lower()
    assert "neon" not in result.lower()


def test_enrich_prompt_uses_default_style_for_unknown_brand():
    from src.brand import enrich_prompt
    result = enrich_prompt("Some image prompt", "unknownbrand999.com")
    assert "corporate tech" in result or "professional" in result


def test_enrich_prompt_truncates_at_1000_chars():
    from src.brand import enrich_prompt
    long_prompt = "A " * 600  # 1200 chars
    result = enrich_prompt(long_prompt, "nvidia.com")
    assert len(result) <= 1000
    assert result.endswith("...")
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_brand.py::test_enrich_prompt_returns_unchanged_for_none_domain tests/test_brand.py::test_enrich_prompt_injects_known_brand_style tests/test_brand.py::test_enrich_prompt_strips_cyberpunk_terms tests/test_brand.py::test_enrich_prompt_uses_default_style_for_unknown_brand tests/test_brand.py::test_enrich_prompt_truncates_at_1000_chars -v
```

Expected: `ImportError` — `enrich_prompt` not defined yet.

- [ ] **Step 3: Add `enrich_prompt` to `src/brand.py`**

Add after `fetch_logo`:

```python
def enrich_prompt(base_prompt: str, brand_domain: str | None) -> str:
    if not brand_domain:
        return base_prompt

    cleaned = base_prompt
    for pattern in _CLEANUP_PATTERNS:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    brand = _BRAND_STYLES.get(brand_domain)
    if brand:
        colors_str = ", ".join(brand["colors"])
        enriched = f"{brand['style_cue']}. {cleaned}. Brand colors: {colors_str}."
    else:
        enriched = f"{_DEFAULT_STYLE}. {cleaned}."

    if len(enriched) > 1000:
        enriched = enriched[:997] + "..."

    return enriched
```

- [ ] **Step 4: Run to confirm they pass**

```bash
pytest tests/test_brand.py -k "enrich_prompt" -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/brand.py tests/test_brand.py
git commit -m "feat(brand): add enrich_prompt with brand style injection and cleanup"
```

---

## Task 6: brand.py — composite_badge

**Files:**
- Modify: `src/brand.py`
- Modify: `tests/test_brand.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_brand.py`:

```python
def test_composite_badge_produces_file(tmp_path):
    from PIL import Image, ImageDraw
    from src.brand import composite_badge

    # Create a fake 1080x1350 base image (solid blue)
    base_path = tmp_path / "base.png"
    img = Image.new("RGB", (1080, 1350), color=(0, 0, 200))
    img.save(base_path)

    # Create a fake 100x100 logo (solid white)
    logo_path = tmp_path / "logo.png"
    logo = Image.new("RGBA", (100, 100), color=(255, 255, 255, 255))
    logo.save(logo_path)

    result = composite_badge(base_path, logo_path)
    assert result == base_path
    assert result.exists()


def test_composite_badge_places_badge_top_right(tmp_path):
    from PIL import Image
    from src.brand import composite_badge

    base_path = tmp_path / "base.png"
    img = Image.new("RGB", (1080, 1350), color=(0, 0, 0))
    img.save(base_path)

    logo_path = tmp_path / "logo.png"
    logo = Image.new("RGBA", (100, 100), color=(255, 0, 0, 255))
    logo.save(logo_path)

    composite_badge(base_path, logo_path)

    result_img = Image.open(base_path).convert("RGB")
    # The badge (80x80) with 20px inset sits at x=980, y=20
    # The white border ring means pixel at (1080-20-80//2, 20) should be white (border)
    badge_center_x = 1080 - 20 - 40  # right edge - inset - half badge = 1020
    badge_top_y = 20
    pixel = result_img.getpixel((badge_center_x, badge_top_y))
    # Top of badge circle should be white (border ring)
    assert pixel[0] > 200  # R channel white-ish
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_brand.py::test_composite_badge_produces_file tests/test_brand.py::test_composite_badge_places_badge_top_right -v
```

Expected: `ImportError` — `composite_badge` not defined yet.

- [ ] **Step 3: Add `composite_badge` to `src/brand.py`**

Add the Pillow import at the top of `src/brand.py`:

```python
from PIL import Image, ImageDraw
```

Add after `enrich_prompt`:

```python
def composite_badge(image_path: Path, logo_path: Path) -> Path:
    BADGE_SIZE = 80
    BORDER_WIDTH = 3
    INSET = 20
    inner_size = BADGE_SIZE - 2 * BORDER_WIDTH

    with Image.open(image_path).convert("RGBA") as base:
        with Image.open(logo_path).convert("RGBA") as logo:
            logo_resized = logo.resize((inner_size, inner_size), Image.LANCZOS)

            # Circular mask for logo interior
            logo_mask = Image.new("L", (inner_size, inner_size), 0)
            ImageDraw.Draw(logo_mask).ellipse(
                (0, 0, inner_size - 1, inner_size - 1), fill=255
            )
            logo_circle = Image.new("RGBA", (inner_size, inner_size), (0, 0, 0, 0))
            logo_circle.paste(logo_resized, mask=logo_mask)

            # Badge canvas — white filled circle for border ring
            badge = Image.new("RGBA", (BADGE_SIZE, BADGE_SIZE), (0, 0, 0, 0))
            border_mask = Image.new("L", (BADGE_SIZE, BADGE_SIZE), 0)
            ImageDraw.Draw(border_mask).ellipse(
                (0, 0, BADGE_SIZE - 1, BADGE_SIZE - 1), fill=255
            )
            white_ring = Image.new("RGBA", (BADGE_SIZE, BADGE_SIZE), (255, 255, 255, 255))
            badge.paste(white_ring, mask=border_mask)
            badge.paste(logo_circle, (BORDER_WIDTH, BORDER_WIDTH), logo_circle)

            # Paste badge at top-right corner
            x = base.width - BADGE_SIZE - INSET
            y = INSET
            base.paste(badge, (x, y), badge)
            base.convert("RGB").save(image_path)

    return image_path
```

- [ ] **Step 4: Run to confirm they pass**

```bash
pytest tests/test_brand.py -v
```

Expected: all brand tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/brand.py tests/test_brand.py
git commit -m "feat(brand): add composite_badge — circular logo badge top-right corner"
```

---

## Task 7: image_gen.py — _pillow_text_card and generate_for_slide

**Files:**
- Create: `src/image_gen.py`
- Create: `tests/test_image_gen.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_image_gen.py`:

```python
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image


SAMPLE_SLIDE = {
    "slide_number": 1,
    "title": "NVIDIA drops RTX 50 series",
    "subtitle": "Next-gen GPUs unveiled",
    "body": "NVIDIA announced RTX 50 series with major performance gains.",
    "hashtags": "#NVIDIA #GPU",
    "image_prompt": "Futuristic cyberpunk GPU, neon cyan, 4K resolution",
}


def test_pillow_text_card_creates_image(tmp_path):
    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        from src.image_gen import _pillow_text_card
        result = _pillow_text_card(post_id=1, slide=SAMPLE_SLIDE)

    assert result.exists()
    img = Image.open(result)
    assert img.size == (1080, 1350)


def test_pillow_text_card_uses_slide_title(tmp_path):
    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        from src.image_gen import _pillow_text_card
        result = _pillow_text_card(post_id=1, slide=SAMPLE_SLIDE)

    # File must exist and not be empty
    assert result.stat().st_size > 0


def test_generate_for_slide_calls_flux_and_returns_path(tmp_path):
    fake_image_url = "https://replicate.example.com/output.png"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200

    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        with patch("src.image_gen.replicate.run", return_value=[fake_image_url]):
            with patch("src.image_gen.urllib.request.urlretrieve",
                       side_effect=lambda url, dest: Path(dest).write_bytes(fake_png)):
                with patch("src.image_gen.fetch_logo", return_value=None):
                    from src.image_gen import generate_for_slide
                    result = generate_for_slide(
                        post_id=1,
                        slide=SAMPLE_SLIDE,
                        brand_domain="nvidia.com",
                    )

    assert result.exists()
    assert result.name == "1_1.png"


def test_generate_for_slide_composites_badge_when_logo_found(tmp_path):
    fake_image_url = "https://replicate.example.com/output.png"
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    fake_logo = tmp_path / "nvidia.com.png"
    fake_logo.write_bytes(fake_png)

    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        with patch("src.image_gen.replicate.run", return_value=[fake_image_url]):
            with patch("src.image_gen.urllib.request.urlretrieve",
                       side_effect=lambda url, dest: Path(dest).write_bytes(fake_png)):
                with patch("src.image_gen.fetch_logo", return_value=fake_logo):
                    with patch("src.image_gen.composite_badge") as mock_badge:
                        from src.image_gen import generate_for_slide
                        generate_for_slide(
                            post_id=1,
                            slide=SAMPLE_SLIDE,
                            brand_domain="nvidia.com",
                        )

    mock_badge.assert_called_once()


def test_generate_for_slide_falls_back_to_text_card_on_flux_failure(tmp_path):
    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        with patch("src.image_gen.replicate.run", side_effect=Exception("Flux API down")):
            from src.image_gen import generate_for_slide
            result = generate_for_slide(
                post_id=1,
                slide=SAMPLE_SLIDE,
                brand_domain="nvidia.com",
            )

    assert result.exists()
    img = Image.open(result)
    assert img.size == (1080, 1350)  # Pillow fallback dimensions
```

- [ ] **Step 2: Run to confirm they fail**

```bash
pytest tests/test_image_gen.py -v
```

Expected: `ModuleNotFoundError` — `src/image_gen.py` doesn't exist yet.

- [ ] **Step 3: Create `src/image_gen.py`**

```python
import logging
import urllib.request
from pathlib import Path

import replicate
from PIL import Image, ImageDraw

from .brand import composite_badge, enrich_prompt, fetch_logo

logger = logging.getLogger(__name__)

_IMAGES_DIR = Path("data/images")


def _image_path(post_id: int, slide_number: int) -> Path:
    _IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    return _IMAGES_DIR / f"{post_id}_{slide_number}.png"


def _pillow_text_card(post_id: int, slide: dict) -> Path:
    path = _image_path(post_id, slide.get("slide_number", 0))
    img = Image.new("RGB", (1080, 1350), color="#1a1a2e")
    draw = ImageDraw.Draw(img)
    draw.text(
        (540, 675),
        slide.get("title", "Image unavailable"),
        fill="white",
        anchor="mm",
    )
    img.save(path)
    return path


def generate_for_slide(post_id: int, slide: dict, brand_domain: str | None) -> Path:
    try:
        enriched = enrich_prompt(slide["image_prompt"], brand_domain)
        output = replicate.run(
            "black-forest-labs/flux-schnell",
            input={
                "prompt": enriched,
                "aspect_ratio": "4:5",
                "output_format": "png",
                "num_outputs": 1,
            },
        )
        image_url = output[0]
        dest = _image_path(post_id, slide["slide_number"])
        urllib.request.urlretrieve(image_url, dest)

        if brand_domain:
            logo = fetch_logo(brand_domain)
            if logo:
                composite_badge(dest, logo)

        return dest
    except Exception as e:
        logger.error(
            "Image gen failed for post %d slide %d: %s — falling back to text card",
            post_id, slide.get("slide_number", 0), e,
        )
        return _pillow_text_card(post_id, slide)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
pytest tests/test_image_gen.py -v
```

Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/image_gen.py tests/test_image_gen.py
git commit -m "feat(image_gen): add generate_for_slide with Flux + Pillow fallback"
```

---

## Task 8: image_gen.py — generate_for_post

**Files:**
- Modify: `src/image_gen.py`
- Modify: `tests/test_image_gen.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_image_gen.py`:

```python
def test_generate_for_post_returns_path_per_slide(tmp_path):
    fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
    carousel = {
        "brand_domain": "nvidia.com",
        "total_slides": 2,
        "slides": [
            {**SAMPLE_SLIDE, "slide_number": 1},
            {**SAMPLE_SLIDE, "slide_number": 2, "title": "Slide 2"},
        ],
    }

    with patch("src.image_gen._IMAGES_DIR", tmp_path):
        with patch("src.image_gen.replicate.run", return_value=["https://example.com/img.png"]):
            with patch("src.image_gen.urllib.request.urlretrieve",
                       side_effect=lambda url, dest: Path(dest).write_bytes(fake_png)):
                with patch("src.image_gen.fetch_logo", return_value=None):
                    from src.image_gen import generate_for_post
                    paths = generate_for_post(
                        post_id=42,
                        carousel=carousel,
                        brand_domain="nvidia.com",
                    )

    assert len(paths) == 2
    assert paths[0].name == "42_1.png"
    assert paths[1].name == "42_2.png"
```

- [ ] **Step 2: Run to confirm it fails**

```bash
pytest tests/test_image_gen.py::test_generate_for_post_returns_path_per_slide -v
```

Expected: `ImportError` — `generate_for_post` not defined yet.

- [ ] **Step 3: Add `generate_for_post` to `src/image_gen.py`**

```python
def generate_for_post(post_id: int, carousel: dict, brand_domain: str | None) -> list[Path]:
    paths = []
    for slide in carousel.get("slides", []):
        path = generate_for_slide(post_id, slide, brand_domain)
        paths.append(path)
    return paths
```

- [ ] **Step 4: Run all image_gen tests**

```bash
pytest tests/test_image_gen.py -v
```

Expected: all 6 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/image_gen.py tests/test_image_gen.py
git commit -m "feat(image_gen): add generate_for_post — generates all slides for a post"
```

---

## Task 9: CLI — add --images command

**Files:**
- Modify: `cli.py`

This provides a manual trigger for image generation without requiring the M6 web UI.

- [ ] **Step 1: Add `cmd_images` to `cli.py`**

Add after `cmd_fix`:

```python
def cmd_images(post_id: int) -> None:
    import json
    from src.db import get_conn, save_image_paths
    from src.image_gen import generate_for_post

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
```

- [ ] **Step 2: Wire the argument in `main()`**

Add to `parser.add_argument` block:

```python
parser.add_argument("--images", type=int, metavar="POST_ID",
                    help="Generate images for a post by DB id (post must be approved)")
```

Add to the `if/elif` dispatch block:

```python
elif args.images:
    from dotenv import load_dotenv
    load_dotenv()
    cmd_images(args.images)
```

- [ ] **Step 3: Smoke-test the help output**

```bash
python cli.py --help
```

Expected: `--images POST_ID` appears in the help text.

- [ ] **Step 4: Run full test suite**

```bash
pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add cli.py
git commit -m "feat(cli): add --images <post_id> command for manual image generation"
```

---

## Self-Review Checklist

### Spec coverage

| Spec requirement | Task |
|---|---|
| `brand_domain` extraction in carousel_gen | Task 3 |
| Split `review_revise` → `review` + `revise` | Task 2 |
| `fetch_logo` with Clearbit + 90-day TTL | Task 4 |
| `enrich_prompt` with brand styles + cleanup + 1000-char cap | Task 5 |
| `composite_badge` top-right corner 80px circle | Task 6 |
| `_pillow_text_card` fallback | Task 7 |
| `generate_for_slide` with Flux + fallback | Task 7 |
| `generate_for_post` | Task 8 |
| `image_paths` DB column + `save_image_paths` | Task 1 |
| CLI manual trigger | Task 9 |

All spec sections covered. ✓

### Type consistency

- `fetch_logo(domain: str, max_age_days: int = 90) -> Path | None` — used consistently in `image_gen.py` and tests ✓
- `enrich_prompt(base_prompt: str, brand_domain: str | None) -> str` — consistent ✓
- `composite_badge(image_path: Path, logo_path: Path) -> Path` — consistent ✓
- `generate_for_slide(post_id: int, slide: dict, brand_domain: str | None) -> Path` — consistent ✓
- `generate_for_post(post_id: int, carousel: dict, brand_domain: str | None) -> list[Path]` — consistent ✓
- `save_image_paths(post_id: int, image_paths: list[str]) -> bool` — consistent ✓

### No placeholders

No TBDs, TODOs, or "add validation" stubs present. All code blocks are complete. ✓
