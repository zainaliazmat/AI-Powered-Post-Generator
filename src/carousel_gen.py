import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

import litellm

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
You are an expert social media strategist. Convert this news into an Instagram carousel.

## NEWS:
Title: {title}
Source: {source}
Summary: {summary}

## SLIDE BUDGET (CRITICAL — choose based on content):
- Complex/breaking news (major release, technical deep dive, comparison) → EXACTLY 6 slides
- Standard news (normal feature, regular update) → 4 or 5 slides
- Minor/small news (quick tip, minor release, simple announcement) → 2 or 3 slides

DO NOT ADD FILLER SLIDES. Every slide must deliver unique value.
MAXIMUM 6 SLIDES. MINIMUM 2 SLIDES.

## OUTPUT FORMAT (MUST BE VALID JSON — NO OTHER TEXT):
{{
  "brand_domain": "nvidia.com",
  "news_summary": "One powerful sentence summarizing the biggest takeaway",
  "total_slides": <2-6>,
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

## SLIDE STRUCTURE:
- Slide 1: Hook + headline (always required)
- Slides 2 to N-1: Core content, vary depth by complexity
- Slide N: CTA + recap + question for comments (always required)

## WRITING RULES:
- Max 2 emojis per slide
- Sentences under 15 words
- Write like a knowledgeable friend, not corporate

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


class ClaudeCarouselGenerator:
    def __init__(
        self,
        cache_dir: str = "carousel_cache",
        max_retries: int = 3,
    ):
        self.model = os.getenv("CAROUSEL_MODEL", "claude-haiku-4-5")
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
        content = f"v2|{article.get('title','')}{article.get('url','')}{article.get('summary','')}"
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
        try:
            resp = litellm.completion(
                model=self.model,
                max_tokens=2048,
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                num_retries=self.max_retries,
                timeout=30,
            )
            self.usage_stats["total_calls"] += 1
            self.usage_stats["total_input_tokens"] += resp.usage.prompt_tokens
            self.usage_stats["total_output_tokens"] += resp.usage.completion_tokens
            try:
                self.usage_stats["estimated_cost_usd"] += litellm.completion_cost(
                    completion_response=resp
                )
            except Exception:
                pass
            return resp.choices[0].message.content
        except Exception as e:
            logger.error("LLM API failed: %s", e)
            raise

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

    @staticmethod
    def _validate_carousel(carousel: dict) -> None:
        for field in ("news_summary", "total_slides", "slides"):
            if field not in carousel:
                raise ValueError(f"Carousel missing required field: {field}")
        total = carousel["total_slides"]
        if not isinstance(total, int) or not (2 <= total <= 6):
            raise ValueError(f"total_slides must be an integer 2-6, got {total!r}")
        slides = carousel["slides"]
        if len(slides) != total:
            raise ValueError(f"Expected {total} slides, got {len(slides)}")
        numbers = [s.get("slide_number") for s in slides]
        if numbers != list(range(1, total + 1)):
            raise ValueError(f"slide_numbers must be 1..{total} in order, got {numbers}")
        for slide in slides:
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
