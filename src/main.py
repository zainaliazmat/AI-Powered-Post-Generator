import asyncio
import json
import logging
import os
import re
import secrets
import signal
import sqlite3 as _sqlite3
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Query, Request, Response

load_dotenv()
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db

BASE_DIR = Path(__file__).parent.parent

_pipeline_logger = logging.getLogger("pipeline")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await asyncio.to_thread(db.reconcile_stale_runs)
    stop_event = asyncio.Event()

    async def _reconciler():
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=60)
            except asyncio.TimeoutError:
                try:
                    await asyncio.to_thread(db.reconcile_stale_runs)
                except Exception:
                    _pipeline_logger.exception("reconcile_stale_runs failed")

    task = asyncio.create_task(_reconciler())
    try:
        yield
    finally:
        stop_event.set()
        await task


app = FastAPI(title="InstaBot Dashboard", lifespan=lifespan)
security = HTTPBasic()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


_PROGRESS_RE = re.compile(r"(\d+)\s*/\s*(\d+)")


def _progress_percent(text: str | None) -> int | None:
    """Extract N/M from progress text and return the percent (0-100)."""
    if not text:
        return None
    m = _PROGRESS_RE.search(text)
    if not m:
        return None
    n, total = int(m.group(1)), int(m.group(2))
    if total <= 0:
        return None
    return max(0, min(100, int(n * 100 / total)))


templates.env.filters["progress_percent"] = _progress_percent

# ── DB admin helpers ────────────────────────────────────────────────────────

_LOGS_DIR = BASE_DIR / "logs"
_LOGS_DIR.mkdir(exist_ok=True)

_audit_logger = logging.getLogger("db_audit")
_audit_logger.setLevel(logging.INFO)
_audit_logger.propagate = False
if not _audit_logger.handlers:
    _ah = logging.FileHandler(_LOGS_DIR / "db_audit.log")
    _ah.setFormatter(logging.Formatter("%(message)s"))
    _audit_logger.addHandler(_ah)


def _audit(action: str, table: str, row_id: int | None, data: dict) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _audit_logger.info(
        "%s | %s | %s | id=%s | %s",
        ts, action, table, row_id, json.dumps(data, default=str),
    )


def _get_tables(conn: _sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r[0] for r in rows]


def _get_columns(conn: _sqlite3.Connection, table: str) -> list[dict]:
    return [dict(r) for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]


def _row_count(conn: _sqlite3.Connection, table: str) -> int:
    return conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]


def _validate_table(conn: _sqlite3.Connection, table: str) -> None:
    if table not in _get_tables(conn):
        raise HTTPException(status_code=404, detail="Table not found")


def _validate_columns(known_names: list[str], payload_keys: list[str]) -> None:
    for key in payload_keys:
        if key not in known_names:
            raise HTTPException(status_code=422, detail=f"Unknown column: {key}")


_TABLE_DELETE_WARNINGS: dict[str, dict] = {
    "sources": {
        "level": "warning",
        "message": "Removing this source stops it from being fetched. Use --fix to restore a crashed source instead.",
    },
    "crashed_sources": {
        "level": "neutral",
        "message": "This will permanently delete the crash record.",
    },
    "generated_posts": {
        "level": "danger",
        "message": "This permanently deletes a generated post and its image paths. This cannot be undone.",
    },
}
_DEFAULT_DELETE_WARNING = {
    "level": "warning",
    "message": "This will permanently delete this row. This cannot be undone.",
}

