"""
UkrDUZT correspondence schedule PDF parser (v2).

Primary strategy: table geometry from pdfplumber.
  - Header row → group columns (with x-ranges)
  - Date cell (col 0) may come digit-reversed: 6202.11.42 → 24.11.2026
  - Pair number in col 1
  - Lesson cells: merged cells cover several groups via x-overlap

Public API stays compatible with scraper/database:
  parse_schedule_pdf_detailed(path) -> {lessons, warnings}
  parse_schedule_pdf(path) -> list[dict]
  BELLS, clean_cell, detect_lesson_number, parse_cell_to_lesson, ...
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import re

import pdfplumber

# ---------------------------------------------------------------------------
# Constants & regexes
# ---------------------------------------------------------------------------

GROUP_RE = re.compile(
    r"(?<![A-Za-zА-Яа-яІіЇїЄєҐґ0-9_])"
    r"(?:"
    r"\d{2,4}[-/][A-Za-zА-Яа-яІіЇїЄєҐґ]{1,12}"
    r"(?:[-/][A-Za-zА-Яа-яІіЇїЄєҐґ0-9]{1,8})?"
    r"|"
    r"[A-Za-zА-Яа-яІіЇїЄєҐґ]{2,10}-\d{2,4}"
    r"(?:-[A-Za-zА-Яа-яІіЇїЄєҐґ0-9]{1,8})?"
    r")"
    r"(?![A-Za-zА-Яа-яІіЇїЄєҐґ0-9_])"
)

DATE_RE = re.compile(r"\b\d{1,2}\.\d{2}\.\d{4}\b")
NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{2})\.(\d{4})\b")
SHORT_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{2})(?!\.\d|\d)")
# pdfplumber often extracts dates right-to-left: 6202.11.42 → 24.11.2026
REVERSED_DATE_RE = re.compile(r"\b(\d{4})\.(\d{2})\.(\d{2})\b")

LESSON_NUM_RE = re.compile(r"^[1-8]$")
PAIR_RE = re.compile(
    r"(?:\b([1-8])\s*(?:та|і|,|-|–)?\s*([1-8])?\s*пар\w*|\b([1-8])\s*пара\b)",
    re.IGNORECASE,
)

LESSON_TYPE_RE = re.compile(
    r"\b(лк|пр|лаб|лік|лек|інд\.?\s*конс\.?|конс\.?|лекц\w*|практ\w*|лаб\w*)\b\.?",
    re.IGNORECASE,
)
ROOM_RE = re.compile(r"\b\d+\.[А-ЯІЇЄҐA-Z0-9]+\b")
URL_RE = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)
ZOOM_RE = re.compile(r"https?://(?:[a-z0-9-]+\.)*zoom\.us/[^\s,;]+", re.IGNORECASE)

WEEKDAY_RE = re.compile(
    r"^(?:понеділок|вівторок|середа|четвер|п['’]?ятниця|субота|неділя|"
    r"коніделоп|коротвіВ|адереС|ревтеЧ|яцинтя.П|атобуС|ялідеН)$",
    re.IGNORECASE,
)

HEADER_NOISE_RE = re.compile(
    r"^(?:затверджую|ректор\s+укрдузт|с\.\s*в\.\s*панченко|розклад\s+занять|"
    r"навчальний\s+рік|механіко-енергетичний|економічний|заступник\s+декана|"
    r"[ІI1]\s+курс|[ІI1]{2}\s+курс|[ІI1]{3}\s+курс|[ІI1]{4}\s+курс|"
    r"\d\s+курс|семестр|факультет|інститут|інд|ирап)$",
    re.IGNORECASE,
)
FRAGMENT_NOISE_RE = re.compile(
    r"^(?:інд\.?\s*конс\w*|індив\w*\s*конс\w*|конс\w*|екз\.?|экз\.?|залік|м|лк|пр|лаб|лік|лек)$",
    re.IGNORECASE,
)
GARBAGE_SUBJECT_RE = re.compile(
    r"^(?:інд|ирап|пари|пар|інд\.?\s*конс\.?|конс\.?|екз\.?|экз\.?|залік)$",
    re.IGNORECASE,
)

TEACHER_NOISE_TAIL_RE = re.compile(
    r"(?:,?\s*\b(?:інд\.?\s*конс\w*|індив\w*\s*конс\w*|конс\w*|екз\.?|экз\.?|"
    r"залік|м|лекц\w*|практ\w*|лаб\w*|лек)\b\.?)+$",
    re.IGNORECASE,
)
TEACHER_NOISE_HEAD_RE = re.compile(
    r"^(?:\b(?:інд\.?\s*конс\w*|індив\w*\s*конс\w*|конс\w*|екз\.?|экз\.?|"
    r"залік|лекц\w*|практ\w*|лаб\w*|лек)\b\.?[,:\s-]+)+",
    re.IGNORECASE,
)
TEACHER_EXACT_NOISE = {
    "4", "і", "м", "екз.", "екз", "экз.", "экз",
    "інд. конс.", "інд. конс", "інд конс",
    "інд. консульт.", "індив. консульт.", "індивід. консультація",
}
SURNAME_WITH_INITIALS_RE = re.compile(
    r"^[А-ЯІЇЄҐ][а-яіїєґ'’`-]{1,25}\s+[А-ЯІЇЄҐ]\.?(?:\s*[А-ЯІЇЄҐ]\.?){0,2}$"
)
FULL_NAME_RE = re.compile(
    r"^[А-ЯІЇЄҐ][а-яіїєґ'’`-]{1,25}\s+[А-ЯІЇЄҐ][а-яіїєґ'’`-]{1,25}"
    r"(?:\s+[А-ЯІЇЄҐ][а-яіїєґ'’`-]{1,25})?$"
)
TEACHER_STOPWORDS = {
    "освіти", "кафедра", "факультет", "університет", "семестр", "курс",
    "група", "аудиторія", "розклад", "занять", "лекція", "практична",
    "лабораторна", "консультація", "екзамен", "залік", "м", "і", "та",
}

MONTHS_UA = {
    "січня": "01", "лютого": "02", "березня": "03", "квітня": "04",
    "травня": "05", "червня": "06", "липня": "07", "серпня": "08",
    "вересня": "09", "жовтня": "10", "листопада": "11", "грудня": "12",
}
TEXT_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(січня|лютого|березня|квітня|травня|червня|липня|"
    r"серпня|вересня|жовтня|листопада|грудня)\s+(\d{4})\b",
    re.IGNORECASE,
)

BELLS: dict[int, tuple[str, str]] = {
    1: ("09:00", "10:20"),
    2: ("10:30", "11:50"),
    3: ("12:00", "13:20"),
    4: ("13:40", "15:00"),
    5: ("15:10", "16:30"),
    6: ("16:40", "18:00"),
    7: ("18:10", "19:30"),
    8: ("19:40", "21:00"),
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class LessonItem:
    date: str
    group: str
    lesson_number: int
    time_start: str
    time_end: str
    subject: str
    room: str | None
    lesson_type: str | None
    teacher: str | None
    meeting_url: str | None
    meeting_code: str | None
    meeting_password: str | None
    raw: str


@dataclass
class ParseWarning:
    date: str | None
    lesson_number: int | None
    group: str | None
    reason: str
    raw: str


@dataclass
class _GroupCol:
    name: str
    x0: float
    x1: float


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def clean_cell(text: str | None) -> str:
    if not text:
        return ""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line and line != "|"]
    joined = " ".join(lines).strip()
    joined = re.sub(r"\s*\|\s*", " ", joined)
    return re.sub(r"\s+", " ", joined).strip()


def _normalize_date_parts(day: str, month: str, year: str) -> str | None:
    try:
        d_val, m_val, y_val = int(day), int(month), int(year)
    except ValueError:
        return None
    if not (1 <= d_val <= 31 and 1 <= m_val <= 12 and 2020 <= y_val <= 2035):
        return None
    return f"{d_val:02d}.{m_val:02d}.{y_val:04d}"


def _extract_year_from_filename(pdf_path: str) -> str | None:
    name = Path(pdf_path).name
    full_years = re.findall(r"(?<!\d)(202[4-9]|203\d)(?!\d)", name)
    if full_years:
        return full_years[-1]
    short_range = re.search(r"\b(2[4-9])-(2[5-9]|30)\b", name)
    if short_range:
        return f"20{short_range.group(2)}"
    return None


def _infer_year_for_month(month_num: int, fallback_year: str | None = None) -> str:
    if fallback_year and fallback_year.isdigit() and len(fallback_year) == 4:
        return fallback_year
    now = datetime.now()
    if month_num >= 8 and now.month <= 3:
        return str(now.year - 1)
    if month_num <= 3 and now.month >= 8:
        return str(now.year + 1)
    return str(now.year)


def _extract_full_dates_from_text(line: str) -> list[str]:
    tokens: list[tuple[int, str]] = []
    for m in TEXT_DATE_RE.finditer(line):
        d, m_name, y = m.groups()
        norm = _normalize_date_parts(d, MONTHS_UA[m_name.lower()], y)
        if norm:
            tokens.append((m.start(), norm))
    for m in NUMERIC_DATE_RE.finditer(line):
        d, m_num, y = m.groups()
        norm = _normalize_date_parts(d, m_num, y)
        if norm:
            tokens.append((m.start(), norm))
    for m in REVERSED_DATE_RE.finditer(line):
        y_rev, m_rev, d_rev = m.groups()
        # Skip already-normal YYYY.MM.DD (year starts with 20)
        if y_rev.startswith("20") and 2020 <= int(y_rev) <= 2035:
            continue
        y, mo, d = y_rev[::-1], m_rev[::-1], d_rev[::-1]
        norm = _normalize_date_parts(d, mo, y)
        if norm:
            tokens.append((m.start(), norm))
    tokens.sort(key=lambda t: t[0])
    dates: list[str] = []
    for _, norm in tokens:
        if norm not in dates:
            dates.append(norm)
    return dates


def _extract_dates_from_text(
    line: str,
    fallback_year: str | None = None,
    *,
    allow_short: bool = False,
) -> list[str]:
    dates = _extract_full_dates_from_text(line)
    if dates:
        return dates
    if not allow_short:
        return []
    for m in SHORT_DATE_RE.finditer(line):
        d_str, m_str = m.groups()
        day_val, month_val = int(d_str), int(m_str)
        if 1 <= day_val <= 31 and 1 <= month_val <= 12:
            year_str = _infer_year_for_month(month_val, fallback_year)
            formatted = f"{day_val:02d}.{month_val:02d}.{year_str}"
            if formatted not in dates:
                dates.append(formatted)
    return dates


def _format_default_date(fallback_year: str | None) -> str:
    y = fallback_year or str(datetime.now().year)
    month = 11 if int(y) >= datetime.now().year else 3
    return f"01.{month:02d}.{y}"


def _normalize_lesson_type(raw: str | None) -> str | None:
    if not raw:
        return None
    t = re.sub(r"\s+", " ", raw).strip().lower()
    if re.match(r"^інд\.?\s*конс", t) or re.match(r"^індив", t):
        return "інд. конс."
    mapping = {
        "лк": "лк", "лек": "лк", "лекц": "лк", "лекція": "лк",
        "пр": "пр", "практ": "пр", "практична": "пр",
        "лаб": "лаб", "лабор": "лаб", "лабораторна": "лаб",
        "лік": "лік", "конс": "конс.", "конс.": "конс.", "консультація": "конс.",
    }
    for key, val in mapping.items():
        if t == key or t.startswith(key):
            return val
    if t in ("конс", "екз", "экз") and not t.endswith("."):
        return t + "."
    return t


def normalize_teacher_name(raw_teacher: str | None) -> str | None:
    if not raw_teacher:
        return None
    cleaned = re.sub(r"\s+", " ", raw_teacher).strip(" ,;|")
    cleaned = cleaned.replace("Ο.", "О.")
    cleaned = re.sub(
        r"(\b[А-ЯІЇЄҐ][а-яіїєґ'\u2019`-]+\s+[А-ЯІЇЄҐ]\.\s*)[L1I]\b\.?",
        r"\1І.",
        cleaned,
    )
    cleaned = TEACHER_NOISE_HEAD_RE.sub("", cleaned).strip(" ,;|")
    cleaned = TEACHER_NOISE_TAIL_RE.sub("", cleaned).strip(" ,;|")
    if not cleaned:
        return None
    cleaned = re.sub(
        r"\b([А-ЯІЇЄҐA-Z])\.([А-ЯІЇЄҐA-Z])(?=\.|\s|$)",
        r"\1. \2.",
        cleaned,
    )
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;|")
    lower = cleaned.lower()
    if lower in TEACHER_EXACT_NOISE:
        return None
    if any(sw in lower for sw in TEACHER_STOPWORDS) and not SURNAME_WITH_INITIALS_RE.match(
        cleaned
    ):
        return None
    return cleaned


def detect_lesson_number(row: list, fallback_counter: int) -> int:
    for cell in row:
        if cell is None:
            continue
        txt = clean_cell(cell)
        if LESSON_NUM_RE.match(txt):
            return int(txt)
        m = PAIR_RE.search(txt)
        if m:
            num_str = m.group(1) or m.group(3)
            if num_str:
                return int(num_str)
    return min(max(fallback_counter, 1), 8)


def _extract_groups_from_text(text: str) -> list:
    found = []
    for m in GROUP_RE.finditer(text or ""):
        g = m.group(0).strip()
        if ROOM_RE.fullmatch(g):
            continue
        if g and g not in found:
            found.append(g)
    return found


def _split_header_groups(cell_text: str) -> list:
    """
    Header cells may pack several groups:
      '103-ЕП,113-МЕ,114-П з26' → ['103-ЕП-з26', '113-МЕ-з26', '114-П-з26']
    """
    text = clean_cell(cell_text)
    if not text:
        return []

    suffix_m = re.search(r"(\s*-?[зzЗZ]?\d{2})\s*$", text)
    shared_suffix = ""
    base = text
    if suffix_m and not GROUP_RE.fullmatch(text):
        shared_suffix = suffix_m.group(1).strip()
        if shared_suffix and not shared_suffix.startswith("-"):
            shared_suffix = "-" + shared_suffix.lstrip()
        base = text[: suffix_m.start()].strip(" ,;")

    groups = _extract_groups_from_text(base)
    if groups:
        if shared_suffix:
            out = []
            for g in groups:
                if re.search(r"-\d{2}$", g) or re.search(r"-з\d{2}$", g, re.I):
                    out.append(g)
                else:
                    out.append(g + shared_suffix)
            return out
        return groups

    if GROUP_RE.search(text):
        return _extract_groups_from_text(text)
    return []


def _extract_subject_and_teacher(parts, cleaned):
    teacher = None
    subject_parts = []
    for p in parts:
        if ROOM_RE.fullmatch(p) or LESSON_TYPE_RE.fullmatch(p) or FRAGMENT_NOISE_RE.match(p):
            continue
        if _extract_full_dates_from_text(p):
            continue
        cand = normalize_teacher_name(p)
        if cand and not teacher:
            teacher = cand
            continue
        if GARBAGE_SUBJECT_RE.match(p):
            continue
        subject_parts.append(p)

    subject = ", ".join(subject_parts).strip() if subject_parts else (parts[0] if parts else cleaned)
    subject = re.sub(r"\s+", " ", subject).strip(" ,;-")
    if not subject or GARBAGE_SUBJECT_RE.match(subject):
        return "", teacher
    return subject, teacher


def parse_cell_to_lesson(date_str, group, lesson_number, raw_text, warnings):
    cleaned = clean_cell(raw_text)
    cleaned = re.sub(r"^\s*\d{1,2}\.\d{2}\.\d{4}\s*[,.;:|()\-–]*\s*", "", cleaned)
    cleaned = re.sub(r"^\s*\d{4}\.\d{2}\.\d{2}\s*[,.;:|()\-–]*\s*", "", cleaned)
    cleaned = re.sub(
        r"^\s*(?:понеділок|вівторок|середа|четвер|п['’]?ятниця|субота|неділя|"
        r"коніделоп|коротвіВ|адереС|ревтеЧ|яцинтя.П|атобуС|ялідеН)"
        r"\s*[,.;:|()\-–]*\s*",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = cleaned.strip()
    if not cleaned:
        return None
    if DATE_RE.fullmatch(cleaned) or LESSON_NUM_RE.fullmatch(cleaned):
        return None
    if (
        WEEKDAY_RE.match(cleaned)
        or HEADER_NOISE_RE.match(cleaned)
        or FRAGMENT_NOISE_RE.match(cleaned)
        or GARBAGE_SUBJECT_RE.match(cleaned)
    ):
        return None
    if _extract_full_dates_from_text(cleaned) and len(cleaned) < 16:
        return None

    type_match = LESSON_TYPE_RE.search(cleaned)
    room_match = ROOM_RE.search(cleaned)
    zoom_match = ZOOM_RE.search(cleaned)
    any_url_match = URL_RE.search(cleaned)

    lesson_type = _normalize_lesson_type(type_match.group(1)) if type_match else None
    room = room_match.group(0) if room_match else None
    meeting_url = (
        zoom_match.group(0)
        if zoom_match
        else (any_url_match.group(0) if any_url_match else None)
    )

    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    subject, teacher = _extract_subject_and_teacher(parts, cleaned)
    if not subject:
        return None

    if not lesson_type and not room and not meeting_url:
        if not teacher:
            return None
        warnings.append(
            ParseWarning(
                date=date_str,
                lesson_number=lesson_number,
                group=group,
                reason="missing_type_or_location",
                raw=cleaned,
            )
        )
    elif not lesson_type or (not room and not meeting_url):
        warnings.append(
            ParseWarning(
                date=date_str,
                lesson_number=lesson_number,
                group=group,
                reason="missing_type_or_location",
                raw=cleaned,
            )
        )

    time_start, time_end = BELLS.get(lesson_number, ("00:00", "00:00"))
    return LessonItem(
        date=date_str,
        group=group,
        lesson_number=lesson_number,
        time_start=time_start,
        time_end=time_end,
        subject=subject,
        room=room,
        lesson_type=lesson_type,
        teacher=teacher,
        meeting_url=meeting_url,
        meeting_code=None,
        meeting_password=None,
        raw=cleaned,
    )


# ---------------------------------------------------------------------------
# Table-based engine
# ---------------------------------------------------------------------------

def _find_group_header(table_obj, extracted_rows):
    rows_geom = table_obj.rows
    best = None

    for ri, row_cells in enumerate(extracted_rows[:8]):
        if ri >= len(rows_geom):
            break
        geom_cells = rows_geom[ri].cells
        cols = []
        for ci, text in enumerate(row_cells):
            if not text or not str(text).strip():
                continue
            groups = _split_header_groups(str(text))
            if not groups:
                continue
            cell_box = geom_cells[ci] if ci < len(geom_cells) else None
            if cell_box is None:
                continue
            x0, _, x1, _ = cell_box
            width = max(x1 - x0, 1.0)
            slice_w = width / len(groups)
            for gi, gname in enumerate(groups):
                cols.append(
                    _GroupCol(
                        name=gname,
                        x0=x0 + gi * slice_w,
                        x1=x0 + (gi + 1) * slice_w,
                    )
                )
        if len(cols) >= 2:
            best = (ri, cols)
            break
        if len(cols) == 1 and best is None:
            best = (ri, cols)
    return best


def _groups_covered_by_cell(group_cols, cell_x0, cell_x1):
    covered = []
    for g in group_cols:
        mid = (g.x0 + g.x1) / 2.0
        if cell_x0 - 2 <= mid <= cell_x1 + 2:
            covered.append(g.name)
    if covered:
        return covered
    for g in group_cols:
        if g.x1 > cell_x0 + 5 and g.x0 < cell_x1 - 5:
            covered.append(g.name)
    return covered


def _parse_table(table_obj, fallback_year, warnings):
    extracted = table_obj.extract()
    if not extracted:
        return []

    header = _find_group_header(table_obj, extracted)
    if not header:
        return []
    header_idx, group_cols = header
    if not group_cols:
        return []

    items = []
    current_date = None
    rows_geom = table_obj.rows

    for ri, row in enumerate(extracted):
        if ri <= header_idx:
            continue
        if ri >= len(rows_geom):
            break

        geom_cells = rows_geom[ri].cells
        cleaned = [clean_cell(c) for c in row]

        for ci in range(min(2, len(cleaned))):
            txt = cleaned[ci]
            if not txt:
                continue
            dates = _extract_dates_from_text(
                txt, fallback_year=fallback_year, allow_short=True
            )
            if dates:
                current_date = dates[-1]
                break

        if not current_date:
            continue

        lesson_num = None
        for ci in range(min(3, len(cleaned))):
            txt = cleaned[ci]
            if LESSON_NUM_RE.match(txt):
                lesson_num = int(txt)
                break
            m = PAIR_RE.search(txt)
            if m:
                num_str = m.group(1) or m.group(3)
                if num_str:
                    lesson_num = int(num_str)
                    break
        if lesson_num is None:
            continue

        for ci, txt in enumerate(cleaned):
            if not txt:
                continue
            if LESSON_NUM_RE.match(txt):
                continue
            if WEEKDAY_RE.match(txt):
                continue
            if _extract_full_dates_from_text(txt) and len(txt) < 20:
                continue
            if HEADER_NOISE_RE.match(txt):
                continue

            cell_box = geom_cells[ci] if ci < len(geom_cells) else None
            if cell_box is None:
                continue
            cell_x0, _, cell_x1, _ = cell_box

            covered = _groups_covered_by_cell(group_cols, cell_x0, cell_x1)
            if not covered:
                continue

            for gname in covered:
                item = parse_cell_to_lesson(
                    current_date, gname, lesson_num, txt, warnings
                )
                if item:
                    items.append(item)

    return items


def _parse_text_fallback(pdf_path, fallback_year, warnings):
    items = []
    current_date = None
    lesson_counter = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_txt = page.extract_text() or ""
            lines = [clean_cell(ln) for ln in page_txt.splitlines() if clean_cell(ln)]
            page_groups = _extract_groups_from_text(page_txt)
            active_groups = page_groups or ["Загальний потік"]

            for line in lines:
                dates = _extract_dates_from_text(
                    line, fallback_year=fallback_year, allow_short=True
                )
                if dates and (
                    WEEKDAY_RE.search(line)
                    or len(line) < 30
                    or not LESSON_TYPE_RE.search(line)
                ):
                    current_date = dates[-1]
                    lesson_counter = 0
                    continue

                if not current_date:
                    continue

                if LESSON_NUM_RE.match(line):
                    lesson_counter = int(line)
                    continue

                line_groups = _extract_groups_from_text(line)
                targets = line_groups if line_groups else active_groups

                if LESSON_TYPE_RE.search(line) or ROOM_RE.search(line) or "," in line:
                    lesson_counter = min(max(lesson_counter, 1), 8)
                    for g in targets:
                        item = parse_cell_to_lesson(
                            current_date, g, lesson_counter, line, warnings
                        )
                        if item:
                            items.append(item)
                    lesson_counter = min(lesson_counter + 1, 8)

    return items


def parse_schedule_pdf_detailed(pdf_path: str) -> dict:
    results = []
    warnings = []
    fallback_year = _extract_year_from_filename(pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            if not fallback_year:
                page_txt = page.extract_text() or ""
                years = re.findall(r"\b(202[4-9]|203\d)\b", page_txt)
                if years:
                    fallback_year = years[-1]

            tables = page.find_tables() or []
            if not tables:
                tables = page.find_tables(
                    table_settings={
                        "vertical_strategy": "text",
                        "horizontal_strategy": "text",
                        "snap_tolerance": 4,
                        "join_tolerance": 4,
                        "edge_min_length": 20,
                    }
                ) or []

            for t_obj in tables:
                results.extend(_parse_table(t_obj, fallback_year, warnings))

    if not results:
        results = _parse_text_fallback(pdf_path, fallback_year, warnings)

    deduped = []
    seen = set()
    for item in results:
        key = (
            item.date,
            item.group,
            item.lesson_number,
            item.subject.lower(),
            (item.teacher or "").lower(),
        )
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    def _sort_key(it):
        parts = it.date.split(".")
        try:
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        except (ValueError, IndexError):
            d, m, y = 1, 1, 2099
        return (y, m, d, it.group, it.lesson_number)

    deduped.sort(key=_sort_key)

    return {
        "lessons": [asdict(r) for r in deduped],
        "warnings": [asdict(w) for w in warnings],
    }


def parse_schedule_pdf(pdf_path: str) -> list:
    return parse_schedule_pdf_detailed(pdf_path)["lessons"]
