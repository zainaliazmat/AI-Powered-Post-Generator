import sqlite3


def test_init_db_creates_post_events_table(tmp_db):
    import src.db as db
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='post_events'"
        ).fetchall()
    assert len(rows) == 1


def test_init_db_creates_post_events_indexes(tmp_db):
    import src.db as db
    with db.get_conn() as conn:
        idx = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='post_events'"
        ).fetchall()}
    assert "idx_post_events_post" in idx
    assert "idx_post_events_post_stage" in idx
    assert "idx_post_events_run" in idx


def test_init_db_adds_run_id_column_to_generated_posts(tmp_db):
    import src.db as db
    with db.get_conn() as conn:
        cols = {r[1] for r in conn.execute(
            "PRAGMA table_info(generated_posts)"
        ).fetchall()}
    assert "run_id" in cols


def test_migration_is_idempotent(tmp_db):
    import src.db as db
    db.init_db()
    db.init_db()  # second call must not raise
    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='post_events'"
        ).fetchall()
    assert len(rows) == 1


def test_post_events_cascades_when_post_deleted(tmp_db):
    import src.db as db
    db.save_reviewed_post("h1", "https://x.com/a", "T", {"slides": []}, 8.0)
    with db.get_conn() as conn:
        post_id = conn.execute(
            "SELECT id FROM generated_posts WHERE article_hash='h1'"
        ).fetchone()["id"]
        conn.execute(
            "INSERT INTO post_events (post_id, stage, status) VALUES (?, 'review', 'ok')",
            (post_id,),
        )
        assert conn.execute(
            "SELECT COUNT(*) FROM post_events WHERE post_id=?", (post_id,)
        ).fetchone()[0] == 1
        conn.execute("DELETE FROM generated_posts WHERE id=?", (post_id,))
        assert conn.execute(
            "SELECT COUNT(*) FROM post_events WHERE post_id=?", (post_id,)
        ).fetchone()[0] == 0
