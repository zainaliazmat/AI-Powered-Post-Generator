import datetime
import hashlib
import json
import logging
import os
import re
import signal
import sqlite3
import sys
import time
import traceback
from contextlib import contextmanager
from pathlib import Path
from typing import TypedDict

import litellm
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cli import cmd_scrape, cmd_dedup, cmd_generate  # noqa: E402

from . import db
from .carousel_gen import ClaudeCarouselGenerator
from .db import save_reviewed_post
from .prompts import (
    REVIEW_SYSTEM as _REVIEW_SYSTEM,
    REVIEW_USER_TEMPLATE as _REVIEW_USER,
    REVISE_SYSTEM as _REVISE_SYSTEM,
    REVISE_USER_TEMPLATE as _REVISE_USER,
)

logger = logging.getLogger(__name__)


# ---------- State ----------

class PipelineState(TypedDict, total=False):
    run_id: int
    force: bool
    articles_count: int
    unique_count: int
    posts: list
    review_results: list       # [{post, score, suggestions}, ...]
    reviewed_posts: list
    saved_count: int
    saved_post_ids: list
    images_count: int
    stop_reason: str | None
    event_buffer: list         # [{article_hash, stage, status, prompt_vars, output, duration_ms}, ...]


# ---------- Status helpers ----------

def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _short_traceback(e: BaseException, limit: int = 2000) -> str:
    return "".join(traceback.format_exception(type(e), e, e.__traceback__))[:limit]


@contextmanager
def _step(run_id: int | None, node: str):
    """Mark a step running on entry; ok/failed on exit. No-op if run_id is None."""
    if run_id is None:
        yield
        return
    db.update_run_step(run_id, node, status="running", started_at=_now_iso())
    try:
        yield
    except BaseException as e:
        db.update_run_step(
            run_id, node,
            status="failed",
            error=_short_traceback(e),
            finished_at=_now_iso(),
        )
        raise
    else:
        db.update_run_step(run_id, node, status="ok", finished_at=_now_iso())


def _progress(run_id: int | None, node: str, text: str) -> None:
    if run_id is None:
        return
    db.update_run_step(run_id, node, progress=text)


# ---------- LLM helpers ----------

_MAX_REVISE_ATTEMPTS = 2


def _repair_json(text: str) -> dict | None:
    text = text.strip()
    # Strip markdown fences (handles ```json, ```, etc.)
    text = re.sub(r'^```\w*\n?', '', text)
    text = re.sub(r'\n?```$', '', text)
    text = text.strip()
    # Extract from first { to last } to skip preamble/postamble
    first_brace = text.find('{')
    last_brace = text.rfind('}')
    if first_brace != -1 and last_brace != -1:
        text = text[first_brace:last_brace + 1]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fix common LLM mistakes: trailing commas
        text = re.sub(r',\s*}', '}', text)
        text = re.sub(r',\s*]', ']', text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None


def run_review(post: dict) -> dict:
    article = post.get("article", {})
    carousel = post.get("carousel", {})
    messages = [
        {"role": "system", "content": _REVIEW_SYSTEM},
        {"role": "user", "content": _REVIEW_USER.format(
            title=article.get("title", ""),
            summary=article.get("summary", ""),
            carousel_json=json.dumps(carousel, indent=2),
        )},
    ]
    try:
        resp = litellm.completion(
            model=os.getenv("REVIEW_MODEL", "claude-sonnet-4-6"),
            max_tokens=1024,
            messages=messages,
            num_retries=2,
            timeout=30,
        )
        parsed = _repair_json(resp.choices[0].message.content)
        if parsed and "score" in parsed:
            return parsed
        logger.warning("ReviewAgent: repaired JSON missing 'score' field — defaulting to score=5")
        return {"score": 5, "issues": ["parse error"], "suggestions": []}
    except Exception as e:
        logger.error("ReviewAgent failed: %s — defaulting to score=5", e)
        return {"score": 5, "issues": [str(e)], "suggestions": []}


def _call_revise_llm(carousel_json: str, suggestions_json: str, extra_hint: str = "") -> dict | None:
    system = _REVISE_SYSTEM + (f"\n\nEXTRA: {extra_hint}" if extra_hint else "")
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": _REVISE_USER.format(
            carousel_json=carousel_json,
            suggestions=suggestions_json,
        )},
    ]
    resp = litellm.completion(
        model=os.getenv("REVISE_MODEL", "claude-haiku-4-5"),
        max_tokens=2048,
        messages=messages,
        num_retries=2,
        timeout=30,
    )
    return _repair_json(resp.choices[0].message.content)


