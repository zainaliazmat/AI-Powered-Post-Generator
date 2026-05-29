import base64


def _auth():
    return {"Authorization": "Basic " + base64.b64encode(b"admin:testpass").decode()}


def test_settings_page_renders(client, tmp_db):
    r = client.get("/settings", headers=_auth())
    assert r.status_code == 200
    assert "time_window_hours" in r.text


def test_put_settings_round_trip(client, tmp_db):
    r = client.put("/settings",
                   data={"time_window_hours": 24, "per_source_max": 5,
                         "global_max_carousels": 4, "min_slides": 3,
                         "max_slides": 6, "image_renderer": "pillow"},
                   headers=_auth())
    assert r.status_code == 200
    from src.settings import get_settings
    s = get_settings()
    assert s["time_window_hours"] == 24
    assert s["per_source_max"] == 5


def test_put_settings_validation_error_renders_form(client, tmp_db):
    r = client.put("/settings",
                   data={"time_window_hours": 9999, "per_source_max": 5,
                         "global_max_carousels": 4, "min_slides": 3,
                         "max_slides": 6, "image_renderer": "pillow"},
                   headers=_auth())
    assert r.status_code == 422
    # Form is re-rendered (response is HTML, not JSON)
    assert "time_window_hours" in r.text


def test_restore_defaults_route(client, tmp_db):
    from src.settings import save_settings, DEFAULTS, get_settings
    save_settings({"time_window_hours": 99})
    r = client.post("/settings/restore-defaults", headers=_auth())
    assert r.status_code == 200
    assert get_settings() == DEFAULTS


def test_cost_estimate_with_query_overrides(client, tmp_db):
    r = client.get("/settings/cost-estimate",
                   params={"global_max_carousels": 10, "image_renderer": "flux",
                           "min_slides": 4, "max_slides": 6,
                           "time_window_hours": 12, "per_source_max": 10},
                   headers=_auth())
    assert r.status_code == 200
    # The HTML fragment should contain a dollar amount
    assert "$" in r.text


def test_settings_page_shows_source_count(client, tmp_db, seed_source):
    """Config impact block shows active source count."""
    r = client.get("/settings", headers=_auth())
    assert r.status_code == 200
    assert "1 source" in r.text


def test_settings_page_shows_cost_range(client, tmp_db):
    """Cost card shows a range line (derived from total ±50%)."""
    r = client.get("/settings", headers=_auth())
    assert r.status_code == 200
    assert "Range:" in r.text


def test_settings_page_has_no_bar_chart_markup(client, tmp_db):
    """Bar chart divs have been removed from the cost card."""
    r = client.get("/settings", headers=_auth())
    assert r.status_code == 200
    assert "cost-line-bar" not in r.text


def test_put_settings_response_includes_source_count(client, tmp_db, seed_source):
    """PUT /settings HTMX response also contains config impact text."""
    r = client.put(
        "/settings",
        data={"time_window_hours": 24, "per_source_max": 5,
              "global_max_carousels": 4, "min_slides": 3,
              "max_slides": 6, "image_renderer": "pillow"},
        headers={**_auth(), "HX-Request": "true"},
    )
    assert r.status_code == 200
    assert "1 source" in r.text


def test_settings_page_renders_renderer_radio_cards(client, tmp_db):
    r = client.get("/settings", headers=_auth())
    assert r.status_code == 200
    assert 'type="radio"' in r.text
    assert 'name="image_renderer"' in r.text
    assert 'value="pillow"' in r.text
    assert 'value="flux"' in r.text
    # Old dropdown should be gone
    assert "<select" not in r.text


def test_put_settings_accepts_radio_renderer_value(client, tmp_db):
    """Saving via radio card value works the same as the old dropdown."""
    r = client.put(
        "/settings",
        data={"time_window_hours": 24, "per_source_max": 5,
              "global_max_carousels": 4, "min_slides": 3,
              "max_slides": 6, "image_renderer": "flux"},
        headers=_auth(),
    )
    assert r.status_code == 200
    from src.settings import get_settings
    assert get_settings()["image_renderer"] == "flux"
