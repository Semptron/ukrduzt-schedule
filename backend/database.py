import sqlite3
from pathlib import Path
from parser import normalize_teacher_name
from quality import is_valid_group, is_valid_teacher, sanitize_lesson

DB_PATH = Path("data/schedule.db")

_UA_ALPHABET = "абвгґдеєжзиіїйклмнопрстуфхцчшщьюя"
_UA_ORDER = {ch: idx for idx, ch in enumerate(_UA_ALPHABET)}


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
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
                raw TEXT NOT NULL
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
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_group_date ON lessons(group_name, date)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_lessons_teacher ON lessons(teacher)"
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
                meeting_url, meeting_code, meeting_password, raw
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                )
                for item in items
            ],
        )
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


def list_groups() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT TRIM(group_name) AS group_name
            FROM lessons
            WHERE TRIM(group_name) != ''
            """
        ).fetchall()
        groups = [r["group_name"] for r in rows if is_valid_group(r["group_name"])]
        return sorted(groups, key=_ua_sort_key)


def list_teachers() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT TRIM(teacher) AS teacher
            FROM lessons
            WHERE teacher IS NOT NULL AND TRIM(teacher) != ''
            """
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

    where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"""
        SELECT id, date, group_name, lesson_number, time_start,
               time_end, subject, room, lesson_type, teacher,
               meeting_url, meeting_code, meeting_password
        FROM lessons
        {where_sql}
        ORDER BY id ASC
    """

    teacher_key = _normalize_search_key(teacher)

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        matched_rows: list[sqlite3.Row] = []
        for r in rows:
            row_teacher = r["teacher"]
            if teacher_key:
                if not row_teacher:
                    continue
                if teacher_key not in _normalize_search_key(row_teacher):
                    continue
            matched_rows.append(r)

        matched_rows.sort(
            key=lambda r: (
                _date_sort_key(r["date"]),
                int(r["lesson_number"]),
                int(r["id"]),
            )
        )

        results = []
        for r in matched_rows:
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
                    "room": r["room"] if "room" in r.keys() else None,
                    "meeting_url": r["meeting_url"],
                    "meeting_code": r["meeting_code"],
                    "meeting_password": r["meeting_password"],
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