def _run_revise_with_meta(
    post: dict,
    suggestions: list,
    gen: "ClaudeCarouselGenerator | None" = None,
) -> tuple[dict, dict]:
    """Same behavior as run_revise; also returns {attempts, fell_back} metadata."""
    _gen = gen or ClaudeCarouselGenerator()
    original_carousel = post.get("carousel", {})
    original_slide_count = original_carousel.get("total_slides", 0)
    carousel_json = json.dumps(original_carousel, indent=2)
    suggestions_json = json.dumps(suggestions, indent=2)

    for attempt in range(_MAX_REVISE_ATTEMPTS):
        try:
            extra_hint = (
                f"CRITICAL: The original has {original_slide_count} slides. "
                f"Your output MUST also have exactly {original_slide_count} slides."
                if attempt > 0 else ""
            )
            revised = _call_revise_llm(carousel_json, suggestions_json, extra_hint)
            if revised is None:
                logger.warning("ReviseAgent attempt %d: non-JSON output", attempt + 1)
                continue
            if revised.get("total_slides") != original_slide_count:
                logger.warning(
                    "ReviseAgent attempt %d: slide count mismatch (%s vs %d)",
                    attempt + 1, revised.get("total_slides"), original_slide_count,
                )
                continue
            _gen._validate_carousel(revised)
            return revised, {"attempts": attempt + 1, "fell_back": False}
        except Exception as e:
            logger.warning("ReviseAgent attempt %d failed: %s", attempt + 1, e)

    logger.warning("ReviseAgent failed after %d attempts — keeping original", _MAX_REVISE_ATTEMPTS)
    return original_carousel, {"attempts": _MAX_REVISE_ATTEMPTS, "fell_back": True}


def run_revise(post: dict, suggestions: list) -> dict:
    carousel, _meta = _run_revise_with_meta(post, suggestions)
    return carousel


# ---------- LangGraph nodes ----------

def _article_hash(article: dict) -> str:
    content = (
        f"{article.get('title', '')}"
        f"{article.get('url', '')}"
        f"{article.get('summary', '')}"
    )
    return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()


def _scrape_node(state: PipelineState) -> dict:
    run_id = state.get("run_id")
    with _step(run_id, "scrape"):
        cmd_scrape()
        articles_path = Path("data/latest_articles.json")
        articles = json.loads(articles_path.read_text(encoding="utf-8")) if articles_path.exists() else []
        if not articles:
            _progress(run_id, "scrape", "0 articles")
            return {"articles_count": 0, "stop_reason": "no new articles scraped"}
        _progress(run_id, "scrape", f"{len(articles)} articles")
        return {"articles_count": len(articles)}


def _dedup_node(state: PipelineState) -> dict:
    run_id = state.get("run_id")
    with _step(run_id, "dedup"):
        cmd_dedup()
        deduped_path = Path("data/deduped_articles.json")
        deduped = json.loads(deduped_path.read_text(encoding="utf-8")) if deduped_path.exists() else []
        if not deduped:
            _progress(run_id, "dedup", "0 unique")
            return {"unique_count": 0, "stop_reason": "all articles were duplicates"}
        _progress(run_id, "dedup", f"{len(deduped)} unique")
        return {"unique_count": len(deduped)}


def _generate_node(state: PipelineState) -> dict:
    run_id = state.get("run_id")
    with _step(run_id, "generate"):
        cmd_generate(force_refresh=state["force"])
        posts_path = Path("data/generated_posts.json")
        posts = json.loads(posts_path.read_text(encoding="utf-8")) if posts_path.exists() else []
        if not posts:
            _progress(run_id, "generate", "0 carousels")
            return {"posts": [], "stop_reason": "carousel generation produced no posts"}
        _progress(run_id, "generate", f"{len(posts)} carousels")
        return {"posts": posts}


