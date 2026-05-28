"""Tests for src/post_detail.py — diff_carousels + safe_image_url."""


def _slide(n: int, **overrides) -> dict:
    base = {
        "slide_number": n,
        "title": f"Title {n}",
        "subtitle": f"Sub {n}",
        "body": f"Body {n}",
        "hashtags": "#tag",
        "image_prompt": f"Prompt {n}",
    }
    base.update(overrides)
    return base


def _carousel(slides: list[dict]) -> dict:
    return {"total_slides": len(slides), "slides": slides}


# ---------- diff_carousels ----------

def test_diff_carousels_all_equal_returns_unchanged_only():
    from src.post_detail import diff_carousels
    pre = _carousel([_slide(1), _slide(2)])
    post = _carousel([_slide(1), _slide(2)])
    out = diff_carousels(pre, post)
    assert isinstance(out, list)
    assert len(out) == 2
    for entry in out:
        assert entry["changed"] == {}
        assert set(entry["unchanged"].keys()) == {
            "title", "subtitle", "body", "hashtags", "image_prompt",
        }


def test_diff_carousels_one_field_changed_in_one_slide():
    from src.post_detail import diff_carousels
    pre = _carousel([_slide(1), _slide(2, title="OLD")])
    post = _carousel([_slide(1), _slide(2, title="NEW")])
    out = diff_carousels(pre, post)
    assert len(out) == 2
    # slide 1 unchanged
    assert out[0]["changed"] == {}
    # slide 2 title changed only
    assert out[1]["changed"] == {"title": ("OLD", "NEW")}
    # other fields preserved in unchanged
    assert "subtitle" in out[1]["unchanged"]
    assert "body" in out[1]["unchanged"]


def test_diff_carousels_slide_idx_uses_slide_number():
    from src.post_detail import diff_carousels
    pre = _carousel([_slide(1), _slide(2)])
    post = _carousel([_slide(1), _slide(2)])
    out = diff_carousels(pre, post)
    assert [e["slide_idx"] for e in out] == [1, 2]


def test_diff_carousels_total_slides_mismatch_returns_structure_changed():
    from src.post_detail import diff_carousels
    pre = _carousel([_slide(1), _slide(2)])
    post = _carousel([_slide(1)])
    out = diff_carousels(pre, post)
    assert isinstance(out, dict)
    assert out.get("structure_changed") is True
    assert out.get("pre_total") == 2
    assert out.get("post_total") == 1


def test_diff_carousels_handles_missing_fields_as_none():
    from src.post_detail import diff_carousels
    pre = _carousel([{"slide_number": 1, "title": "T"}])
    post = _carousel([{"slide_number": 1, "title": "T", "body": "added"}])
    out = diff_carousels(pre, post)
    assert out[0]["changed"] == {"body": (None, "added")}


# ---------- safe_image_url ----------

def test_safe_image_url_accepts_data_images_path():
    from src.post_detail import safe_image_url
    assert safe_image_url("data/images/42_1.png") == "/static/images/42_1.png"


def test_safe_image_url_accepts_pathlib_string_with_leading_slash():
    from src.post_detail import safe_image_url
    assert safe_image_url("/abs/data/images/42_1.png") is None


def test_safe_image_url_rejects_path_outside_data_images():
    from src.post_detail import safe_image_url
    assert safe_image_url("etc/passwd") is None
    assert safe_image_url("data/secrets/key.pem") is None


def test_safe_image_url_rejects_dotdot_segments():
    from src.post_detail import safe_image_url
    assert safe_image_url("data/images/../../etc/passwd") is None
    assert safe_image_url("../../etc/passwd") is None


def test_safe_image_url_rejects_empty_and_none():
    from src.post_detail import safe_image_url
    assert safe_image_url("") is None
    assert safe_image_url(None) is None


def test_safe_image_url_preserves_subpath_filename():
    from src.post_detail import safe_image_url
    assert safe_image_url("data/images/42_3.png") == "/static/images/42_3.png"
