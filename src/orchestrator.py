import hashlib
import json
import logging
import time
from pathlib import Path

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

from .carousel_gen import ClaudeCarouselGenerator
from .db import save_reviewed_post

logger = logging.getLogger(__name__)

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


async def run_review(post: dict) -> dict:
    """Spawn a Sonnet ReviewAgent for one post. Returns {score, issues, suggestions}."""
    article = post.get("article", {})
    carousel = post.get("carousel", {})

    prompt = _REVIEW_PROMPT.format(
        title=article.get("title", ""),
        summary=article.get("summary", ""),
        carousel_json=json.dumps(carousel, indent=2),
    )

    result_text = ""
    try:
        async for msg in query(
            prompt=prompt,
            options=ClaudeAgentOptions(
                model="claude-sonnet-4-6",
                effort="medium",
                max_turns=1,
                permission_mode="dontAsk",
            ),
        ):
            if isinstance(msg, ResultMessage) and msg.subtype == "success":
                result_text = msg.result
        return json.loads(result_text)
    except (json.JSONDecodeError, ValueError) as e:
        logger.warning("ReviewAgent returned non-JSON: %s — defaulting to score=5", e)
        return {"score": 5, "issues": ["parse error"], "suggestions": []}
    except Exception as e:
        logger.error("ReviewAgent failed: %s — defaulting to score=5", e)
        return {"score": 5, "issues": [str(e)], "suggestions": []}