def _review_node(state: PipelineState) -> dict:
    run_id = state.get("run_id")
    with _step(run_id, "review"):
        posts = state["posts"]
        results = []
        buffer = list(state.get("event_buffer", []))
        for i, post in enumerate(posts):
            article = post.get("article", {})
            carousel = post.get("carousel", {})
            title = article.get("title", "")[:50]
            _progress(run_id, "review", f"Reviewing {i + 1}/{len(posts)} posts")
            logger.info("Reviewing post %d/%d: %s", i + 1, len(posts), title)
            print(f"  Reviewing {i + 1}/{len(posts)}: {title}...", flush=True)
            started = time.monotonic()
            review = run_review(post)
            duration_ms = int((time.monotonic() - started) * 1000)
            score = float(review.get("score", 5))
            results.append({
                "post": post,
                "score": score,
                "suggestions": review.get("suggestions", []),
            })
            buffer.append({
                "article_hash": _article_hash(article),
                "stage": "review",
                "status": "ok",
                "prompt_vars": {
                    "title": article.get("title", ""),
                    "summary": article.get("summary", ""),
                    "carousel_json": carousel,
                },
                "output": {
                    "score": review.get("score", 5),
                    "issues": review.get("issues", []),
                    "suggestions": review.get("suggestions", []),
                    "raw": None,
                },
                "duration_ms": duration_ms,
            })
            if i < len(posts) - 1:
                time.sleep(1)
        return {"review_results": results, "event_buffer": buffer}


def _revise_node(state: PipelineState) -> dict:
    run_id = state.get("run_id")
    with _step(run_id, "revise"):
        reviewed = []
        items = state["review_results"]
        buffer = list(state.get("event_buffer", []))
        from .settings import get_settings
        _gen = ClaudeCarouselGenerator(settings=get_settings())
        for i, item in enumerate(items):
            post = item["post"]
            score = item["score"]
            article = post.get("article", {})
            title = article.get("title", "")[:50]
            _progress(run_id, "revise", f"Revising {i + 1}/{len(items)} posts")
            if score < 7:
                logger.info("  Score %.1f — revising: %s", score, title)
                print(f"    Score {score:.0f} — revising...", flush=True)
                pre_carousel = post.get("carousel", {})
                started = time.monotonic()
                revised_carousel, meta = _run_revise_with_meta(post, item["suggestions"], gen=_gen)
                duration_ms = int((time.monotonic() - started) * 1000)
                post = {**post, "carousel": revised_carousel}
                buffer.append({
                    "article_hash": _article_hash(article),
                    "stage": "revise",
                    "status": "ok",
                    "prompt_vars": {
                        "pre_carousel": pre_carousel,
                        "suggestions": item["suggestions"],
                    },
                    "output": {
                        "post_carousel": revised_carousel,
                        "attempts": meta["attempts"],
                        "fell_back": meta["fell_back"],
                    },
                    "duration_ms": duration_ms,
                })
            else:
                logger.info("  Score %.1f — approved: %s", score, title)
                print(f"    Score {score:.0f} — approved", flush=True)
            reviewed.append({"post": post, "score": score})
        return {"reviewed_posts": reviewed, "event_buffer": buffer}


def _route_after_review(state: PipelineState) -> str:
    """Pure routing decision: revise if any score < 7, else save_draft."""
    return "revise" if any(r["score"] < 7 for r in state.get("review_results", [])) else "save_draft"


