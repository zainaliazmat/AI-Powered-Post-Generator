import hashlib
import json
import logging
import re
import time
from pathlib import Path

import anthropic

logger = logging.getLogger(__name__)


def _sanitize(v: str) -> str:
    return v.replace("{", "{{").replace("}", "}}")


_EMPTY_SUMMARIES = frozenset({"comments", "", "no summary", "n/a"})

_SLIDE_FIELDS = ("title", "subtitle", "body", "hashtags", "image_prompt")

_SYSTEM = (
    "You are an expert at generating structured JSON for social media carousels. "
    "Return ONLY valid JSON. Never include markdown formatting or explanations."
)

_PROMPT_TEMPLATE = """\
You are an expert social media content strategist specializing in AI/tech news.
Convert this news into an engaging Instagram carousel.

## NEWS:
Title: {title}
Source: {source}
Date: {date}
Categories: {categories}
Summary: {summary}

## OUTPUT FORMAT (MUST BE VALID JSON — NO OTHER TEXT):
{{
  "news_summary": "One powerful sentence summarizing the biggest takeaway",
  "total_slides": 8,
  "slides": [
    {{
      "slide_number": 1,
      "title": "CATCHY HOOK (max 40 chars, use emoji)",
      "subtitle": "Supporting line (max 60 chars)",
      "body": "2-3 punchy sentences. Short. Impactful. Use \\n for line breaks.",
      "hashtags": "#Hashtag1 #Hashtag2 #Hashtag3",
      "image_prompt": "50-100 words: subject, colors (neon cyan/purple/dark), lighting (cinematic), style (cyberpunk/futuristic), resolution (4K)"
    }}
  ]
}}

## SLIDE GUIDELINES:
- Slide 1: Hook + biggest headline
- Slides 2-3: Break down the story with data/numbers
- Slides 4-5: Second angle or implications
- Slides 6-7: Technical details simplified or future predictions
- Slide 8: CTA + recap + question for comments ("Which excites you most? 👇")

## WRITING RULES:
- Max 2 emojis per slide (🔥 🚀 💡 🤖 💰 📡 as relevant)
- Sentences under 15 words
- Create urgency/FOMO
- Write like a knowledgeable friend, not corporate

## CRITICAL: Return ONLY valid JSON. Start with {{ and end with }}."""


class ClaudeCarouselGenerator:
    def __init__(
        self,
        api_key: str,
        cache_dir: str = "carousel_cache",
        max_retries: int = 3,
    ):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = "claude-haiku-4-5"
        self.cache_dir = Path(cache_dir)
        self.max_retries = max_retries
        self.cache_dir.mkdir(exist_ok=True)
        self.usage_stats = {
            "total_calls": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "estimated_cost_usd": 0.0,
        }

    def _cache_key(self, article: dict) -> str:
        content = f"{article.get('title','')}{article.get('url','')}{article.get('summary','')}"
        return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()

    def _load_cache(self, key: str) -> dict | None:
        path = self.cache_dir / f"{key}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                logger.warning("Corrupt cache file %s — regenerating", path)
        return None

    def _save_cache(self, key: str, carousel: dict) -> None:
        path = self.cache_dir / f"{key}.json"
        path.write_text(json.dumps(carousel, indent=2, ensure_ascii=False), encoding="utf-8")

    def _build_prompt(self, article: dict) -> str:
        return _PROMPT_TEMPLATE.format(
            title=_sanitize(article.get("title", "")),
            source=_sanitize(article.get("source", "")),
            date=_sanitize(article.get("date", "")),
            categories=_sanitize(", ".join(article.get("categories", [])) or "AI, Technology"),
            summary=_sanitize(article.get("summary", "")),
        )

    def _call_api(self, prompt: str) -> str:
        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = self.client.messages.create(
                    model=self.model,
                    max_tokens=2048,
                    system=_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                self.usage_stats["total_calls"] += 1
                self.usage_stats["total_input_tokens"] += resp.usage.input_tokens
                self.usage_stats["total_output_tokens"] += resp.usage.output_tokens
                self.usage_stats["estimated_cost_usd"] += (
                    resp.usage.input_tokens / 1_000_000 * 1.0
                    + resp.usage.output_tokens / 1_000_000 * 5.0
                )
                return resp.content[0].text
            except anthropic.RateLimitError as e:
                last_err = e
                wait = 2 ** attempt
                logger.warning("Rate limited — waiting %ds (attempt %d/%d)", wait, attempt + 1, self.max_retries)
                time.sleep(wait)
            except anthropic.APIStatusError as e:
                if e.status_code < 500:
                    raise
                last_err = e
                logger.warning("API server error %d: %s — retrying", e.status_code, e)
                time.sleep(2 ** attempt)
            except anthropic.APIError as e:
                last_err = e
                logger.warning("API error: %s — retrying", e)
                time.sleep(2 ** attempt)
        raise RuntimeError(f"Claude API failed after {self.max_retries} attempts: {last_err}")

    def _extract_json(self, text: str) -> dict:
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        for pattern in (r"```json\s*([\s\S]*?)\s*```", r"```\s*([\s\S]*?)\s*```", r"(\{[\s\S]*\})"):
            m = re.search(pattern, text)
            if m:
                try:
                    return json.loads(m.group(1).strip())
                except json.JSONDecodeError:
                    continue
        raise ValueError(f"Could not extract JSON from response: {text[:200]}")

    def _validate_carousel(self, carousel: dict) -> None:
        for field in ("news_summary", "total_slides", "slides"):
            if field not in carousel:
                raise ValueError(f"Carousel missing required field: {field}")
        if len(carousel["slides"]) != 8:
            raise ValueError(f"Expected 8 slides, got {len(carousel['slides'])}")
        for slide in carousel["slides"]:
            for field in _SLIDE_FIELDS:
                if field not in slide:
                    raise ValueError(f"Slide {slide.get('slide_number','?')} missing field: {field}")

    def generate_carousel(self, article: dict, force_refresh: bool = False) -> dict:
        key = self._cache_key(article)
        if not force_refresh:
            cached = self._load_cache(key)
            if cached:
                logger.debug("Cache hit for: %s", article.get("title", "")[:60])
                return cached

        logger.info("Generating carousel for: %s", article.get("title", "")[:60])
        raw = self._call_api(self._build_prompt(article))
        carousel = self._extract_json(raw)
        self._validate_carousel(carousel)

        summary = (article.get("summary") or "").strip().lower()
        if summary in _EMPTY_SUMMARIES:
            carousel["low_confidence"] = True

        self._save_cache(key, carousel)
        return carousel

    def batch_generate(self, articles: list, force_refresh: bool = False) -> list:
        results = []
        for i, article in enumerate(articles):
            logger.info("Processing %d/%d: %s", i + 1, len(articles), article.get("title", "")[:50])
            try:
                carousel = self.generate_carousel(article, force_refresh=force_refresh)
                results.append({"article": article, "carousel": carousel, "success": True})
            except Exception as e:
                logger.error("Failed to generate carousel: %s", e)
                results.append({"article": article, "carousel": None, "success": False, "error": str(e)})
            if i < len(articles) - 1:
                time.sleep(1)
        return results

    def get_usage_report(self) -> dict:
        return {
            **self.usage_stats,
            "estimated_cost_usd_formatted": f"${self.usage_stats['estimated_cost_usd']:.4f}",
        }
