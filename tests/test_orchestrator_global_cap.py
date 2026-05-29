import json
from pathlib import Path


def test_generate_node_slices_to_global_max_carousels(tmp_path, monkeypatch):
    """If global_max_carousels=2 and 5 deduped articles exist, only 2 carousels are generated."""
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # 5 deduped articles on disk (output of the dedup node)
    articles = [{"title": f"t{i}", "url": f"u{i}", "summary": "s",
                 "source": "hn", "date": "2026-05-25T00:00:00Z"} for i in range(5)]
    (data_dir / "deduped_articles.json").write_text(json.dumps(articles))

    # Stub cmd_generate: mirrors real behavior — applies cap then produces one post per article.
    def fake_cmd_generate(force_refresh=False):
        from src.settings import get_settings
        arts = json.loads(Path("data/deduped_articles.json").read_text())
        cap = get_settings()["global_max_carousels"]
        arts = arts[:cap]
        posts_out = [
            {"article": a, "carousel": {"news_summary": "x", "total_slides": 4,
                                         "slides": [{"slide_number": i + 1} for i in range(4)]}}
            for a in arts
        ]
        Path("data/generated_posts.json").write_text(json.dumps(posts_out))

    import src.db as db
    import src.orchestrator as orch
    db.DB_PATH = tmp_path / "test.db"
    db.init_db()

    from src.settings import save_settings
    save_settings({"global_max_carousels": 2})

    monkeypatch.setattr(orch, "cmd_generate", fake_cmd_generate)

    state = {"force": False, "articles_count": 5, "unique_count": 5,
             "posts": [], "review_results": [], "reviewed_posts": [],
             "saved_count": 0, "stop_reason": None}

    result = orch._generate_node(state)
    assert len(result["posts"]) == 2


def test_generate_node_does_not_truncate_when_under_cap(tmp_path, monkeypatch):
    """If fewer articles than the cap, file is left intact and all are generated."""
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    articles = [{"title": f"t{i}", "url": f"u{i}", "summary": "s",
                 "source": "hn", "date": "2026-05-25T00:00:00Z"} for i in range(3)]
    (data_dir / "deduped_articles.json").write_text(json.dumps(articles))

    def fake_cmd_generate(force_refresh=False):
        arts = json.loads(Path("data/deduped_articles.json").read_text())
        posts_out = [{"article": a, "carousel": {"slides": []}} for a in arts]
        Path("data/generated_posts.json").write_text(json.dumps(posts_out))

    import src.db as db
    import src.orchestrator as orch
    db.DB_PATH = tmp_path / "test.db"
    db.init_db()

    from src.settings import save_settings
    save_settings({"global_max_carousels": 10})

    monkeypatch.setattr(orch, "cmd_generate", fake_cmd_generate)

    state = {"force": False, "articles_count": 3, "unique_count": 3,
             "posts": [], "review_results": [], "reviewed_posts": [],
             "saved_count": 0, "stop_reason": None}

    result = orch._generate_node(state)
    assert len(result["posts"]) == 3
