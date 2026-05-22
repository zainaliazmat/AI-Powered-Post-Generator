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


def generate_for_post(post_id: int, carousel: dict, brand_domain: str | None) -> list[Path]:
    paths = []
    for slide in carousel.get("slides", []):
        path = generate_for_slide(post_id, slide, brand_domain)
        paths.append(path)
    return paths
