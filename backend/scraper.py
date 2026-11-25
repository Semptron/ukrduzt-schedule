import hashlib
import logging
import re
import time
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import httpx
from database import get_file_hash, replace_source_lessons
from parser import parse_schedule_pdf_detailed

logger = logging.getLogger("ukrduzt-scraper")
PORTAL_URL = "https://kart.edu.ua/osvita/portal-rz"
DOWNLOAD_DIR = Path("downloads")
FILE_EXT_RE = re.compile(r"\.(pdf|xlsx|xls)$", re.IGNORECASE)
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; UkrDUZT-Schedule-Sync/1.0)"
}
MAX_DOWNLOAD_RETRIES = 3


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download_with_retry(client: httpx.Client, url: str) -> bytes:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.content
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_DOWNLOAD_RETRIES:
                time.sleep(1.5 * attempt)
    assert last_exc is not None
    raise last_exc


def fetch_schedule_links(portal_url: str = PORTAL_URL) -> list[str]:
    content = _download_with_retry_text(portal_url)
    soup = BeautifulSoup(content, "html.parser")
    links: set[str] = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href:
            continue
        full_url = urljoin(portal_url, href)
        clean_path = full_url.split("?", 1)[0]
        if FILE_EXT_RE.search(clean_path):
            links.add(full_url)

    return sorted(links)


def _download_with_retry_text(url: str) -> str:
    last_exc: Exception | None = None
    with httpx.Client(
        timeout=25.0, follow_redirects=True, headers=DEFAULT_HEADERS
    ) as client:
        for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
            try:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.text
            except Exception as exc:
                last_exc = exc
                if attempt < MAX_DOWNLOAD_RETRIES:
                    time.sleep(1.5 * attempt)
    assert last_exc is not None
    raise last_exc


def run_sync() -> dict:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    urls = fetch_schedule_links()
    updated_files = 0
    failed_files = 0
    total_lessons_saved = 0

    with httpx.Client(
        timeout=35.0, follow_redirects=True, headers=DEFAULT_HEADERS
    ) as client:
        for url in urls:
            local_path: Path | None = None
            try:
                content = _download_with_retry(client, url)
                if not content:
                    raise ValueError("Empty file downloaded")

                file_hash = sha256_bytes(content)
                if get_file_hash(url) == file_hash:
                    continue

                raw_name = Path(url.split("?", 1)[0]).name or "schedule.pdf"
                safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)
                local_path = DOWNLOAD_DIR / safe_name
                local_path.write_bytes(content)

                ext = local_path.suffix.lower()
                if ext == ".pdf":
                    parsed_bundle = parse_schedule_pdf_detailed(str(local_path))
                    lessons = parsed_bundle.get("lessons") or []
                    warnings = parsed_bundle.get("warnings") or []

                    if not lessons:
                        raise ValueError(f"Zero lessons parsed from {raw_name}")

                    replace_source_lessons(url, file_hash, lessons, len(warnings))
                    updated_files += 1
                    total_lessons_saved += len(lessons)
                else:
                    logger.info("Skipping unsupported file format %s", raw_name)
            except Exception as exc:
                failed_files += 1
                logger.warning("Failed syncing %s: %s", url, exc)
            finally:
                if local_path and local_path.exists():
                    try:
                        local_path.unlink()
                    except OSError:
                        pass

    return {
        "found_files": len(urls),
        "updated_files": updated_files,
        "failed_files": failed_files,
        "parsed_lessons": total_lessons_saved,
    }
