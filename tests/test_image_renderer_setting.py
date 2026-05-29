def test_renderer_name_reads_settings_then_env(tmp_db, monkeypatch):
    """When a setting is saved, _get_renderer_name returns it regardless of env."""
    from src.settings import save_settings
    save_settings({"image_renderer": "flux"})
    monkeypatch.setenv("IMAGE_RENDERER", "pillow")
    from src.ImageGen.image_gen import _get_renderer_name
    assert _get_renderer_name() == "flux"


def test_renderer_falls_back_to_env_when_settings_missing(monkeypatch):
    """If settings can't be read (no DB), fall back to env var."""
    import src.ImageGen.image_gen as ig

    monkeypatch.setattr(ig, "_settings_renderer", lambda: None)
    monkeypatch.setenv("IMAGE_RENDERER", "flux")
    assert ig._get_renderer_name() == "flux"


def test_unknown_renderer_raises(tmp_db, monkeypatch):
    """Saving a bad renderer is blocked at settings level — but env override of garbage still raises."""
    import pytest

    import src.ImageGen.image_gen as ig
    monkeypatch.setattr(ig, "_settings_renderer", lambda: "midjourney")
    with pytest.raises(ValueError):
        ig._get_renderer_name()
