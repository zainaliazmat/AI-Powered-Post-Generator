import os
from pathlib import Path

_VALID_RENDERERS = frozenset({"pillow", "flux"})


def _get_renderer_name() -> str:
    name = os.getenv("IMAGE_RENDERER", "pillow")
    if name not in _VALID_RENDERERS:
        raise ValueError(
            f"Unknown IMAGE_RENDERER={name!r}. Valid options: {sorted(_VALID_RENDERERS)}"
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