def _save_draft_node(state: PipelineState) -> dict:
    run_id = state.get("run_id")

    # If we arrived without going through revise, mark revise as skipped so the
    # UI shows the correct state during the run (not just after it ends).
    if run_id is not None and not state.get("revised_posts") and not state.get("reviewed_posts"):
        needs_revise = any(r["score"] < 7 for r in state.get("review_results", []))
        if not needs_revise:
            db.update_run_step(
                run_id, "revise",
                status="skipped", finished_at=_now_iso(),
            )

    with _step(run_id, "save_draft"):
        # Use reviewed_posts if revise ran; otherwise convert from review_results directly
        posts_to_save = state.get("reviewed_posts") or [
            {"post": r["post"], "score": r["score"]}
            for r in state.get("review_results", [])
        ]
        saved = 0
        saved_post_ids: list[int] = []
        buffer = list(state.get("event_buffer", []))
        for item in posts_to_save:
            post = item["post"]
            article = post.get("article", {})
            carousel = post.get("carousel", {})
            carousel["og_image_url"] = article.get("og_image_url", "")
            article_hash = _article_hash(article)
            ok = save_reviewed_post(
                article_hash=article_hash,
                article_url=article.get("url", ""),
                article_title=article.get("title", ""),
                carousel_json=carousel,
                review_score=item["score"],
            )
            if ok:
                saved += 1
            with db.get_conn() as conn:
                row = conn.execute(
                    "SELECT id FROM generated_posts WHERE article_hash = ?",
                    (article_hash,),
                ).fetchone()
            if not row:
                continue
            post_id = row["id"]
            saved_post_ids.append(post_id)

            remaining: list[dict] = []
            for event in buffer:
                if event["article_hash"] != article_hash:
                    remaining.append(event)
                    continue
                try:
                    db.insert_post_event(
                        post_id=post_id, run_id=run_id,
                        stage=event["stage"], status=event["status"],
                        prompt_vars=event.get("prompt_vars"),
                        output=event.get("output"),
                        duration_ms=event.get("duration_ms"),
                    )
                except Exception as e:
                    logger.warning("insert_post_event failed: %s", e)
            buffer = remaining

            if run_id is not None:
                try:
                    db.set_post_run_id(post_id, run_id)
                except Exception as e:
                    logger.warning("set_post_run_id failed: %s", e)

        if buffer:
            logger.warning(
                "save_draft: %d buffered event(s) had no matching saved post; dropping",
                len(buffer),
            )

        return {
            "saved_count": saved,
            "saved_post_ids": saved_post_ids,
            "event_buffer": [],
        }


