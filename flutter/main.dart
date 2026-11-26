import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, HTTPException, Query, Response, status
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

_sync_lock = threading.Lock()
_sync_in_progress = False
_initial_sync_done = False
_last_sync_stats: dict[str, Any] | None = None
_last_sync_error: str | None = None


def _normalize_query_text(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned or None


def _execute_sync_safe(force: bool = False) -> dict[str, Any] | None:
    global _sync_in_progress, _initial_sync_done, _last_sync_stats, _last_sync_error

    if not _sync_lock.acquire(blocking=False):
        return None

    _sync_in_progress = True
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
        _sync_in_progress = False
        _sync_lock.release()


async def background_sync_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(_execute_sync_safe, False)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Background sync loop error: %s", exc)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
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

# CORS: API is consumed by the Flutter app and any browser client.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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
        "sync_in_progress": _sync_in_progress,
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
    from quality import quality_report, is_valid_group, is_valid_teacher

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
    group: str | None = Query(None, description="Наприклад: 101-ЛЛГ-з26 або потік"),
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
        if _sync_in_progress and not _initial_sync_done:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Триває первинна синхронізація розкладу після запуску сервера",
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
        "sync_in_progress": _sync_in_progress,
        "initial_sync_done": _initial_sync_done,
    }


@app.post("/api/v1/sync")
def trigger_sync(force: bool = Query(False, description="Примусово перепарсити всі файли")):
    if _sync_in_progress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "already_running",
                "last_stats": _last_sync_stats,
                "last_error": _last_sync_error,
            },
        )

    thread = threading.Thread(
        target=_execute_sync_safe, args=(force,), daemon=True
    )
    thread.start()
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "started",
            "force": force,
            "last_stats": _last_sync_stats,
            "last_error": _last_sync_error,
        },
    )
