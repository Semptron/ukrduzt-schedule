import asyncio
import logging
import threading
from contextlib import asynccontextmanager
from typing import Any
from fastapi import FastAPI, HTTPException, Query, status
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

SYNC_INTERVAL_SECONDS = 3600

_sync_lock = threading.Lock()
_sync_in_progress = False
_last_sync_stats: dict[str, Any] | None = None
_last_sync_error: str | None = None


def _execute_sync_safe() -> dict[str, Any] | None:
    global _sync_in_progress, _last_sync_stats, _last_sync_error

    if not _sync_lock.acquire(blocking=False):
        return None

    _sync_in_progress = True
    try:
        stats = run_sync()
        _last_sync_stats = stats
        _last_sync_error = None
        logger.info("Schedule sync finished: %s", stats)
        return stats
    except Exception as exc:
        _last_sync_error = str(exc)
        logger.warning("Schedule sync error: %s", exc)
        return None
    finally:
        _sync_in_progress = False
        _sync_lock.release()


async def background_sync_loop() -> None:
    while True:
        try:
            await asyncio.to_thread(_execute_sync_safe)
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


@app.get("/api/v1/bells")
def get_bells():
    return {
        "bells": [
            {"lesson": num, "start": start, "end": end}
            for num, (start, end) in BELLS.items()
        ]
    }


@app.get("/api/v1/groups")
def get_groups():
    return {"groups": list_groups()}


@app.get("/api/v1/teachers")
def get_teachers():
    return {"teachers": list_teachers()}


@app.get("/api/v1/changes")
def get_changes(limit: int = Query(20, ge=1, le=100)):
    return {"changes": get_recent_changes(limit=limit)}


@app.get("/api/v1/schedule")
def get_schedule(
    group: str | None = Query(None, description="Наприклад: 101-ЛЛГ-326 або потік"),
    teacher: str | None = Query(None, description="Частина ПІБ викладача"),
    date: str | None = Query(None, description="Формат ДД.ММ.РРРР"),
):
    clean_group = group.strip() if group else None
    clean_teacher = teacher.strip() if teacher else None
    clean_date = date.strip() if date else None

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
        raise HTTPException(status_code=404, detail="Розклад не знайдено")
    return {"items": items}


@app.post("/api/v1/sync")
def trigger_sync():
    if _sync_in_progress:
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "status": "already_running",
                "last_stats": _last_sync_stats,
                "last_error": _last_sync_error,
            },
        )

    thread = threading.Thread(target=_execute_sync_safe, daemon=True)
    thread.start()
    return JSONResponse(
        status_code=status.HTTP_202_ACCEPTED,
        content={
            "status": "started",
            "last_stats": _last_sync_stats,
            "last_error": _last_sync_error,
        },
    )
