import hashlib
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import httpx
from database import get_file_hash, replace_source_lessons
from parser import parse_schedule_pdf_detailed
from quality import sanitize_lessons

logger = logging.getLogger("ukrduzt-scraper")
PORTAL_URL = "https://kart.edu.ua/osvita/portal-rz"
DOWNLOAD_DIR = Path("downloads")
FILE_EXT_RE = re.compile(r"\.(pdf|xlsx|xls)$", re.IGNORECASE)
# Educational-process charts, scans of orders, etc. — not lesson tables
SKIP_NAME_RE = re.compile(
    r"(grafik|графік|adobe-scan|scan-|порядок|наказ|положен)",
    re.IGNORECASE,
)
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; UkrDUZT-Schedule-Sync/1.0)"
}
MAX_DOWNLOAD_RETRIES = 3
# Download + parse is I/O-bound and CPU-light (pdfplumber releases the GIL
# during file reads). A small pool keeps the portal happy while cutting total
# sync time roughly by the pool size.
SYNC_WORKERS = 4


def _should_skip_url(url: str) -> bool:
    name = Path(url.split("?", 1)[0]).name
    return bool(SKIP_NAME_RE.search(name))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _download_with_retry(
    client: httpx.Client, url: str, *, as_text: bool = False
) -> bytes | str:
    last_exc: Exception | None = None
    for attempt in range(1, MAX_DOWNLOAD_RETRIES + 1):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text if as_text else resp.content
        except Exception as exc:
            last_exc = exc
            if attempt < MAX_DOWNLOAD_RETRIES:
                time.sleep(1.5 * attempt)
    assert last_exc is not None
    raise last_exc


def fetch_schedule_links(
    portal_url: str = PORTAL_URL, client: httpx.Client | None = None
) -> list[str]:
    own_client = client is None
    client = client or httpx.Client(
        timeout=35.0, follow_redirects=True, headers=DEFAULT_HEADERS
    )
    try:
        content = _download_with_retry(client, portal_url, as_text=True)
    finally:
        if own_client:
            client.close()

    soup = BeautifulSoup(content, "html.parser")
    links: set[str] = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if not href:
            continue
        full_url = urljoin(portal_url, href)
        clean_path = full_url.split("?", 1)[0]
        if not FILE_EXT_RE.search(clean_path):
            continue
        if _should_skip_url(clean_path):
            logger.info("Skip non-schedule file: %s", Path(clean_path).name)
            continue
        links.add(full_url)

    return sorted(links)


@dataclass
class _ParseResult:
    url: str
    file_hash: str | None = None
    lessons: list[dict] | None = None
    warnings_count: int = 0
    skipped: bool = False  # unchanged since last sync
    error: str | None = None


def _process_one_file(
    client: httpx.Client, url: str, force: bool
) -> _ParseResult:
    """Download + parse a single schedule file. Returns a result object so the
    caller can do the SQLite write serially (SQLite does not like contention)."""
    raw_name = Path(url.split("?", 1)[0]).name or "schedule.pdf"
    try:
        content = _download_with_retry(client, url)
        if not content:
            raise ValueError("Empty file downloaded")

        file_hash = sha256_bytes(content)
        if not force and get_file_hash(url) == file_hash:
            return _ParseResult(url=url, file_hash=file_hash, skipped=True)

        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", raw_name)
        local_path = DOWNLOAD_DIR / safe_name
        try:
            local_path.write_bytes(content)
            ext = local_path.suffix.lower()
            if ext != ".pdf":
                logger.info("Skipping unsupported file format %s", raw_name)
                return _ParseResult(
                    url=url, file_hash=file_hash, skipped=True
                )

            bundle = parse_schedule_pdf_detailed(str(local_path))
            lessons = sanitize_lessons(bundle.get("lessons") or [])
            warnings = bundle.get("warnings") or []
            if not lessons:
                logger.info("No lessons in %s — skip", raw_name)
                return _ParseResult(
                    url=url, file_hash=file_hash, lessons=[], warnings_count=0
                )
            return _ParseResult(
                url=url,
                file_hash=file_hash,
                lessons=lessons,
                warnings_count=len(warnings),
            )
        finally:
            if local_path.exists():
                try:
                    local_path.unlink()
                except OSError:
                    pass
    except Exception as exc:
        logger.warning("Failed syncing %s: %s", url, exc)
        return _ParseResult(url=url, error=str(exc))


def run_sync(force: bool = False) -> dict:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    updated_files = 0
    failed_files = 0
    total_lessons_saved = 0

    with httpx.Client(
        timeout=35.0, follow_redirects=True, headers=DEFAULT_HEADERS
    ) as client:
        urls = fetch_schedule_links(client=client)

        # Fan out download+parse across workers; collect results in completion
        # order. The DB write below is serial, which is what SQLite wants.
        results: list[_ParseResult] = []
        with ThreadPoolExecutor(max_workers=SYNC_WORKERS) as pool:
            futures = {
                pool.submit(_process_one_file, client, url, force): url
                for url in urls
            }
            for fut in as_completed(futures):
                results.append(fut.result())

    for res in results:
        if res.error is not None:
            failed_files += 1
            continue
        if res.skipped or res.lessons is None:
            continue
        replace_source_lessons(
            res.url, res.file_hash, res.lessons, res.warnings_count
        )
        updated_files += 1
        total_lessons_saved += len(res.lessons)

    return {
        "found_files": len(urls),
        "updated_files": updated_files,
        "failed_files": failed_files,
        "parsed_lessons": total_lessons_saved,
    }
