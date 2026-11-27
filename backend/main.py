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
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse

from database import (
    get_recent_changes,
    init_db,
    list_groups,
    list_teachers,
    query_lessons,
)
from parser import BELLS
from scraper import run_sync

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
def get_groups(response: Response):
    # Groups change slowly — let caches hold them for a minute.
    response.headers["Cache-Control"] = "public, max-age=60"
    return {"groups": list_groups()}


@app.get("/api/v1/teachers")
def get_teachers(response: Response):
    response.headers["Cache-Control"] = "public, max-age=60"
    return {"teachers": list_teachers()}


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