def _images_node(state: PipelineState) -> dict:
    run_id = state.get("run_id")
    post_ids = state.get("saved_post_ids", [])
    with _step(run_id, "images"):
        if not post_ids:
            _progress(run_id, "images", "no posts to render")
            return {"images_count": 0}
        from .db import get_post, save_image_paths
        from .ImageGen import generate_for_post_with_events
        renderer = os.getenv("IMAGE_RENDERER", "pillow")
        rendered = 0
        for i, post_id in enumerate(post_ids):
            _progress(
                run_id, "images",
                f"Rendering post {i + 1}/{len(post_ids)} (id={post_id})",
            )
            post = get_post(post_id)
            if not post:
                logger.warning("images: post id %s not found, skipping", post_id)
                continue
            try:
                carousel = json.loads(post["carousel_json"])
            except (json.JSONDecodeError, TypeError):
                logger.warning("images: invalid carousel_json for post %s, skipping", post_id)
                continue
            brand_domain = carousel.get("brand_domain")
            started = time.monotonic()
            paths, slide_events = generate_for_post_with_events(
                post_id=post_id,
                carousel=carousel,
                brand_domain=brand_domain,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            save_image_paths(post_id, [str(p) for p in paths])
            rendered += 1
            try:
                db.insert_post_event(
                    post_id=post_id, run_id=run_id,
                    stage="images", status="ok",
                    prompt_vars={"renderer": renderer, "brand_domain": brand_domain},
                    output={"slides": slide_events},
                    duration_ms=duration_ms,
                )
            except Exception as e:
                logger.warning("insert_post_event(images) failed: %s", e)
        return {"images_count": rendered}


# ---------- Graph assembly ----------

def _build_graph(checkpointer) -> object:
    builder = StateGraph(PipelineState)
    builder.add_node("scrape", _scrape_node)
    builder.add_node("dedup", _dedup_node)
    builder.add_node("generate", _generate_node)
    builder.add_node("review", _review_node)
    builder.add_node("revise", _revise_node)
    builder.add_node("save_draft", _save_draft_node)
    builder.add_node("images", _images_node)

    builder.set_entry_point("scrape")
    builder.add_conditional_edges("scrape", lambda s: END if s.get("stop_reason") else "dedup")
    builder.add_conditional_edges("dedup", lambda s: END if s.get("stop_reason") else "generate")
    builder.add_conditional_edges("generate", lambda s: END if s.get("stop_reason") else "review")
    builder.add_conditional_edges("review", _route_after_review)
    builder.add_edge("revise", "save_draft")
    builder.add_edge("save_draft", "images")
    builder.add_edge("images", END)

    return builder.compile(checkpointer=checkpointer)


# ---------- Entry point ----------

def run_pipeline(force: bool = False, run_id: int | None = None) -> None:
    load_dotenv()

    if run_id is None:
        run_id = db.create_pipeline_run(trigger="cli")
        if run_id is None:
            logger.error("Another pipeline run is already active; aborting.")
            sys.exit(2)

    db.update_run(run_id, pid=os.getpid())

    def _on_signal(signum, _frame):
        db.cancel_running_step(run_id)
        db.finish_pipeline_run(run_id, status="cancelled")
        sys.exit(0)

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    today = datetime.date.today().isoformat()
    thread_id = f"pipeline-{today}-{int(time.time())}" if force else f"pipeline-{today}"

    checkpoint_path = Path("data/pipeline_checkpoints.db")
    checkpoint_path.parent.mkdir(exist_ok=True)

    conn = sqlite3.connect(str(checkpoint_path), check_same_thread=False)
    final: dict = {}
    try:
        saver = SqliteSaver(conn)
        graph = _build_graph(saver)
        config = {"configurable": {"thread_id": thread_id}}

        initial_state: PipelineState = {
            "run_id": run_id,
            "force": force,
            "articles_count": 0,
            "unique_count": 0,
            "posts": [],
            "review_results": [],
            "reviewed_posts": [],
            "saved_count": 0,
            "saved_post_ids": [],
            "images_count": 0,
            "stop_reason": None,
        }

        print(f"Starting pipeline (run #{run_id})...")
        for event in graph.stream(initial_state, config=config):
            for node_name, node_state in event.items():
                if node_name == "scrape" and node_state.get("articles_count", 0):
                    print(f"  ✓ Scraped {node_state['articles_count']} articles")
                elif node_name == "dedup" and node_state.get("unique_count", 0):
                    print(f"  ✓ Deduped to {node_state['unique_count']} unique")
                elif node_name == "generate" and node_state.get("posts"):
                    print(f"  ✓ Generated {len(node_state['posts'])} carousels")
                elif node_name == "review" and node_state.get("review_results"):
                    print(f"  ✓ Reviewed {len(node_state['review_results'])} posts")
                elif node_name == "revise" and node_state.get("reviewed_posts"):
                    print(f"  ✓ Revised {len(node_state['reviewed_posts'])} posts")
                elif node_name == "save_draft":
                    print(f"  ✓ Saved {node_state.get('saved_count', 0)} to DB")
                elif node_name == "images":
                    print(f"  ✓ Rendered {node_state.get('images_count', 0)} post(s)")
                final.update(node_state)

        if final.get("stop_reason"):
            print(f"\nSTOP: {final['stop_reason']}")
        else:
            print(
                f"\nPipeline complete:\n"
                f"  Scraped  : {final.get('articles_count', 0)}\n"
                f"  Unique   : {final.get('unique_count', 0)}\n"
                f"  Generated: {len(final.get('posts', []))}\n"
                f"  Reviewed : {len(final.get('reviewed_posts', []))}\n"
                f"  Saved    : {final.get('saved_count', 0)}\n"
                f"  Images   : {final.get('images_count', 0)}"
            )
    except Exception as e:
        db.finish_pipeline_run(
            run_id, status="failed", error=_short_traceback(e),
        )
        raise
    else:
        db.finish_pipeline_run(
            run_id,
            status="stopped" if final.get("stop_reason") else "ok",
            stop_reason=final.get("stop_reason"),
        )
    finally:
        conn.close()
