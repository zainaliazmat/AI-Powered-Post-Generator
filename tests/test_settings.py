import sqlite3
from pathlib import Path

import pytest


def test_pipeline_settings_table_exists_after_init(tmp_db):
    """init_db creates the pipeline_settings table with a single row id=1."""
    import src.db as db
    with db.get_conn() as conn:
        # Table exists
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pipeline_settings'"
        ).fetchone()
        assert row is not None, "pipeline_settings table missing"
        # Exactly one row with id=1 (defaults inserted)
        rows = conn.execute("SELECT id FROM pipeline_settings").fetchall()
        assert len(rows) == 1
        assert rows[0]["id"] == 1


def test_pipeline_settings_migration_is_idempotent(tmp_db):
    """Calling init_db twice does not raise or duplicate rows."""
    import src.db as db
    db.init_db()  # second call
    with db.get_conn() as conn:
        rows = conn.execute("SELECT id FROM pipeline_settings").fetchall()
        assert len(rows) == 1


def test_get_settings_returns_defaults_on_first_read(tmp_db):
    from src.settings import DEFAULTS, get_settings
    assert get_settings() == DEFAULTS


def test_save_settings_round_trips_a_single_field(tmp_db):
    from src.settings import get_settings, save_settings
    save_settings({"time_window_hours": 24})
    assert get_settings()["time_window_hours"] == 24
    # Untouched field stayed at default
    assert get_settings()["per_source_max"] == 10


def test_save_settings_all_fields(tmp_db):
    from src.settings import get_settings, save_settings
    updates = {
        "time_window_hours": 48,
        "per_source_max": 20,
        "global_max_carousels": 12,
        "min_slides": 3,
        "max_slides": 8,
        "image_renderer": "flux",
    }
    save_settings(updates)
    after = get_settings()
    for k, v in updates.items():
        assert after[k] == v


@pytest.mark.parametrize("field,bad", [
    ("time_window_hours", 0),
    ("time_window_hours", 169),
    ("per_source_max", 0),
    ("per_source_max", 101),
    ("global_max_carousels", 0),
    ("global_max_carousels", 51),
    ("min_slides", 0),
    ("min_slides", 11),
    ("max_slides", 0),
    ("max_slides", 11),
    ("image_renderer", "midjourney"),
    ("time_window_hours", "twelve"),
])
def test_save_settings_rejects_out_of_range(tmp_db, field, bad):
    from src.settings import save_settings
    with pytest.raises(ValueError) as exc:
        save_settings({field: bad})
    assert field in str(exc.value)


def test_save_settings_rejects_min_greater_than_max(tmp_db):
    from src.settings import save_settings
    with pytest.raises(ValueError) as exc:
        save_settings({"min_slides": 7, "max_slides": 4})
    assert "min_slides" in str(exc.value) or "max_slides" in str(exc.value)


def test_restore_defaults(tmp_db):
    from src.settings import DEFAULTS, get_settings, restore_defaults, save_settings
    save_settings({"time_window_hours": 99, "image_renderer": "flux"})
    restore_defaults()
    assert get_settings() == DEFAULTS


def test_save_settings_rejects_unknown_field(tmp_db):
    from src.settings import save_settings
    with pytest.raises(ValueError) as exc:
        save_settings({"frobnicate_level": 9})
    assert "frobnicate_level" in str(exc.value)


def test_save_settings_returns_updated_settings(tmp_db):
    from src.settings import save_settings
    result = save_settings({"per_source_max": 25})
    assert result["per_source_max"] == 25
