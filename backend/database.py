import sqlite3
from pathlib import Path
from parser import normalize_teacher_name
from quality import is_valid_group, is_valid_teacher, sanitize_lesson

DB_PATH = Path("data/schedule.db")

# Source-type constants. A lesson row carries one of these so query_lessons
# can prefer a manually-uploaded curator's file over the portal for the same
# (date, group, lesson_number) — the use case is "the site has one schedule
# but the curator sent a different one".
SOURCE_PORTAL = "portal"
SOURCE_MANUAL = "manual"

_UA_ALPHABET = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
_UA_ORDER = {ch: idx for idx, ch in enumerate(_UA_ALPHABET)}


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA busy_timeout=30000;")
    return conn


def _normalize_search_key(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(value.lower().split()).replace(" ", "")


def _normalize_date_str(value: str | None) -> str | None:
    if not value:
        return None
    parts = value.strip().split(".")
    if len(parts) != 3:
        return value.strip()
    try:
        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
        return f"{day:02d}.{month:02d}.{year:04d}"
    except ValueError:
        return value.strip()


def _date_sort_key(date_str: str | None) -> tuple[int, int, int, str]:
    if not date_str:
        return (9999, 12, 31, "")
    parts = date_str.strip().split(".")
    if len(parts) == 3:
        try:
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
            return (year, month, day, date_str)
        except ValueError:
            pass
    return (9999, 12, 31, date_str)


def _ua_sort_key(text: str) -> tuple:
    return tuple((_UA_ORDER.get(ch, 100 + ord(ch)), ch) for ch in text.lower())


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS synced_files (
                url TEXT PRIMARY KEY,
                sha256 TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT NOT NULL,
                date TEXT NOT NULL,
                group_name TEXT NOT NULL,
                lesson_number INTEGER NOT NULL,
                time_start TEXT NOT NULL,
                time_end TEXT NOT NULL,
                subject TEXT NOT NULL,
                room TEXT,
                lesson_type TEXT,
                teacher TEXT,
                meeting_url TEXT,
                meeting_code TEXT,
                meeting_password TEXT,
                raw TEXT NOT NULL,
                -- 'portal' (scraped) or 'manual' (uploaded by a curator).
                -- Manual overrides portal for the same (date, group, pair).
                source_type TEXT NOT NULL DEFAULT 'portal',
                -- Human-readable name of the source file, shown in the UI.
                source_name TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS change_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_url TEXT NOT NULL,
                added_count INTEGER NOT NULL,
                removed_count INTEGER NOT NULL,
                warnings_count INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        # Registry of manually-uploaded files. Each upload gets a stable id;
        # lessons reference it via source_url = "manual://<id>".
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT NOT NULL,
                sha256 TEXT NOT NULL,
                lesson_count INTEGER NOT NULL DEFAULT 0,
                warnings_count INTEGER NOT NULL DEFAULT 0,
                uploaded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                -- Optional note set by the curator, e.g. "розклад від 24.11".
                note TEXT
            )
            """
        )

        # --- migration: add source_type / source_name to pre-existing DBs ---
        # SQLite doesn't support IF NOT EXISTS on ALTER TABLE ADD COLUMN, so
        # introspect the existing columns first and only add the new ones.
        existing_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(lessons)").fetchall()
        }
        if "source_type" not in existing_cols:
            conn.execute(
                "ALTER TABLE lessons ADD COLUMN source_type TEXT NOT NULL DEFAULT 'portal'"
            )
        if "source_name" not in existing_cols:
            conn.execute("ALTER TABLE lessons ADD COLUMN source_name TEXT")

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_group_date ON lessons(group_name, date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_teacher ON lessons(teacher)"
        )
        # Composite index helps the teacher + date query path used by the
        # /api/v1/schedule?teacher=...&date=... client requests.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_teacher_date ON lessons(teacher, date)"
        )
        # Speed up "manual overrides portal" dedup in query_lessons.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_source_type ON lessons(source_type)"
        )
        conn.commit()


def get_file_hash(url: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT sha256 FROM synced_files WHERE url = ?", (url,)
        ).fetchone()
        return row["sha256"] if row else None


def _lesson_signature(row: dict | sqlite3.Row) -> tuple:
    return (
        row["date"],
        row["group"] if isinstance(row, dict) else row["group_name"],
        row["lesson_number"],
        row["subject"],
        row["lesson_type"],
        row["teacher"],
    )


def replace_source_lessons(
    source_url: str,
    file_hash: str,
    items: list[dict],
    warnings_count: int = 0,
    *,
    source_type: str = SOURCE_PORTAL,
    source_name: str | None = None,
) -> dict:
    with get_connection() as conn:
        old_rows = conn.execute(
            """
            SELECT date, group_name, lesson_number, subject, lesson_type, teacher
            FROM lessons WHERE source_url = ?
            """,
            (source_url,),
        ).fetchall()

        old_set = {_lesson_signature(r) for r in old_rows}
        new_set = {_lesson_signature(i) for i in items}
        added_count = len(new_set - old_set)
        removed_count = len(old_set - new_set)

        conn.execute("DELETE FROM lessons WHERE source_url = ?", (source_url,))
        conn.executemany(
            """
            INSERT INTO lessons (
                source_url, date, group_name, lesson_number,
                time_start, time_end, subject, room, lesson_type, teacher,
                meeting_url, meeting_code, meeting_password, raw,
                source_type, source_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    source_url,
                    _normalize_date_str(item["date"]) or item["date"].strip(),
                    item["group"].strip(),
                    int(item["lesson_number"]),
                    item["time_start"],
                    item["time_end"],
                    item["subject"].strip(),
                    item.get("room"),
                    item.get("lesson_type"),
                    normalize_teacher_name(item.get("teacher")),
                    item.get("meeting_url"),
                    item.get("meeting_code"),
                    item.get("meeting_password"),
                    item["raw"],
                    source_type,
                    source_name,
                )
                for item in items
            ],
        )
        # Only portal files go into synced_files (for the change-by-hash
        # dedup the scraper relies on). Manual uploads have their own registry.
        if source_type == SOURCE_PORTAL:
            conn.execute(
                """
                INSERT INTO synced_files (url, sha256, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(url) DO UPDATE SET
                    sha256 = excluded.sha256,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (source_url, file_hash),
            )
        conn.execute(
            """
            INSERT INTO change_logs (source_url, added_count, removed_count, warnings_count)
            VALUES (?, ?, ?, ?)
            """,
            (source_url, added_count, removed_count, warnings_count),
        )
        conn.commit()

    return {"added": added_count, "removed": removed_count}


# ---------------------------------------------------------------------------
# Manual file registry
# ---------------------------------------------------------------------------

def add_manual_file(
    original_name: str,
    file_hash: str,
    items: list[dict],
    warnings_count: int = 0,
    note: str | None = None,
) -> dict:
    """Persist a curator-uploaded schedule.

    Returns the new manual file id and the resulting source_url. The lessons
    are written with source_type='manual' so query_lessons prefers them over
    any conflicting portal rows for the same (date, group, lesson_number).
    """
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO manual_files
                (original_name, sha256, lesson_count, warnings_count, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (original_name, file_hash, len(items), warnings_count, note),
        )
        manual_id = cur.lastrowid
        source_url = f"manual://{manual_id}"
        source_name = note or original_name

        # De-dup inside this manual file first (same logic as the portal path).
        seen: set[tuple] = set()
        deduped: list[dict] = []
        for item in items:
            key = _lesson_signature(item)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)

        conn.executemany(
            """
            INSERT INTO lessons (
                source_url, date, group_name, lesson_number,
                time_start, time_end, subject, room, lesson_type, teacher,
                meeting_url, meeting_code, meeting_password, raw,
                source_type, source_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    source_url,
                    _normalize_date_str(item["date"]) or item["date"].strip(),
                    item["group"].strip(),
                    int(item["lesson_number"]),
                    item["time_start"],
                    item["time_end"],
                    item["subject"].strip(),
                    item.get("room"),
                    item.get("lesson_type"),
                    normalize_teacher_name(item.get("teacher")),
                    item.get("meeting_url"),
                    item.get("meeting_code"),
                    item.get("meeting_password"),
                    item["raw"],
                    SOURCE_MANUAL,
                    source_name,
                )
                for item in deduped
            ],
        )
        conn.execute(
            """
            INSERT INTO change_logs (source_url, added_count, removed_count, warnings_count)
            VALUES (?, ?, ?, ?)
            """,
            (source_url, len(deduped), 0, warnings_count),
        )
        conn.commit()

    return {"manual_id": manual_id, "source_url": source_url, "lessons": len(deduped)}


