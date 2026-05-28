import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_VALID_RENDERERS = frozenset({"pillow", "flux"})


def _settings_renderer() -> str | None:
    """Read image_renderer from settings; returns None on any failure."""
    try:
        from src.settings import get_settings
        return get_settings()["image_renderer"]
    except Exception as e:
        logger.warning("settings unreachable, falling back to env: %s", e)
        return None


def _get_renderer_name() -> str:
    name = _settings_renderer() or os.getenv("IMAGE_RENDERER", "pillow")
    if name not in _VALID_RENDERERS:
        raise ValueError(
            f"Unknown image renderer {name!r}. Valid options: {sorted(_VALID_RENDERERS)}"
        )
    return name


def generate_for_slide(
    post_id: int,
    slide: dict,
    brand_domain: str | None,
    og_image_url: str = "",
) -> Path:
    renderer = _get_renderer_name()
    if renderer == "pillow":
        from .image_gen_pillow import generate_for_slide as _impl
        return _impl(post_id, slide, brand_domain, og_image_url=og_image_url)
    from .image_gen_flux import generate_for_slide as _impl
    # og_image_url is intentionally ignored by the Flux renderer; Flux uses slide["image_prompt"]
    return _impl(post_id, slide, brand_domain)


def generate_for_post(
    post_id: int,
    carousel: dict,
    brand_domain: str | None,
) -> list[Path]:
    renderer = _get_renderer_name()
    if renderer == "pillow":
        from .image_gen_pillow import generate_for_post as _impl
        return _impl(post_id, carousel, brand_domain)
    from .image_gen_flux import generate_for_post as _impl
    return _impl(post_id, carousel, brand_domain)


def generate_for_post_with_events(
    post_id: int,
    carousel: dict,
    brand_domain: str | None,
) -> tuple[list[Path], list[dict]]:
    renderer = _get_renderer_name()
    if renderer == "pillow":
        from .image_gen_pillow import generate_for_post_with_events as _impl
        return _impl(post_id, carousel, brand_domain)
    from .image_gen_flux import generate_for_post_with_events as _impl
    return _impl(post_id, carousel, brand_domain)
