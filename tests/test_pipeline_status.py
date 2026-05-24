import os
import threading
from unittest.mock import patch, MagicMock

import pytest

os.environ.setdefault("DASHBOARD_PASSWORD", "testpass")
os.environ.setdefault("DASHBOARD_USERNAME", "admin")


AUTH = ("admin", "testpass")


# ── DB helper tests (atomic claim + reconciler) ─────────────────────────────

def test_create_pipeline_run_seeds_seven_step_rows(tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    assert run_id is not None

    steps = db.get_run_steps(run_id)
    assert len(steps) == 7
    expected = ["scrape", "dedup", "generate", "review", "revise", "save_draft", "images"]
    assert [s["node"] for s in steps] == expected
    assert all(s["status"] == "pending" for s in steps)


def test_create_pipeline_run_returns_none_when_running_row_exists(tmp_db):
    import src.db as db

    first = db.create_pipeline_run(trigger="cli")
    assert first is not None
    second = db.create_pipeline_run(trigger="dashboard")
    assert second is None


def test_create_pipeline_run_concurrent_atomic_claim(tmp_db):
    """Concurrent creates: exactly one returns an id, the other returns None."""
    import src.db as db

    results: list[int | None] = []
    lock = threading.Lock()

    def worker():
        rid = db.create_pipeline_run(trigger="dashboard")
        with lock:
            results.append(rid)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads: t.start()
    for t in threads: t.join()

    successes = [r for r in results if r is not None]
    failures = [r for r in results if r is None]
    assert len(successes) == 1
    assert len(failures) == 1

    with db.get_conn() as conn:
        running_count = conn.execute(
            "SELECT COUNT(*) FROM pipeline_runs WHERE status='running'"
        ).fetchone()[0]
    assert running_count == 1


def test_reconcile_stale_runs_marks_orphan_failed(tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    db.update_run(run_id, pid=999999)  # PID highly unlikely to exist

    n = db.reconcile_stale_runs()
    assert n == 1

    run = db.get_run(run_id)
    assert run["status"] == "failed"
    assert "subprocess died" in run["error"]


def test_reconcile_stale_runs_leaves_live_pid_alone(tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    db.update_run(run_id, pid=os.getpid())

    n = db.reconcile_stale_runs()
    assert n == 0

    run = db.get_run(run_id)
    assert run["status"] == "running"


def test_reconcile_stale_runs_marks_null_pid_failed(tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="dashboard")
    # PID stays NULL — simulates Popen-never-ran

    n = db.reconcile_stale_runs()
    assert n == 1
    run = db.get_run(run_id)
    assert run["status"] == "failed"


def test_finish_pipeline_run_clears_pid_and_sets_status(tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    db.update_run(run_id, pid=12345)
    db.finish_pipeline_run(run_id, status="ok")

    run = db.get_run(run_id)
    assert run["status"] == "ok"
    assert run["pid"] is None
    assert run["finished_at"] is not None


def test_cancel_running_step_marks_only_running(tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    db.update_run_step(run_id, "review", status="running", started_at=db.now_iso())
    db.cancel_running_step(run_id)

    steps = {s["node"]: s for s in db.get_run_steps(run_id)}
    assert steps["review"]["status"] == "cancelled"
    assert steps["scrape"]["status"] == "pending"  # untouched


# ── FastAPI route tests ─────────────────────────────────────────────────────

def test_pipeline_start_spawns_subprocess(client, tmp_db):
    import src.db as db

    fake_proc = MagicMock()
    with patch("src.main.subprocess.Popen", return_value=fake_proc) as popen:
        resp = client.post("/pipeline/start", auth=AUTH)
    assert resp.status_code == 200

    runs = db.get_recent_runs(limit=10)
    assert len(runs) == 1
    assert runs[0]["trigger"] == "dashboard"
    assert runs[0]["status"] == "running"

    steps = db.get_run_steps(runs[0]["id"])
    assert len(steps) == 7

    popen.assert_called_once()
    args = popen.call_args[0][0]
    assert "--run-id" in args
    assert str(runs[0]["id"]) in args


def test_pipeline_start_409_when_already_running(client, tmp_db):
    import src.db as db

    db.create_pipeline_run(trigger="cli")
    with patch("src.main.subprocess.Popen"):
        resp = client.post("/pipeline/start", auth=AUTH)
    assert resp.status_code == 409

    with db.get_conn() as conn:
        count = conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0]
    assert count == 1


def test_pipeline_start_requires_auth(client):
    resp = client.post("/pipeline/start")
    assert resp.status_code == 401


def test_pipeline_stop_sends_sigterm_to_active_pid(client, tmp_db):
    import signal as signal_module
    import src.db as db

    run_id = db.create_pipeline_run(trigger="dashboard")
    db.update_run(run_id, pid=os.getpid())

    with patch("src.main.os.kill") as kill_mock:
        resp = client.post("/pipeline/stop", auth=AUTH)
    assert resp.status_code == 200
    kill_mock.assert_called_once_with(os.getpid(), signal_module.SIGTERM)


def test_pipeline_stop_404_when_no_active(client, tmp_db):
    resp = client.post("/pipeline/stop", auth=AUTH)
    assert resp.status_code == 404


def test_pipeline_stop_swallows_process_lookup_error(client, tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="dashboard")
    db.update_run(run_id, pid=999999)

    with patch("src.main.os.kill", side_effect=ProcessLookupError):
        resp = client.post("/pipeline/stop", auth=AUTH)
    assert resp.status_code == 200


def test_pipeline_status_renders_current_card(client, tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    db.update_run_step(run_id, "scrape", status="ok",
                       started_at=db.now_iso(), finished_at=db.now_iso(),
                       progress="12 articles")

    resp = client.get("/pipeline/status", auth=AUTH)
    assert resp.status_code == 200
    assert "scrape" in resp.text
    assert "12 articles" in resp.text
    assert "Run #" in resp.text


def test_pipeline_status_no_progress_bar_when_text_lacks_n_over_m(client, tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    db.update_run_step(run_id, "scrape", status="running",
                       progress="working on it")

    resp = client.get("/pipeline/status", auth=AUTH)
    assert "pipeline-step-bar" not in resp.text


def test_pipeline_status_renders_progress_bar_when_n_over_m_present(client, tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    db.update_run_step(run_id, "review", status="running",
                       progress="Reviewing 3/8 posts")

    resp = client.get("/pipeline/status", auth=AUTH)
    assert "pipeline-step-bar" in resp.text


def test_pipeline_status_terminal_subtitle_cancelled(client, tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    db.finish_pipeline_run(run_id, status="cancelled")

    resp = client.get("/pipeline/status", auth=AUTH)
    assert "Cancelled by user" in resp.text


def test_pipeline_status_terminal_subtitle_stopped(client, tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    db.finish_pipeline_run(run_id, status="stopped", stop_reason="no new articles")

    resp = client.get("/pipeline/status", auth=AUTH)
    assert "no new articles" in resp.text
    assert "Stopped" in resp.text


def test_pipeline_run_detail_returns_200(client, tmp_db):
    import src.db as db

    run_id = db.create_pipeline_run(trigger="cli")
    resp = client.get(f"/pipeline/runs/{run_id}", auth=AUTH)
    assert resp.status_code == 200
    assert f"Pipeline Run #{run_id}" in resp.text


def test_pipeline_run_detail_404_for_unknown(client, tmp_db):
    resp = client.get("/pipeline/runs/99999", auth=AUTH)
    assert resp.status_code == 404


def test_dashboard_includes_pipeline_card(client, tmp_db):
    resp = client.get("/", auth=AUTH)
    assert resp.status_code == 200
    assert "pipeline-card" in resp.text
    assert "▶ Start" in resp.text


# ── progress_percent filter ─────────────────────────────────────────────────

def test_progress_percent_extracts_ratio():
    from src.main import _progress_percent
    assert _progress_percent("Reviewing 3/8 posts") == 37
    assert _progress_percent("Rendering post 1/4 (id=42)") == 25
    assert _progress_percent("4/4 done") == 100


def test_progress_percent_handles_missing_or_invalid():
    from src.main import _progress_percent
    assert _progress_percent(None) is None
    assert _progress_percent("") is None
    assert _progress_percent("just text") is None
    assert _progress_percent("3/0 bad") is None
