"""UkrDUZT correspondence schedule API.

A small FastAPI service that serves the parsed schedule out of a local
SQLite database. A background task periodically re-syncs the schedule from the
portal (``scraper.run_sync``). An operator can also trigger a sync via
``POST /api/v1/sync`` — that endpoint is guarded by a bearer token so a
random visitor cannot DoS the parser.

Concurrency model:
- ``_sync_running`` is a ``threading.Event`` (not a lock-as-flag). It is set
  *before* the worker thread starts, so /health and /version observe an
  accurate state the moment a sync is requested. The actual worker uses a
  ``threading.Lock`` to serialise the work itself.
- A schedule query returns 503 (Retry-After) whenever a sync is running AND
  no matching rows exist yet — both for the initial sync and for a forced
  re-sync. This stops the client from caching a "not found" for 5 minutes
  while the database is being rebuilt.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import (
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from database import (
    add_manual_file,
    delete_manual_file,
    get_recent_changes,
    init_db,
    list_groups,
    list_manual_files,
    list_teachers,
    query_lessons,
)
from parser import BELLS, parse_schedule_pdf_detailed
from quality import sanitize_lessons
from scraper import run_sync, sha256_bytes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ukrduzt-backend")

# Sync cadence is configurable so the same image can run with a shorter
# interval in staging and a longer one in production.
SYNC_INTERVAL_SECONDS = int(os.environ.get("SYNC_INTERVAL_SECONDS", "3600"))

# Optional bearer token that gates the force-sync endpoint. If unset, the
# endpoint is open (fine for local dev). Set it in production to stop random
# visitors from triggering a full re-parse on the free Render plan.
SYNC_TOKEN = os.environ.get("SYNC_TOKEN")

# A list of allowed CORS origins. If unset, fall back to permissive "*" (no
# credentials). When credentials are needed, configure explicit origins.
_cors_raw = os.environ.get("CORS_ALLOW_ORIGINS", "*")
CORS_ALLOW_ORIGINS: list[str] | None = (
    None
    if _cors_raw.strip() == "*"
    else [o.strip() for o in _cors_raw.split(",") if o.strip()]
)
CORS_ALLOW_CREDENTIALS = bool(CORS_ALLOW_ORIGINS)  # credentials require explicit origins

# --- concurrency state -----------------------------------------------------
# _sync_running is set the instant a sync is *requested* (before the worker
# thread starts), so observers see an accurate state without a race window.
# _sync_lock serialises the actual work: only one sync runs at a time.
_sync_lock = threading.Lock()
_sync_running = threading.Event()
_initial_sync_done = False
_last_sync_stats: dict[str, Any] | None = None
_last_sync_error: str | None = None


def _normalize_query_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _execute_sync_safe(force: bool = False) -> dict[str, Any] | None:
    """Run a sync under ``_sync_lock`` and update the shared stats.

    Returns the stats dict on success, or ``None`` on error. ``_sync_running``
    is *cleared* here (not by the caller) so that even the lifespan background
    loop and the manual trigger share one well-defined release point.
    """
    global _initial_sync_done, _last_sync_stats, _last_sync_error

    # Acquire the work lock. The Event has already been set by the caller, so
    # other observers know a sync is in flight; the lock just makes sure two
    # threads don't actually run together.
    if not _sync_lock.acquire(blocking=False):
        return None

    try:
        stats = run_sync(force=force)
        _last_sync_stats = stats
        _last_sync_error = None
        _initial_sync_done = True
        logger.info("Schedule sync finished: %s", stats)
        return stats
    except Exception as exc:
        _last_sync_error = str(exc)
        _initial_sync_done = True
        logger.warning("Schedule sync error: %s", exc)
        return None
    finally:
        _sync_running.clear()
        _sync_lock.release()


def _start_sync(force: bool) -> bool:
    """Request a sync. Returns True if started, False if one was already running.

    Sets ``_sync_running`` *synchronously* before spawning the worker thread
    so there is no race window where /health reports "not running" right
    after a request was accepted.
    """
    if _sync_running.is_set():
        return False
    _sync_running.set()
    thread = threading.Thread(
        target=_execute_sync_safe, args=(force,), daemon=True
    )
    thread.start()
    return True


async def background_sync_loop() -> None:
    """Run periodic syncs until the app shuts down."""
    while True:
        try:
            # The background loop is the only writer when no operator is
            # hitting /api/v1/sync, so we don't need the "already running"
            # shortcut — _start_sync handles it.
            _start_sync(force=False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Background sync loop error: %s", exc)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Kick off the first sync immediately (non-blocking) so the API is ready
    # to serve stale/empty data with a 503 hint while the initial parse runs.
    _start_sync(force=False)
    task = asyncio.create_task(background_sync_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="UkrDUZT Correspondence Schedule API", lifespan=lifespan)

# Compress JSON responses > ~1 KB — schedule payloads can be a few hundred KB.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# CORS: API is consumed by the Flutter app and any browser client. When
# ``CORS_ALLOW_ORIGINS`` is the wildcard (default) we disable credentials —
# the CORS spec forbids ``*`` together with ``allow_credentials=True``, and
# FastAPI/Starlette would silently drop credentialed requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS or ["*"],
    allow_credentials=CORS_ALLOW_CREDENTIALS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "service": "ukrduzt-schedule",
        "status": "ok",
        "docs": "/docs",
        "endpoints": [
            "/api/v1/groups",
            "/api/v1/teachers",
            "/api/v1/schedule",
            "/api/v1/stats",
            "/api/v1/changes",
            "/api/v1/sync",
            "/api/v1/bells",
            "/api/v1/version",
        ],
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "sync_in_progress": _sync_running.is_set(),
        "initial_sync_done": _initial_sync_done,
        "last_stats": _last_sync_stats,
        "last_error": _last_sync_error,
    }


@app.get("/api/v1/bells")
def get_bells():
    return {
        "bells": [
            {"lesson": num, "start": start, "end": end}
            for num, (start, end) in BELLS.items()
        ]
    }


@app.get("/api/v1/groups")
def get_groups(
    response: Response,
    source: str | None = Query(
        default=None,
        description="Фільтр джерела: 'portal' (сайт), 'manual' (завантажений "
        "куратором файл) або пропущено/'all' (обидва).",
        pattern="^(portal|manual|all)$",
    ),
):
    # Groups change slowly — let caches hold them for a minute. The source
    # filter is part of the response identity, so vary the cache by it.
    response.headers["Cache-Control"] = "public, max-age=60"
    response.headers["Vary"] = "source"
    return {"groups": list_groups(source_type=source)}


@app.get("/api/v1/teachers")
def get_teachers(
    response: Response,
    source: str | None = Query(
        default=None,
        description="Фільтр джерела, як у /api/v1/groups.",
        pattern="^(portal|manual|all)$",
    ),
):
    response.headers["Cache-Control"] = "public, max-age=60"
    response.headers["Vary"] = "source"
    return {"teachers": list_teachers(source_type=source)}


@app.get("/api/v1/stats")
def get_stats():
    """Aggregate quality snapshot of the current schedule DB."""
    from database import get_connection
    from quality import is_valid_group, is_valid_teacher, quality_report

    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT date, group_name, lesson_number, subject, room,
                   lesson_type, teacher, meeting_url
            FROM lessons
            """
        ).fetchall()
        items = [
            {
                "date": r["date"],
                "group": r["group_name"],
                "lesson_number": r["lesson_number"],
                "subject": r["subject"],
                "room": r["room"],
                "lesson_type": r["lesson_type"],
                "teacher": r["teacher"],
                "meeting_url": r["meeting_url"],
            }
            for r in rows
        ]
    report = quality_report(items)
    report["valid_groups"] = sum(1 for i in items if is_valid_group(i.get("group")))
    report["valid_teachers_rows"] = sum(
        1 for i in items if is_valid_teacher(i.get("teacher"))
    )
    return report