# Static file mounts — images first (more specific path)
_images_dir = BASE_DIR / "data" / "images"
_images_dir.mkdir(parents=True, exist_ok=True)
app.mount("/static/images", StaticFiles(directory=str(_images_dir)), name="images")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    username = os.getenv("DASHBOARD_USERNAME", "admin")
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if not password:
        raise HTTPException(status_code=500, detail="DASHBOARD_PASSWORD not set in .env")
    valid = secrets.compare_digest(credentials.username, username) and \
            secrets.compare_digest(credentials.password, password)
    if not valid:
        raise HTTPException(
            status_code=401,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/database")
def db_index(request: Request, _: None = Depends(require_auth)):
    with db.get_conn() as conn:
        table_names = _get_tables(conn)
        tables = [
            {
                "name": t,
                "row_count": _row_count(conn, t),
                "col_count": len(_get_columns(conn, t)),
            }
            for t in table_names
        ]
    return templates.TemplateResponse(
        request, "database.html", {"tables": tables}
    )


_PAGE_SIZE = 50


@app.get("/database/{table}")
def db_table_view(
    request: Request,
    table: str,
    page: int = Query(default=1, ge=1),
    _: None = Depends(require_auth),
):
    with db.get_conn() as conn:
        _validate_table(conn, table)
        columns = _get_columns(conn, table)
        total = _row_count(conn, table)
        offset = (page - 1) * _PAGE_SIZE
        rows = [
            dict(r)
            for r in conn.execute(
                f'SELECT * FROM "{table}" ORDER BY rowid LIMIT ? OFFSET ?',
                (_PAGE_SIZE + 1, offset),
            ).fetchall()
        ]
    has_next = len(rows) > _PAGE_SIZE
    rows = rows[:_PAGE_SIZE]
    return templates.TemplateResponse(
        request,
        "database_table.html",
        {
            "table": table,
            "columns": columns,
            "rows": rows,
            "row_count": total,
            "page": page,
            "page_size": _PAGE_SIZE,
            "has_next": has_next,
        },
    )


@app.get("/database/{table}/create-panel")
def db_create_panel(
    request: Request,
    table: str,
    _: None = Depends(require_auth),
):
    with db.get_conn() as conn:
        _validate_table(conn, table)
        columns = _get_columns(conn, table)
    return templates.TemplateResponse(
        request,
        "_db_edit_panel.html",
        {"table": table, "columns": columns, "row": {}, "mode": "create", "error": None},
    )


@app.get("/database/{table}/{row_id}/panel")
def db_edit_panel(
    request: Request,
    table: str,
    row_id: int,
    _: None = Depends(require_auth),
):
    with db.get_conn() as conn:
        _validate_table(conn, table)
        columns = _get_columns(conn, table)
        row = conn.execute(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Row not found")
    return templates.TemplateResponse(
        request,
        "_db_edit_panel.html",
        {"table": table, "columns": columns, "row": dict(row), "mode": "edit", "error": None},
    )


@app.put("/database/{table}/{row_id}")
async def db_update_row(
    request: Request,
    table: str,
    row_id: int,
    _: None = Depends(require_auth),
):
    form_data = await request.form()
    payload = {k: v for k, v in form_data.items() if k != "id"}
    if not payload:
        raise HTTPException(status_code=422, detail="No fields to update")

    with db.get_conn() as conn:
        _validate_table(conn, table)
        columns = _get_columns(conn, table)
        col_names = [c["name"] for c in columns]
        _validate_columns(col_names, list(payload.keys()))

        row = conn.execute(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Row not found")

        before = dict(row)
        set_clause = ", ".join(f'"{k}" = ?' for k in payload)
        try:
            conn.execute(
                f'UPDATE "{table}" SET {set_clause} WHERE id = ?',
                (*payload.values(), row_id),
            )
        except (_sqlite3.IntegrityError, _sqlite3.OperationalError) as exc:
            return templates.TemplateResponse(
                request,
                "_db_edit_panel.html",
                {"table": table, "columns": columns, "row": before, "mode": "edit", "error": str(exc)},
                status_code=409,
            )

        refetched = conn.execute(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,)).fetchone()
        updated = dict(refetched) if refetched is not None else before
        _audit("UPDATE", table, row_id, {"before": before, "after": updated})

    resp = templates.TemplateResponse(
        request,
        "_db_row.html",
        {"table": table, "columns": columns, "row": updated},
    )
    resp.headers["HX-Retarget"] = f"#db-row-{row_id}"
    resp.headers["HX-Reswap"] = "outerHTML"
    return resp


@app.post("/database/{table}")
async def db_create_row(
    request: Request,
    table: str,
    _: None = Depends(require_auth),
):
    form_data = await request.form()
    payload = {k: v for k, v in form_data.items() if k != "id" and v != ""}

    if not payload:
        raise HTTPException(status_code=422, detail="No fields provided")

    with db.get_conn() as conn:
        _validate_table(conn, table)
        columns = _get_columns(conn, table)
        col_names = [c["name"] for c in columns]
        _validate_columns(col_names, list(payload.keys()))

        cols_sql = ", ".join(f'"{k}"' for k in payload)
        placeholders = ", ".join("?" * len(payload))
        try:
            cursor = conn.execute(
                f'INSERT INTO "{table}" ({cols_sql}) VALUES ({placeholders})',
                tuple(payload.values()),
            )
        except (_sqlite3.IntegrityError, _sqlite3.OperationalError) as exc:
            return templates.TemplateResponse(
                request,
                "_db_edit_panel.html",
                {"table": table, "columns": columns, "row": dict(payload), "mode": "create", "error": str(exc)},
                status_code=409,
            )

        new_id = cursor.lastrowid
        refetched = conn.execute(f'SELECT * FROM "{table}" WHERE rowid = ?', (new_id,)).fetchone()
        new_row = dict(refetched) if refetched is not None else dict(payload)
        _audit("INSERT", table, new_id, new_row)

    resp = templates.TemplateResponse(
        request,
        "_db_row.html",
        {"table": table, "columns": columns, "row": new_row},
    )
    resp.headers["HX-Retarget"] = "#db-tbody"
    resp.headers["HX-Reswap"] = "afterbegin"
    return resp


@app.get("/database/{table}/{row_id}/row")
def db_row_fragment(
    request: Request,
    table: str,
    row_id: int,
    _: None = Depends(require_auth),
):
    with db.get_conn() as conn:
        _validate_table(conn, table)
        columns = _get_columns(conn, table)
        row = conn.execute(f'SELECT * FROM "{table}" WHERE id = ?', (row_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Row not found")
    return templates.TemplateResponse(
        request,
        "_db_row.html",
        {"table": table, "columns": columns, "row": dict(row)},
    )


@app.get("/database/{table}/{row_id}/delete-confirm")
def db_delete_confirm(
    request: Request,
    table: str,
    row_id: int,
    _: None = Depends(require_auth),
):
    with db.get_conn() as conn:
        _validate_table(conn, table)
    warning = _TABLE_DELETE_WARNINGS.get(table, _DEFAULT_DELETE_WARNING)
    return templates.TemplateResponse(
        request,
        "_db_delete_confirm.html",
        {"table": table, "row_id": row_id, "warning": warning},
    )


@app.delete("/database/{table}/{row_id}")
def db_delete_row(
    table: str,
    row_id: int,
    _: None = Depends(require_auth),
):
    with db.get_conn() as conn:
        _validate_table(conn, table)
        deleted = conn.execute(
            f'DELETE FROM "{table}" WHERE id = ?', (row_id,)
        ).rowcount
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Row not found")
    _audit("DELETE", table, row_id, {})
    return Response(content="", status_code=200)


# ── pipeline observability ────────────────────────────────────────────────────


def _render_pipeline_card(request: Request, status_code: int = 200) -> Response:
    latest = db.get_latest_run()
    steps = db.get_run_steps(latest["id"]) if latest else []
    recent = db.get_recent_runs(limit=5)
    return templates.TemplateResponse(
        request,
        "_pipeline_card.html",
        {"run": latest, "steps": steps, "recent": recent},
        status_code=status_code,
    )


@app.get("/pipeline/status")
def pipeline_status(request: Request, _: None = Depends(require_auth)):
    return _render_pipeline_card(request)


@app.post("/pipeline/start")
def pipeline_start(request: Request, _: None = Depends(require_auth)):
    run_id = db.create_pipeline_run(trigger="dashboard")
    if run_id is None:
        return _render_pipeline_card(request, status_code=409)
    log_path = _LOGS_DIR / f"pipeline_run_{run_id}.log"
    try:
        subprocess.Popen(
            [sys.executable, "cli.py", "--run", "--run-id", str(run_id)],
            cwd=str(BASE_DIR),
            stdout=open(log_path, "ab"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        _pipeline_logger.exception("Failed to spawn pipeline subprocess")
        # Row stays as 'running' with pid=NULL; reconciler will mark it failed.
    return _render_pipeline_card(request)


@app.post("/pipeline/stop")
def pipeline_stop(request: Request, _: None = Depends(require_auth)):
    run = db.get_active_run()
    if run is None:
        return _render_pipeline_card(request, status_code=404)
    pid = run.get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return _render_pipeline_card(request)


@app.get("/pipeline/runs/{run_id}")
def pipeline_run_detail(
    request: Request,
    run_id: int,
    _: None = Depends(require_auth),
):
    run = db.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    steps = db.get_run_steps(run_id)
    return templates.TemplateResponse(
        request,
        "pipeline_run.html",
        {"run": run, "steps": steps},
    )


@app.get("/")
def dashboard(request: Request, _: None = Depends(require_auth)):
    counts = db.get_post_counts()

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(created_at), AVG(review_score) FROM generated_posts"
        ).fetchone()
    last_generated = row[0]
    avg_score = round(row[1], 1) if row[1] else None

    crashed_count = len(db.get_crashed_sources())

    latest_run = db.get_latest_run()
    steps = db.get_run_steps(latest_run["id"]) if latest_run else []
    recent_runs = db.get_recent_runs(limit=5)

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "counts": counts,
            "last_generated": last_generated,
            "avg_score": avg_score,
            "crashed_count": crashed_count,
            "run": latest_run,
            "steps": steps,
            "recent": recent_runs,
        }
    )


@app.get("/review")
def review_queue(
    request: Request,
    page: int = 1,
    per_page: int = 20,
    _: None = Depends(require_auth),
):
    offset = (page - 1) * per_page
    raw = db.get_review_queue(limit=per_page + 1, offset=offset)
    has_next = len(raw) > per_page
    posts_raw = raw[:per_page]

    posts = []
    for p in posts_raw:
        entry = dict(p)
        try:
            carousel = json.loads(p["carousel_json"]) if isinstance(p["carousel_json"], str) else p["carousel_json"]
            slides = carousel.get("slides", [])
            entry["caption"] = slides[0].get("caption", "") if slides else ""
            entry["slide_count"] = len(slides)
            entry["brand_domain"] = carousel.get("brand_domain", "")
        except (json.JSONDecodeError, KeyError, TypeError):
            entry["caption"] = ""
            entry["slide_count"] = 0
            entry["brand_domain"] = ""

        try:
            paths = json.loads(p["image_paths"]) if p.get("image_paths") else []
            entry["first_image"] = f"/static/images/{Path(paths[0]).name}" if paths else None
        except (json.JSONDecodeError, TypeError, IndexError):
            entry["first_image"] = None

        posts.append(entry)

    counts = db.get_post_counts()
    total = counts["pending_review"] + counts["image_ready"]

    return templates.TemplateResponse(
        request,
        "review.html",
        {
            "posts": posts,
            "page": page,
            "per_page": per_page,
            "has_next": has_next,
            "total": total,
        }
    )


@app.post("/review/{post_id}/approve")
def approve_post(post_id: int, _: None = Depends(require_auth)):
    if db.get_post(post_id) is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if not db.approve_post(post_id):
        raise HTTPException(status_code=409, detail="Post already reviewed")
    return Response(content="", status_code=200)


@app.get("/review/{post_id}/reject-panel")
def reject_panel(post_id: int, request: Request, _: None = Depends(require_auth)):
    if db.get_post(post_id) is None:
        raise HTTPException(status_code=404, detail="Post not found")
    return templates.TemplateResponse(
        request,
        "_reject_panel.html",
        {"post_id": post_id, "error": None},
    )


@app.get("/review/{post_id}/reject-panel-clear")
def reject_panel_clear(post_id: int, _: None = Depends(require_auth)):
    return Response(content="", status_code=200)


@app.post("/review/{post_id}/reject")
def reject_post_route(
    request: Request,
    post_id: int,
    reason: str = Form(...),
    _: None = Depends(require_auth),
):
    if db.get_post(post_id) is None:
        raise HTTPException(status_code=404, detail="Post not found")
    if len(reason.strip()) < 10:
        return templates.TemplateResponse(
            request,
            "_reject_panel.html",
            {"post_id": post_id,
             "error": "Reason must be at least 10 characters."},
            status_code=200,
        )
    if not db.reject_post(post_id, reason.strip()):
        raise HTTPException(status_code=409, detail="Post already reviewed")
    resp = Response(content="", status_code=200)
    resp.headers["HX-Retarget"] = f"#post-{post_id}"
    resp.headers["HX-Reswap"] = "outerHTML"
    return resp