def list_manual_files() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, original_name, sha256, lesson_count, warnings_count,
                   uploaded_at, note
            FROM manual_files
            ORDER BY id DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def delete_manual_file(manual_id: int) -> bool:
    """Remove a manual file and all its lessons. Returns True if a row was
    deleted, False if the id did not exist."""
    source_url = f"manual://{manual_id}"
    with get_connection() as conn:
        cur = conn.execute(
            "DELETE FROM manual_files WHERE id = ?", (manual_id,)
        )
        deleted = cur.rowcount > 0
        if deleted:
            conn.execute("DELETE FROM lessons WHERE source_url = ?", (source_url,))
            conn.execute(
                """
                INSERT INTO change_logs (source_url, added_count, removed_count, warnings_count)
                VALUES (?, 0, ?, 0)
                """,
                (source_url, conn.total_changes),  # rough, only used for audit
            )
        conn.commit()
    return deleted


def list_groups(source_type: str | None = None) -> list[str]:
    """Distinct group codes.

    ``source_type`` filters by source: ``'portal'`` (scraped site),
    ``'manual'`` (curator-uploaded file), or ``None``/``'all'`` (both). The
    manual-overrides-portal shadowing is a query-time rule (see query_lessons)
    and doesn't change which *groups* exist, so listing never drops a group.
    """
    where = ""
    params: list[object] = []
    if source_type and source_type != "all":
        where = "AND source_type = ?"
        params = [source_type]
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT TRIM(group_name) AS group_name
            FROM lessons
            WHERE TRIM(group_name) != ''
            {where}
            """,
            params,
        ).fetchall()
        groups = [r["group_name"] for r in rows if is_valid_group(r["group_name"])]
        return sorted(groups, key=_ua_sort_key)


def list_teachers(source_type: str | None = None) -> list[str]:
    """Distinct teacher names (normalised). Same source filter semantics as
    ``list_groups``."""
    where = ""
    params: list[object] = []
    if source_type and source_type != "all":
        where = "AND source_type = ?"
        params = [source_type]
    with get_connection() as conn:
        rows = conn.execute(
            f"""
            SELECT DISTINCT TRIM(teacher) AS teacher
            FROM lessons
            WHERE teacher IS NOT NULL AND TRIM(teacher) != ''
            {where}
            """,
            params,
        ).fetchall()
        cleaned = {
            norm
            for r in rows
            if (norm := normalize_teacher_name(r["teacher"])) is not None
            and is_valid_teacher(norm)
        }
        return sorted(cleaned, key=_ua_sort_key)


def query_lessons(
    group: str | None = None,
    teacher: str | None = None,
    date: str | None = None,
    source_type: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[object] = []

    if group:
        clauses.append("LOWER(TRIM(group_name)) = LOWER(TRIM(?))")
        params.append(group)

    norm_date = _normalize_date_str(date)
    if norm_date:
        clauses.append("TRIM(date) = ?")
        params.append(norm_date)

    teacher_key = _normalize_search_key(teacher)
    if teacher_key:
        # Pre-filter in SQL to keep rows that have a non-empty teacher. The
        # case-insensitive substring match itself still has to happen in Python
        # because SQLite's built-in LOWER() does not fold Cyrillic letters
        # (it would need the ICU extension, which is rarely available).
        clauses.append(
            "teacher IS NOT NULL AND TRIM(teacher) != ''"
        )

    # Source filter. 'all'/None keeps both sources and applies the
    # manual-overrides-portal dedup below; 'portal'/'manual' restrict to one
    # source and skip the dedup (there's nothing to shadow).
    restrict_source = source_type and source_type != "all"
    if restrict_source:
        clauses.append("source_type = ?")
        params.append(source_type)

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT id, date, group_name, lesson_number, time_start,
               time_end, subject, room, lesson_type, teacher,
               meeting_url, meeting_code, meeting_password,
               source_type, source_name
        FROM lessons
        {where_sql}
        ORDER BY id ASC
    """

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        matched_rows: list[sqlite3.Row] = []
        for r in rows:
            if teacher_key:
                row_teacher = r["teacher"]
                if not row_teacher:
                    continue
                if teacher_key not in _normalize_search_key(row_teacher):
                    continue
            matched_rows.append(r)

        # Manual overrides portal: if a (date, group, lesson_number) exists in
        # both source types, keep only the manual row. This implements the
        # "the curator's file is more authoritative than the site" rule. We do
        # this in Python (after the SQL filter) because the dedup key is a
        # composite that the sort below already groups together. When the
        # caller restricted to a single source there's nothing to shadow, so we
        # skip the dedup pass entirely.
        if restrict_source:
            deduped_rows = matched_rows
        else:
            manual_seen: set[tuple] = set()
            deduped_rows = []
            # First pass: collect manual keys so we can drop conflicting portal rows.
            for r in matched_rows:
                if r["source_type"] == SOURCE_MANUAL:
                    manual_seen.add(
                        (r["date"], r["group_name"], int(r["lesson_number"]))
                    )
            for r in matched_rows:
                if (
                    r["source_type"] == SOURCE_PORTAL
                    and (r["date"], r["group_name"], int(r["lesson_number"])) in manual_seen
                ):
                    continue  # portal row shadowed by a manual one
                deduped_rows.append(r)

        deduped_rows.sort(
            key=lambda r: (
                _date_sort_key(r["date"]),
                int(r["lesson_number"]),
                int(r["id"]),
            )
        )

        results = []
        for r in deduped_rows:
            item = sanitize_lesson(
                {
                    "date": r["date"],
                    "group": r["group_name"],
                    "lesson_number": r["lesson_number"],
                    "time_start": r["time_start"],
                    "time_end": r["time_end"],
                    "subject": r["subject"],
                    "lesson_type": r["lesson_type"],
                    "teacher": r["teacher"],
                    "room": r["room"],
                    "meeting_url": r["meeting_url"],
                    "meeting_code": r["meeting_code"],
                    "meeting_password": r["meeting_password"],
                    # Surface the source so the UI can badge "from curator".
                    "source_type": r["source_type"],
                    "source_name": r["source_name"],
                }
            )
            if item:
                results.append(item)
        return results


def get_recent_changes(limit: int = 20) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT source_url, added_count, removed_count, warnings_count, created_at
            FROM change_logs
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
