import hashlib
import logging
import re
from pathlib import Path
from urllib.parse import urljoin
import httpx
from bs4 import BeautifulSoup
from database import get_file_hash, replace_source_lessons
from parser import parse_schedule_pdf_detailed

logger = logging.getLogger("ukrduzt-scraper")
PORTAL_URL = "https://kart.edu.ua/osvita/portal-rz"
DOWNLOAD_DIR = Path("downloads")
FILE_EXT_RE = re.compile(r"\.(pdf|xlsx|xls)$", re.IGNORECASE)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch_schedule_links(portal_url: str = PORTAL_URL) -> list[str]:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; UkrDUZT-Schedule-Sync/1.0)"}
    with httpx.Client(timeout=20.0, follow_redirects=True, headers=headers) as client:
        resp = client.get(portal_url)
        resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    links: set[str] = set()

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        full_url = urljoin(portal_url, href)
        clean_path = full_url.split("?", 1)[0]
        if FILE_EXT_RE.search(clean_path):
            links.add(full_url)

    return sorted(links)


def run_sync() -> dict:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    urls = fetch_schedule_links()
    updated_files = 0
    failed_files = 0
    total_lessons_saved = 0

    headers = {"User-Agent": "Mozilla/5.0 (compatible; UkrDUZT-Schedule-Sync/1.0)"}
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=headers) as client:
        for url in urls:
            try:
                resp = client.get(url)
                resp.raise_for_status()
                content = resp.content
                file_hash = sha256_bytes(content)

                if get_file_hash(url) == file_hash:
                    continue

                filename = Path(url.split("?", 1)[0]).name or "schedule.pdf"
                local_path = DOWNLOAD_DIR / filename
                local_path.write_bytes(content)

                if local_path.suffix.lower() == ".pdf":
                    parsed_bundle = parse_schedule_pdf_detailed(str(local_path))
                    lessons = parsed_bundle["lessons"]
                    warnings = parsed_bundle["warnings"]

                    if not lessons:
                        raise ValueError(f"Zero lessons parsed from {filename}")

                    replace_source_lessons(url, file_hash, lessons, len(warnings))
                    updated_files += 1
                    total_lessons_saved += len(lessons)
            except Exception as exc:
                failed_files += 1
                logger.warning("Failed syncing %s: %s", url, exc)

    return {
        "found_files": len(urls),
        "updated_files": updated_files,
        "failed_files": failed_files,
        "parsed_lessons": total_lessons_saved,
    }