@app.get("/api/v1/changes")
def get_changes(limit: int = Query(20, ge=1, le=100)):
    return {"changes": get_recent_changes(limit=limit)}


@app.get("/api/v1/schedule")
def get_schedule(
    group: str | None = Query(None, description="Точна назва групи, напр. 101-ЛЛГ-з26"),
    teacher: str | None = Query(None, description="Частина ПІБ викладача"),
    date: str | None = Query(None, description="Формат ДД.ММ.РРРР"),
    source: str | None = Query(
        None,
        description="Фільтр джерела: 'portal' (сайт), 'manual' (завантажений "
        "куратором) або пропущено/'all' (обидва, manual переважає над portal "
        "для тих самих дат і груп).",
        pattern="^(portal|manual|all)$",
    ),
    response: Response = None,
):
    clean_group = _normalize_query_text(group)
    clean_teacher = _normalize_query_text(teacher)
    clean_date = _normalize_query_text(date)

    if not any([clean_group, clean_teacher]):
        raise HTTPException(
            status_code=400, detail="Вкажіть хоча б один параметр: group або teacher"
        )

    items = query_lessons(
        group=clean_group,
        teacher=clean_teacher,
        date=clean_date,
        source_type=source,
    )

    if not items:
        # 503 (Retry-After) — not 404 — whenever a sync is in flight and there
        # is nothing to show yet. This applies to BOTH the initial sync after
        # startup and a forced re-sync triggered by an operator. Returning
        # 404 here would let the client cache "not found" for the duration of
        # the response's Cache-Control (5 min) — the wrong signal during an
        # active rebuild.
        if _sync_running.is_set():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Триває синхронізація розкладу, спробуйте через кілька секунд",
                headers={"Retry-After": "10"},
            )
        raise HTTPException(status_code=404, detail="Розклад не знайдено")

    # A specific schedule is stable for the day — let CDN/proxies cache it.
    if response is not None:
        response.headers["Cache-Control"] = "public, max-age=300"
    return {"items": items}


