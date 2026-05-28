"""Runtime-editable pipeline knobs. Single source of truth backed by SQLite."""

from __future__ import annotations

from typing import Literal, TypedDict

from .db import get_conn
from .shared import now_iso


class PipelineSettings(TypedDict):
    time_window_hours: int
    per_source_max: int
    global_max_carousels: int
    min_slides: int
    max_slides: int
    image_renderer: Literal["pillow", "flux"]


DEFAULTS: PipelineSettings = {
    "time_window_hours": 12,
    "per_source_max": 10,
    "global_max_carousels": 8,
    "min_slides": 4,
    "max_slides": 6,
    "image_renderer": "pillow",
}

_FIELDS = tuple(DEFAULTS.keys())

_INT_BOUNDS: dict[str, tuple[int, int]] = {
    "time_window_hours": (1, 168),
    "per_source_max": (1, 100),
    "global_max_carousels": (1, 50),
    "min_slides": (1, 10),
    "max_slides": (1, 10),
}

_RENDERERS = frozenset({"pillow", "flux"})


def _validate(updates: dict) -> dict:
    """Return updates dict with values validated. Raises ValueError with field name(s) on failure."""
    errors: dict[str, str] = {}
    cleaned: dict = {}
    for k, v in updates.items():
        if k not in _FIELDS:
            errors[k] = f"unknown field '{k}'"
            continue
        if k == "image_renderer":
            if v not in _RENDERERS:
                errors[k] = f"must be one of {sorted(_RENDERERS)}"
            else:
                cleaned[k] = v
            continue
        # All other fields are integer-bounded
        try:
            iv = int(v)
        except (TypeError, ValueError):
            errors[k] = f"{k} must be an integer, got {v!r}"
            continue
        lo, hi = _INT_BOUNDS[k]
        if not (lo <= iv <= hi):
            errors[k] = f"{k} must be between {lo} and {hi}, got {iv}"
            continue
        cleaned[k] = iv
    if errors:
        raise ValueError(errors)
    return cleaned


def get_settings() -> PipelineSettings:
    """Read the single settings row. Inserts defaults if missing."""
    with get_conn() as conn:
        row = conn.execute(
            f"SELECT {', '.join(_FIELDS)} FROM pipeline_settings WHERE id = 1"
        ).fetchone()
    if row is None:
        # Defensive: migration should have seeded; if not, return defaults
        return dict(DEFAULTS)  # type: ignore[return-value]
    return {k: row[k] for k in _FIELDS}  # type: ignore[return-value]


def save_settings(updates: dict) -> PipelineSettings:
    """Validate & persist updates. Raises ValueError({field: msg}) on invalid input.

    `updates` may be a partial dict; only listed fields are changed.
    Enforces min_slides <= max_slides post-merge.
    """
    cleaned = _validate(updates)
    # Cross-field check: min_slides <= max_slides
    current = get_settings()
    merged = {**current, **cleaned}
    if merged["min_slides"] > merged["max_slides"]:
        raise ValueError({
            "min_slides": "min_slides cannot exceed max_slides",
            "max_slides": "max_slides must be >= min_slides",
        })
    set_clauses = ", ".join(f"{k} = ?" for k in cleaned) + ", updated_at = ?"
    params = list(cleaned.values()) + [now_iso()]
    with get_conn() as conn:
        conn.execute(
            f"UPDATE pipeline_settings SET {set_clauses} WHERE id = 1",
            params,
        )
    return get_settings()


def restore_defaults() -> PipelineSettings:
    """Reset every field to DEFAULTS."""
    return save_settings(dict(DEFAULTS))


# --- Cost model (tunable constants) ---
# Anthropic per-million-token prices, May 2026:
#   Haiku-4-5:  $0.80 input / $4.00 output
#   Sonnet-4-6: $3.00 input / $15.00 output
_HAIKU_IN  = 0.80 / 1_000_000
_HAIKU_OUT = 4.00 / 1_000_000
_SONNET_IN  = 3.00 / 1_000_000
_SONNET_OUT = 15.00 / 1_000_000

# Per-article token heuristics (calibrate by running the pipeline and comparing)
_CAROUSEL_IN, _CAROUSEL_OUT = 1500, 800
_REVIEW_IN, _REVIEW_OUT     = 1200, 400
_REVISE_IN, _REVISE_OUT     = 2000, 1000
_REVISE_RATIO = 0.30

# Flux per-slide cost via Replicate
_FLUX_PER_SLIDE_USD = 0.003


def estimate_run_cost(s: PipelineSettings) -> dict:
    """Estimate USD cost of one pipeline run given current settings."""
    n = s["global_max_carousels"]

    carousel = n * (_CAROUSEL_IN * _HAIKU_IN + _CAROUSEL_OUT * _HAIKU_OUT)
    review   = n * (_REVIEW_IN * _SONNET_IN + _REVIEW_OUT * _SONNET_OUT)
    revise   = n * _REVISE_RATIO * (_REVISE_IN * _HAIKU_IN + _REVISE_OUT * _HAIKU_OUT)

    if s["image_renderer"] == "flux":
        avg_slides = (s["min_slides"] + s["max_slides"]) / 2
        images = n * avg_slides * _FLUX_PER_SLIDE_USD
    else:
        images = 0.0

    return {
        "carousel_usd": round(carousel, 4),
        "review_usd":   round(review,   4),
        "revise_usd":   round(revise,   4),
        "images_usd":   round(images,   4),
        "total_usd":    round(carousel + review + revise + images, 4),
    }
