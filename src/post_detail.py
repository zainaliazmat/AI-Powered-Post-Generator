"""Pure helpers for the post-detail page.

`diff_carousels` produces a per-slide field-level diff between pre- and
post-revise carousels. `safe_image_url` validates that a path event came from
ImageGen's expected output directory before turning it into a /static URL.
"""

from pathlib import PurePosixPath

_DIFF_FIELDS = ("title", "subtitle", "body", "hashtags", "image_prompt")


def diff_carousels(pre: dict, post: dict) -> list[dict] | dict:
    """Field-level diff between pre- and post-revise carousels.

    Returns a list of {slide_idx, changed, unchanged} per slide, where
    `changed[field] = (before, after)` and `unchanged[field] = value`.

    If `total_slides` differs between pre and post, returns
    `{"structure_changed": True, "pre_total": int, "post_total": int}` so
    the UI can render a single warning block rather than a misleading
    per-slide diff.
    """
    pre_slides = pre.get("slides", [])
    post_slides = post.get("slides", [])
    pre_total = pre.get("total_slides", len(pre_slides))
    post_total = post.get("total_slides", len(post_slides))
    if pre_total != post_total:
        return {
            "structure_changed": True,
            "pre_total": pre_total,
            "post_total": post_total,
        }

    diffs: list[dict] = []
    for i, (a, b) in enumerate(zip(pre_slides, post_slides)):
        changed: dict[str, tuple] = {}
        unchanged: dict[str, object] = {}
        for field in _DIFF_FIELDS:
            av = a.get(field)
            bv = b.get(field)
            if av != bv:
                changed[field] = (av, bv)
            else:
                unchanged[field] = av
        diffs.append({
            "slide_idx": a.get("slide_number", i + 1),
            "changed": changed,
            "unchanged": unchanged,
        })
    return diffs


def safe_image_url(path: object) -> str | None:
    """Turn a stored slide path into a /static/images/ URL, or None if unsafe.

    Safe means: a relative path beginning with `data/images/` and containing
    no `..` segments. Absolute paths and paths outside that directory return
    None so the template can render an inert <code> block instead of <img>.
    """
    if not isinstance(path, str) or not path:
        return None
    normalized = path.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p != ""]
    if not parts or ".." in parts:
        return None
    if normalized.startswith("/"):
        return None
    if len(parts) < 3 or parts[0] != "data" or parts[1] != "images":
        return None
    filename = PurePosixPath(*parts[2:]).name
    return f"/static/images/{filename}"
