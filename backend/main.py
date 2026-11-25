import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query
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


async def background_sync_loop() -> None:
    while True:
        try:
            stats = await asyncio.to_thread(run_sync)
            logger.info("Schedule sync finished: %s", stats)
        except Exception as exc:
            logger.warning("Schedule sync error: %s", exc)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    task = asyncio.create_task(background_sync_loop())
    try:
        yield
    finally:
        task.cancel()


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
    if not any([group, teacher]):
        raise HTTPException(
            status_code=400, detail="Вкажіть хоча б один параметр: group або teacher"
        )
    items = query_lessons(group=group, teacher=teacher, date=date)
    if not items:
        raise HTTPException(status_code=404, detail="Розклад не знайдено")
    return {"items": items}


@app.post("/api/v1/sync")
def trigger_sync():
    try:
        return run_sync()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
