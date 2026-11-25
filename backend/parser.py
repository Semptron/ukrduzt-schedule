from dataclasses import asdict, dataclass
from datetime import datetime
import re
import pdfplumber

GROUP_RE = re.compile(
    r"(?<![A-Za-zА-Яа-яІіЇїЄєҐґ0-9_])"
    r"(?:\d{2,4}[-/][A-Za-zА-Яа-яІіЇїЄєҐґ]{1,10}(?:[-/][A-Za-zА-Яа-яІіЇїЄєҐґ0-9]{1,6})?|"
    r"[A-Za-zА-Яа-яІіЇїЄєҐґ]{2,8}-\d{2,4}(?:-[A-Za-zА-Яа-яІіЇїЄєҐґ0-9]{1,6})?)"
    r"(?![A-Za-zА-Яа-яІіЇїЄєҐґ0-9_])"
)
DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{2})\.(\d{4})\b")
SHORT_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{2})(?!\.\d|\d)")
LESSON_NUM_RE = re.compile(r"^[1-8]$")
LESSON_TYPE_RE = re.compile(
    r"\b(лк|пр|лаб|лік|інд\.?\s*конс\.?|конс\.?|лекц\w*|практ\w*|лаб\w*)\b\.?",
    re.IGNORECASE,
)
ROOM_RE = re.compile(r"\b\d+\.[А-ЯІЇЄҐA-Z0-9]+\b")
URL_RE = re.compile(r"https?://[^\s,;]+", re.IGNORECASE)
ZOOM_RE = re.compile(r"https?://(?:[a-z0-9-]+\.)*zoom\.us/[^\s,;]+", re.IGNORECASE)
PAIR_RE = re.compile(
    r"(?:\b([1-8])\s*(?:та|і|,|-|–)?\s*([1-8])?\s*пар\w*|\b([1-8])\s*пара\b)",
    re.IGNORECASE,
)
ZOOM_ID_RE = re.compile(
    r"(?:ідентифікатор|id|код)\s*конференції[^:\d]*:\s*([\d\s]{5,20})",
    re.IGNORECASE,
)
ZOOM_PASS_RE = re.compile(
    r"(?:код\s*доступу|пароль|passcode)[^:\w]*:\s*([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
SUBJECT_QUOTES_RE = re.compile(r"[«\"]([^»\"]+)[»\"]")

WEEKDAY_RE = re.compile(
    r"^(?:понеділок|вівторок|середа|четвер|п['’]?ятниця|субота|неділя)$",
    re.IGNORECASE,
)
HEADER_NOISE_RE = re.compile(
    r"^(?:затверджую|ректор\s+укрдузт|с\.\s*в\.\s*панченко|розклад\s+занять|"
    r"навчальний\s+рік|механіко-енергетичний|заступник\s+декана|рукавішников)",
    re.IGNORECASE,
)

TEACHER_NOISE_TAIL_RE = re.compile(
    r"(?:,?\s*\b(?:інд\.?\s*конс\w*|індив\w*\s*конс\w*|конс\w*|екз\.?|залік|м|лекц\w*|практ\w*|лаб\w*)\b\.?)+$",
    re.IGNORECASE,
)
TEACHER_NOISE_HEAD_RE = re.compile(
    r"^(?:\b(?:інд\.?\s*конс\w*|індив\w*\s*конс\w*|конс\w*|екз\.?|залік|лекц\w*|практ\w*|лаб\w*)\b\.?[,:\s-]+)+",
    re.IGNORECASE,
)
TEACHER_EXACT_NOISE = {
    "4",
    "і",
    "м",
    "екз.",
    "екз",
    "інд. конс.",
    "інд. конс",
    "інд конс",
    "інд. консульт.",
    "індив. консульт.",
    "індивід. консультація",
}
SURNAME_INITIALS_RE = re.compile(
    r"^[А-ЯІЇЄҐA-Z][а-яіїєґa-z'’`-]+\s+[А-ЯІЇЄҐA-Z]\.\s*[А-ЯІЇЄҐA-Z]\.?$"
)

MONTHS_UA = {
    "січня": "01",
    "лютого": "02",
    "березня": "03",
    "квітня": "04",
    "травня": "05",
    "червня": "06",
    "липня": "07",
    "серпня": "08",
    "вересня": "09",
    "жовтня": "10",
    "листопада": "11",
    "грудня": "12",
}
TEXT_DATE_RE = re.compile(
    r"\b(\d{1,2})\s+(січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)\s+(\d{4})\b",
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


def clean_cell(text: str | None) -> str:
    if not text:
        return ""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line and line != "|"]
    return " ".join(lines).strip()


def normalize_teacher_name(raw_teacher: str | None) -> str | None:
    if not raw_teacher:
        return None

    cleaned = re.sub(r"\s+", " ", raw_teacher).strip(" ,;|")
    if not cleaned:
        return None

    cleaned = TEACHER_NOISE_HEAD_RE.sub("", cleaned).strip(" ,;|")
    cleaned = TEACHER_NOISE_TAIL_RE.sub("", cleaned).strip(" ,;|")
    if not cleaned:
        return None

    cleaned = re.sub(
        r"\b([А-ЯІЇЄҐA-Z])\.([А-ЯІЇЄҐA-Z])(?=\.|\s|$)",
        r"\1. \2.",
        cleaned,
    )
    cleaned = re.sub(r"\.\.+", ".", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;|")

    if cleaned.lower() in TEACHER_EXACT_NOISE:
        return None

    if len(cleaned) < 5 or cleaned.isdigit():
        return None

    if SURNAME_INITIALS_RE.match(cleaned):
        return cleaned

    words = cleaned.split()
    if len(words) >= 2 and all(re.match(r"^[А-ЯІЇЄҐA-Z]", w) for w in words[:2]):
        return cleaned

    return None


def detect_lesson_number(row: list[str | None], fallback_counter: int) -> int:
    for cell in row:
        txt = clean_cell(cell)
        if LESSON_NUM_RE.match(txt):
            return int(txt)
        m = PAIR_RE.search(txt)
        if m:
            num_str = m.group(1) or m.group(3)
            if num_str:
                return int(num_str)
    return min(max(fallback_counter, 1), 8)


def _normalize_date_parts(day: str, month: str, year: str) -> str:
    return f"{int(day):02d}.{month}.{year}"


def _infer_year_for_month(month_num: int, fallback_year: str | None = None) -> str:
    if fallback_year and fallback_year.isdigit() and len(fallback_year) == 4:
        return fallback_year
    now = datetime.now()
    if month_num >= 8 and now.month <= 3:
        return str(now.year - 1)
    if month_num <= 3 and now.month >= 8:
        return str(now.year + 1)
    return str(now.year)


def _extract_dates_from_text(
    line: str, fallback_year: str | None = None
) -> list[str]:
    dates: list[str] = []
    for m in TEXT_DATE_RE.finditer(line):
        d, m_name, y = m.groups()
        dates.append(_normalize_date_parts(d, MONTHS_UA[m_name.lower()], y))
    for m in NUMERIC_DATE_RE.finditer(line):
        d, m_num, y = m.groups()
        dates.append(_normalize_date_parts(d, m_num, y))
    if not dates:
        for m in SHORT_DATE_RE.finditer(line):
            d_str, m_str = m.groups()
            day_val = int(d_str)
            month_val = int(m_str)
            if 1 <= day_val <= 31 and 1 <= month_val <= 12:
                year_str = _infer_year_for_month(month_val, fallback_year)
                dates.append(f"{day_val:02d}.{month_val:02d}.{year_str}")
    return dates


def _extract_groups_from_text(text: str) -> list[str]:
    found: list[str] = []
    for m in GROUP_RE.finditer(text):
        g = m.group(0).strip()
        if ROOM_RE.fullmatch(g):
            continue
        if g and g not in found:
            found.append(g)
    return found


def _looks_like_lesson_line(line: str) -> bool:
    if not line or len(line) < 4:
        return False
    if WEEKDAY_RE.match(line) or HEADER_NOISE_RE.match(line):
        return False
    if DATE_RE.fullmatch(line) or LESSON_NUM_RE.fullmatch(line):
        return False
    if GROUP_RE.fullmatch(line):
        return False
    return bool(
        LESSON_TYPE_RE.search(line)
        or ROOM_RE.search(line)
        or "," in line
    )


def _extract_teacher_from_parts(parts: list[str], cleaned: str) -> str | None:
    for candidate in reversed(parts[1:] if len(parts) > 1 else parts):
        norm = normalize_teacher_name(candidate)
        if norm:
            return norm
    if len(parts) >= 3:
        return normalize_teacher_name(parts[-1])
    return normalize_teacher_name(cleaned)


def parse_cell_to_lesson(
    date_str: str,
    group: str,
    lesson_number: int,
    raw_text: str,
    warnings: list[ParseWarning],
) -> LessonItem | None:
    cleaned = clean_cell(raw_text)
    if not cleaned:
        return None
    if DATE_RE.fullmatch(cleaned) or LESSON_NUM_RE.fullmatch(cleaned):
        return None
    if WEEKDAY_RE.match(cleaned) or HEADER_NOISE_RE.match(cleaned):
        return None

    type_match = LESSON_TYPE_RE.search(cleaned)
    room_match = ROOM_RE.search(cleaned)
    zoom_match = ZOOM_RE.search(cleaned)
    any_url_match = URL_RE.search(cleaned)

    lesson_type = re.sub(r"\s+", " ", type_match.group(1)).lower() if type_match else None
    room = room_match.group(0) if room_match else None
    meeting_url = (
        zoom_match.group(0)
        if zoom_match
        else (any_url_match.group(0) if any_url_match else None)
    )

    parts = [p.