@app.get("/api/v1/version")
def get_version():
    """Lightweight endpoint for clients to detect breaking API changes."""
    return {
        "api_version": "v1",
        "sync_interval_seconds": SYNC_INTERVAL_SECONDS,
        "sync_in_progress": _sync_running.is_set(),
        "initial_sync_done": _initial_sync_done,
    }


@app.post("/api/v1/sync")
def trigger_sync(
    force: bool = Query(False, description="Примусово перепарсити всі файли"),
    x_sync_token: str | None = Header(default=None, alias="X-Sync-Token"),
):
    """Trigger a sync. ``force=True`` re-parses even unchanged files.

    The endpoint is open by default for local development. In production,
    set the ``SYNC_TOKEN`` env var: when set, the request must carry
    ``X-Sync-Token: <value>`` or it gets 403. This stops random visitors from
    triggering a full re-parse and burning the free Render plan.
    """
    if force and SYNC_TOKEN:
        provided = (x_sync_token or "").strip()
        if not provided or provided != SYNC_TOKEN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Примусова синхронізація вимагає дійсного X-Sync-Token",
            )

    if _sync_running.is_set():
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "already_running",
                "last_stats": _last_sync_stats,
                "last_error": _last_sync_error,
            },
        )

    started = _start_sync(force=force)
    if not started:
        # _start_sync returned False — extremely tight race with another
        # request that set the Event a few microseconds earlier.
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "already_running",
                "last_stats": _last_sync_stats,
                "last_error": _last_sync_error,
            },
        )
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "started",
            "force": force,
            "last_stats": _last_sync_stats,
            "last_error": _last_sync_error,
        },
    )


# ---------------------------------------------------------------------------
# Manual schedule upload — "the curator sent a different schedule"
# ---------------------------------------------------------------------------
# The portal sometimes disagrees with what a group's curator actually sends
# out. This lets a trusted user upload that PDF; its rows are tagged
# source_type='manual' and override the portal rows for the same
# (date, group, lesson_number) in query_lessons. The endpoints reuse the
# SYNC_TOKEN gate so a random visitor can't poison the database.

# Hard cap on upload size — schedule PDFs are well under this, and the cap
# stops a malicious upload from OOMing the parser.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
ALLOWED_UPLOAD_EXTS = {".pdf"}


