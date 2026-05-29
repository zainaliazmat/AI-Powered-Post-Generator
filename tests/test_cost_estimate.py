def test_pillow_renderer_has_zero_image_cost(tmp_db):
    from src.settings import DEFAULTS, estimate_run_cost
    s = {**DEFAULTS, "image_renderer": "pillow"}
    breakdown = estimate_run_cost(s)
    assert breakdown["images_usd"] == 0.0
    assert breakdown["total_usd"] >= breakdown["carousel_usd"]


def test_flux_renderer_scales_with_slide_count(tmp_db):
    from src.settings import DEFAULTS, estimate_run_cost
    low = {**DEFAULTS, "image_renderer": "flux", "min_slides": 2, "max_slides": 2}
    high = {**DEFAULTS, "image_renderer": "flux", "min_slides": 10, "max_slides": 10}
    assert estimate_run_cost(high)["images_usd"] > estimate_run_cost(low)["images_usd"]


def test_total_equals_sum_of_parts(tmp_db):
    from src.settings import DEFAULTS, estimate_run_cost
    b = estimate_run_cost(DEFAULTS)
    expected = round(
        b["carousel_usd"] + b["review_usd"] + b["revise_usd"] + b["images_usd"], 6
    )
    assert round(b["total_usd"], 6) == expected


def test_global_max_carousels_scales_linearly(tmp_db):
    from src.settings import DEFAULTS, estimate_run_cost
    one  = estimate_run_cost({**DEFAULTS, "global_max_carousels": 1})
    ten  = estimate_run_cost({**DEFAULTS, "global_max_carousels": 10})
    # 10x the carousels → ~10x the carousel_usd (allow for floating-point rounding)
    assert abs(ten["carousel_usd"] - one["carousel_usd"] * 10) < 1e-6