def _require_sync_token(token: str | None) -> None:
    """Reject the request if SYNC_TOKEN is set and the provided token is wrong.

    Upload mutates the DB, so we gate it the same way as a forced sync: if the
    operator set SYNC_TOKEN, the request must carry a matching X-Sync-Token.
    Without this, any visitor could push a bogus PDF and shadow the real
    schedule for every group it touches.
    """
    if not SYNC_TOKEN:
        return
    provided = (token or "").strip()
    if not provided or provided != SYNC_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Завантаження розкладу вимагає дійсного X-Sync-Token",
        )


@app.post("/api/v1/admin/upload")
async def upload_manual_schedule(
    file: UploadFile = File(..., description="PDF-файл розкладу від куратора"),
    note: str | None = Form(
        default=None,
        description="Необов'язкова помітка, напр. 'розклад від 24.11 від куратора'",
    ),
    x_sync_token: str | None = Header(default=None, alias="X-Sync-Token"),
):
    """Parse and persist a curator-uploaded schedule PDF.

    The uploaded file is parsed in-memory (no temp file on disk) and stored as
    source_type='manual'. Its rows override the portal's for the same
    (date, group, lesson_number). Re-uploading a file with the same content is
    safe — it creates a new manual entry (idempotent at the lesson level via
    the source_url key).

    Requires ``X-Sync-Token`` when ``SYNC_TOKEN`` is configured.
    """
    _require_sync_token(x_sync_token)

    # --- validate extension & size ----------------------------------------
    original_name = (file.filename or "manual.pdf").strip()
    lower = original_name.lower()
    if not any(lower.endswith(ext) for ext in ALLOWED_UPLOAD_EXTS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Підтримуються лише PDF-файли (.pdf)",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл порожній",
        )
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Файл занадто великий (макс. {MAX_UPLOAD_BYTES // (1024*1024)} МБ)",
        )
    # Reject anything that doesn't smell like a PDF, even with a .pdf name —
    # a renamed .exe or .png would crash pdfplumber with a confusing error.
    if not raw[:8].startswith(b"%PDF-"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл не є коректним PDF (відсутній заголовок %PDF-)",
        )

    # --- parse in memory --------------------------------------------------
    try:
        bundle = parse_schedule_pdf_detailed(io.BytesIO(raw), filename=original_name)
    except Exception as exc:
        logger.warning("Manual upload parse failed for %s: %s", original_name, exc)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Не вдалося розібрати PDF: {exc}",
        ) from exc

    lessons = sanitize_lessons(bundle.get("lessons") or [])
    warnings = bundle.get("warnings") or []
    if not lessons:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="У файлі не знайдено жодної пари. Перевірте, що це розклад, а не графік навчального процесу.",
        )

    file_hash = sha256_bytes(raw)
    try:
        result = add_manual_file(
            original_name=original_name,
            file_hash=file_hash,
            items=lessons,
            warnings_count=len(warnings),
            note=note,
        )
    except Exception as exc:
        logger.warning("Manual upload persist failed for %s: %s", original_name, exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Помилка збереження: {exc}",
        ) from exc

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "status": "ok",
            "manual_id": result["manual_id"],
            "source_url": result["source_url"],
            "lessons_added": result["lessons"],
            "warnings_count": len(warnings),
            "original_name": original_name,
            "note": note,
        },
    )


@app.get("/api/v1/admin/uploads")
def list_uploads(x_sync_token: str | None = Header(default=None, alias="X-Sync-Token")):
    """List all manually-uploaded schedule files (newest first).

    Requires ``X-Sync-Token`` when ``SYNC_TOKEN`` is configured.
    """
    _require_sync_token(x_sync_token)
    return {"uploads": list_manual_files()}


@app.delete("/api/v1/admin/uploads/{manual_id}")
def remove_upload(
    manual_id: int,
    x_sync_token: str | None = Header(default=None, alias="X-Sync-Token"),
):
    """Delete a manual upload and all its lessons.

    Removing a manual file un-shadows the portal rows for the same
    (date, group, lesson_number): the portal schedule becomes authoritative
    again for those pairs. Requires ``X-Sync-Token`` when ``SYNC_TOKEN`` is set.
    """
    _require_sync_token(x_sync_token)
    deleted = delete_manual_file(manual_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Файл із id={manual_id} не знайдено",
        )
    return {"status": "deleted", "manual_id": manual_id}
