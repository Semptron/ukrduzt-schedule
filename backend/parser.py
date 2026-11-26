from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import re
import pdfplumber

GROUP_RE = re.compile(
    r"(?<![A-Za-zА-Яа-яІіЇїЄєҐґ0-9_])"
    r"(?:\d{2,4}[-/][A-Za-zА-Яа-яІіЇїЄєҐґ]{1,10}(?:[-/][A-Za-zА-Яа-яІіЇїЄєҐґ0-9]{1,6})?|"
    r"[A-Za-zА-Яа-яІіЇїЄєҐґ]{2,8}-\d{2,4}(?:-[A-Za-zА-Яа-яІіЇїЄєҐґ0-9]{1,6})?)"
    r"(?![A-Za-zА-Яа-яІіЇїЄєҐґ0-9_])"
)
DATE_RE = re.compile(r"\b\d{1,2}\.\d{2}\.\d{4}\b")
NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{2})\.(\d{4})\b")
SHORT_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{2})(?!\.\d|\d)")
# pdfplumber sometimes extracts dates right-to-left: 6202.11.42 → 24.11.2026
REVERSED_DATE_RE = re.compile(r"\b(\d{4})\.(\d{2})\.(\d{2})\b")
LESSON_NUM_RE = re.compile(r"^[1-8]$")
LESSON_TYPE_RE = re.compile(
    r"\b(лк|пр|лаб|лік|лек|інд\.?\s*конс\.?|конс\.?|лекц\w*|практ\w*|лаб\w*)\b\.?",
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
SUBJECT_QUOTES_RE = re.compile(r'[«"]([^»"]+)[»"]')

WEEKDAY_RE = re.compile(
    r"^(?:понеділок|вівторок|середа|четвер|п['’]?ятниця|субота|неділя|"
    # pdfplumber sometimes reverses weekday text character-by-character
    r"коніделоп|коротвіВ|адереС|ревтеЧ|яцинтя.П|атобуС|ялідеН)$",
    re.IGNORECASE,
)
HEADER_NOISE_RE = re.compile(
    r"^(?:затверджую|ректор\s+укрдузт|с\.\s*в\.\s*панченко|розклад\s+занять|"
    r"навчальний\s+рік|механіко-енергетичний|заступник\s+декана|рукавішников|"
    r"[ІI1]\s+курс|[ІI1]{2}\s+курс|[ІI1]{3}\s+курс|[ІI1]{4}\s+курс|"
    r"\d\s+курс|семестр|факультет|інститут)",
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
    r"(?:,?\s*\b(?:інд\.?\s*конс\w*|індив\w*\s*конс\w*|конс\w*|екз\.?|экз\.?|залік|м|лекц\w*|практ\w*|лаб\w*|лек)\b\.?)+$",
    re.IGNORECASE,
)
TEACHER_NOISE_HEAD_RE = re.compile(
    r"^(?:\b(?:інд\.?\s*конс\w*|індив\w*\s*конс\w*|конс\w*|екз\.?|экз\.?|залік|лекц\w*|практ\w*|лаб\w*|лек)\b\.?[,:\s-]+)+",
    re.IGNORECASE,
)
TEACHER_EXACT_NOISE = {
    "4",
    "і",
    "м",
    "екз.",
    "екз",
    "экз.",
    "экз",
    "інд. конс.",
    "інд. конс",
    "інд конс",
    "інд. консульт.",
    "індив. консульт.",
    "індивід. консультація",
}
MIXED_SCRIPT_WORD_RE = re.compile(
    r"^(?=.*[А-ЯІЇЄҐа-яіїєґ])(?=.*[A-Za-z])[А-ЯІЇЄҐа-яіїєґA-Za-z'’`-]+$"
)
WEIRD_CASE_WORD_RE = re.compile(
    r"^(?:[а-яіїєґa-z]+[А-ЯІЇЄҐA-Z]|[А-ЯІЇЄҐA-Z]{2,}[а-яіїєґa-z]+)[А-ЯІЇЄҐа-яіїєґA-Za-z'’`-]*$"
)
SURNAME_WITH_INITIALS_RE = re.compile(
    r"^[А-ЯІЇЄҐ][а-яіїєґ'’`-]{1,25}\s+[А-ЯІЇЄҐ]\.?(?:\s*[А-ЯІЇЄҐ]\.?){0,2}$"
)
FULL_NAME_RE = re.compile(
    r"^[А-ЯІЇЄҐ][а-яіїєґ'’`-]{1,25}\s+[А-ЯІЇЄҐ][а-яіїєґ'’`-]{1,25}(?:\s+[А-ЯІЇЄҐ][а-яіїєґ'’`-]{1,25})?$"
)
TEACHER_STOPWORDS = {
    "освіти",
    "кафедра",
    "факультет",
    "університет",
    "семестр",
    "курс",
    "група",
    "аудиторія",
    "розклад",
    "занять",
    "лекція",
    "практична",
    "лабораторна",
    "консультація",
    "екзамен",
    "залік",
    "м",
    "і",
    "та",
}

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
ANY_FULL_DATE_TOKEN_RE = re.compile(
    r"(?:\b\d{1,2}\.\d{2}\.\d{4}\b|\b\d{1,2}\s+(?:січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)\s+\d{4}\b)",
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


def _normalize_lesson_type(raw: str | None) -> str | None:
    """Canonicalize lesson type strings from OCR/regex captures."""
    if not raw:
        return None
    t = re.sub(r"\s+", " ", raw).strip().lower()
    # Individual consultation variants → "інд. конс."
    if re.match(r"^інд\.?\s*конс", t) or re.match(r"^індив", t):
        return "інд. конс."
    # Short forms
    mapping = {
        "лк": "лк",
        "лек": "лк",
        "лекц": "лк",
        "лекція": "лк",
        "пр": "пр",
        "практ": "пр",
        "практична": "пр",
        "лаб": "лаб",
        "лабор": "лаб",
        "лабораторна": "лаб",
        "лік": "лік",
        "конс": "конс.",
        "конс.": "конс.",
        "консультація": "конс.",
    }
    for key, val in mapping.items():
        if t == key or t.startswith(key):
            return val
    # Keep trailing period for short abbreviations that usually have it
    if t in ("конс", "екз", "экз") and not t.endswith("."):
        return t + "."
    return t


def clean_cell(text: str | None) -> str:
    if not text:
        return ""
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line and line != "|"]
    joined = " ".join(lines).strip()
    # Remove standalone pipe separators left from table OCR
    joined = re.sub(r"\s*\|\s*", " ", joined)
    return re.sub(r"\s+", " ", joined).strip()


def _split_cell_lines(text: str | None) -> list[str]:
    if not text:
        return []
    out: list[str] = []
    for line in text.splitlines():
        cleaned = re.sub(r"\s+", " ", line).strip()
        if cleaned and cleaned != "|":
            out.append(cleaned)
    return out


def _fix_ocr_initials(text: str) -> str:
    fixed = text.replace("Ο.", "О.")
    fixed = re.sub(
        r"(\b[А-ЯІЇЄҐ][а-яіїєґ'’`-]+\s+[А-ЯІЇЄҐ]\.\s*)[L1I]\b\.?",
        r"\1І.",
        fixed,
    )
    return fixed


def normalize_teacher_name(raw_teacher: str | None) -> str | None:
    if not raw_teacher:
        return None

    cleaned = _fix_ocr_initials(re.sub(r"\s+", " ", raw_teacher)).strip(" ,;|")
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
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ,;|")

    lower = cleaned.lower()
    if lower in TEACHER_EXACT_NOISE:
        return None
    if any(sw in lower for sw in TEACHER_STOPWORDS) and not SURNAME_WITH_INITIALS_RE.match(
        cleaned
    ):
        return None
    if MIXED_SCRIPT_WORD_RE.match(cleaned) or WEIRD_CASE_WORD_RE.match(cleaned):
        return None
    if not (
        SURNAME_WITH_INITIALS_RE.match(cleaned)
        or FULL_NAME_RE.match(cleaned)
        or re.match(r"^[А-ЯІЇЄҐ][а-яіїєґ'’`-]{1,25}\s+[А-ЯІЇЄҐ]\.", cleaned)
    ):
        if len(cleaned) < 4:
            return None
    return cleaned


def detect_lesson_number(row: list[str | None], fallback_counter: int) -> int:
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


def _normalize_date_parts(day: str, month: str, year: str) -> str | None:
    d_val = int(day)
    m_val = int(month)
    y_val = int(year)
    if not (1 <= d_val <= 31 and 1 <= m_val <= 12 and 2020 <= y_val <= 2035):
        return None
    return f"{d_val:02d}.{m_val:02d}.{y_val:04d}"


def _extract_year_from_filename(pdf_path: str) -> str | None:
    name = Path(pdf_path).name
    # Prefer explicit 4-digit years, including ranges like 24.11-02.12.2026
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
    # Reversed OCR/extraction order: YYYY.MM.DD written backwards as digits
    # e.g. "6202.11.42" is visual "24.11.2026"
    for m in REVERSED_DATE_RE.finditer(line):
        y_rev, m_rev, d_rev = m.groups()
        # Only treat as reversed when year part looks like a reversed 202x
        if y_rev.startswith("20") and 2020 <= int(y_rev) <= 2035:
            # Already a normal-looking YYYY.MM.DD — skip (handled above if needed)
            continue
        # Digit-reverse each component: 6202→2026, 11→11, 42→24
        y = y_rev[::-1]
        mo = m_rev[::-1]
        d = d_rev[::-1]
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
        day_val = int(d_str)
        month_val = int(m_str)
        if 1 <= day_val <= 31 and 1 <= month_val <= 12:
            year_str = _infer_year_for_month(month_val, fallback_year)
            formatted = f"{day_val:02d}.{month_val:02d}.{year_str}"
            if formatted not in dates:
                dates.append(formatted)
    return dates


def _is_date_header_line(line: str) -> bool:
    s = line.strip()
    if not s:
        return False
    if DATE_RE.fullmatch(s) or TEXT_DATE_RE.fullmatch(s) or REVERSED_DATE_RE.fullmatch(s):
        return True
    if _extract_full_dates_from_text(s) and WEEKDAY_RE.fullmatch(
        re.sub(r"[\d.\s,.;:|()-–]+", "", s)
    ):
        return True
    stripped = re.sub(r"\b\d{1,2}\.\d{2}\.\d{4}\b", "", s)
    stripped = re.sub(r"\b\d{4}\.\d{2}\.\d{2}\b", "", stripped)
    stripped = TEXT_DATE_RE.sub("", stripped)
    stripped = WEEKDAY_RE.sub("", stripped.strip())
    stripped = re.sub(r"[\s,.;:|()-–]+", "", stripped)
    return bool(_extract_full_dates_from_text(s) and len(stripped) == 0)


def _strip_leading_date_or_weekday(text: str) -> str:
    s = re.sub(r"^\s*\d{1,2}\.\d{2}\.\d{4}\s*[,.;:|()-–]*\s*", "", text)
    s = re.sub(r"^\s*\d{4}\.\d{2}\.\d{2}\s*[,.;:|()-–]*\s*", "", s)
    s = re.sub(
        r"^\s*(?:понеділок|вівторок|середа|четвер|п['’]?ятниця|субота|неділя|"
        r"коніделоп|коротвіВ|адереС|ревтеЧ|яцинтя.П|атобуС|ялідеН)"
        r"\s*[,.;:|()-–]*\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )
    return s.strip()


def _split_text_by_dates(text: str, active_date: str) -> list[tuple[str, str]]:
    """Splits text into (date_str, segment_text) chunks when dates appear inside a block/cell."""
    matches = list(ANY_FULL_DATE_TOKEN_RE.finditer(text))
    if not matches:
        return [(active_date, text)]

    segments: list[tuple[str, str]] = []
    if matches[0].start() > 0:
        prefix = text[: matches[0].start()].strip()
        if prefix:
            segments.append((active_date, prefix))

    for i, m in enumerate(matches):
        raw_tok = m.group(0)
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[m.end() : end].strip()
        parsed_dates = _extract_full_dates_from_text(raw_tok)
        seg_date = parsed_dates[0] if parsed_dates else active_date
        if body:
            segments.append((seg_date, body))
        elif not segments:
            segments.append((seg_date, ""))

    return segments if segments else [(active_date, text)]


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
    if _is_date_header_line(line):
        return False
    if WEEKDAY_RE.match(line) or HEADER_NOISE_RE.match(line) or FRAGMENT_NOISE_RE.match(line):
        return False
    if DATE_RE.fullmatch(line) or LESSON_NUM_RE.fullmatch(line):
        return False
    if GROUP_RE.fullmatch(line):
        return False
    return bool(
        LESSON_TYPE_RE.search(line)
        or ROOM_RE.search(line)
        or "," in line
        or SUBJECT_QUOTES_RE.search(line)
        or URL_RE.search(line)
    )


def _extract_subject_and_teacher(
    parts: list[str], cleaned: str
) -> tuple[str, str | None]:
    teacher: str | None = None
    subject_parts: list[str] = []

    for p in parts:
        if ROOM_RE.fullmatch(p) or LESSON_TYPE_RE.fullmatch(p) or FRAGMENT_NOISE_RE.match(p):
            continue
        if _is_date_header_line(p):
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


def parse_cell_to_lesson(
    date_str: str,
    group: str,
    lesson_number: int,
    raw_text: str,
    warnings: list[ParseWarning],
) -> LessonItem | None:
    cleaned = _strip_leading_date_or_weekday(clean_cell(raw_text))
    if not cleaned:
        return None
    if _is_date_header_line(cleaned):
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

    type_match = LESSON_TYPE_RE.search(cleaned)
    room_match = ROOM_RE.search(cleaned)
    zoom_match = ZOOM_RE.search(cleaned)
    any_url_match = URL_RE.search(cleaned)

    lesson_type = (
        _normalize_lesson_type(type_match.group(1)) if type_match else None
    )
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
        return None

    if not lesson_type or (not room and not meeting_url):
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


def _extract_tables_from_page(page) -> list:
    tables = page.find_tables()
    if tables:
        return tables
    return page.find_tables(
        table_settings={
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "snap_tolerance": 4,
            "join_tolerance": 4,
            "edge_min_length": 20,
        }
    )


def _collect_page_date_anchors(page, fallback_year: str | None) -> list[tuple[float, float, float, str]]:
    """Collects (top, x0, x1, date_str) anchors on a page using word/line coordinates."""
    anchors: list[tuple[float, float, float, str]] = []
    try:
        words = page.extract_words() or []
    except Exception:
        words = []
    for w in words:
        txt = (w.get("text") or "").strip()
        dates = _extract_dates_from_text(txt, fallback_year=fallback_year, allow_short=False)
        for d in dates:
            anchors.append(
                (
                    float(w.get("top", 0)),
                    float(w.get("x0", 0)),
                    float(w.get("x1", 0)),
                    d,
                )
            )
    try:
        lines = page.extract_text_lines() or []
    except Exception:
        lines = []
    for ln in lines:
        txt = (ln.get("text") or "").strip()
        dates = _extract_dates_from_text(txt, fallback_year=fallback_year, allow_short=True)
        for d in dates:
            anchors.append(
                (
                    float(ln.get("top", 0)),
                    float(ln.get("x0", 0)),
                    float(ln.get("x1", 0)),
                    d,
                )
            )
    anchors.sort(key=lambda a: (a[0], a[1]))
    return anchors


def _resolve_date_by_position(
    anchors: list[tuple[float, float, float, str]],
    item_top: float,
    item_x0: float,
    item_x1: float,
    fallback_date: str,
) -> str:
    """Finds the most recent date anchor above `item_top` (preferring same/left column)."""
    if not anchors:
        return fallback_date
    above = [a for a in anchors if a[0] <= item_top + 2]
    if not above:
        return fallback_date

    mid_x = (item_x0 + item_x1) / 2
    col_candidates = [
        a
        for a in above
        if a[1] - 30 <= mid_x <= a[2] + 30 or a[1] <= item_x0 <= a[2] or item_x0 <= a[1] <= item_x1
    ]
    pool = col_candidates if col_candidates else above
    # Closest above
    best = max(pool, key=lambda a: a[0])
    return best[3]


def _parse_text_fallback(pdf_path: str) -> list[LessonItem]:
    items: list[LessonItem] = []
    warnings: list[ParseWarning] = []
    fallback_year = _extract_year_from_filename(pdf_path)

    with pdfplumber.open(pdf_path) as pdf:
        full_text = "\n".join((p.extract_text() or "") for p in pdf.pages)
        if not fallback_year:
            years_in_text = re.findall(r"\b(202[4-9]|203\d)\b", full_text)
            fallback_year = years_in_text[-1] if years_in_text else str(datetime.now().year)

    current_date: str | None = None
    current_teacher: str | None = None
    meeting_code: str | None = None
    meeting_password: str | None = None
    lesson_counter = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_txt = page.extract_text() or ""
            lines = [clean_cell(ln) for ln in page_txt.splitlines()]
            lines = [ln for ln in lines if ln]
            page_groups = _extract_groups_from_text("\n".join(lines))
            active_page_groups = page_groups or ["Загальний потік"]
            full_lines = lines

            for i, line in enumerate(full_lines):
                if _is_date_header_line(line):
                    dates = _extract_dates_from_text(
                        line, fallback_year=fallback_year, allow_short=True
                    )
                    if dates:
                        current_date = dates[0]
                        lesson_counter = 0
                    continue

                id_m = ZOOM_ID_RE.search(line)
                if id_m:
                    meeting_code = re.sub(r"\s+", "", id_m.group(1))
                pass_m = ZOOM_PASS_RE.search(line)
                if pass_m:
                    meeting_password = pass_m.group(1).strip()

                line_groups = _extract_groups_from_text(line)
                target_groups = line_groups if line_groups else active_page_groups

                line_dates = _extract_dates_from_text(
                    line, fallback_year=fallback_year, allow_short=True
                )
                pair_match = PAIR_RE.search(line)
                if line_dates and pair_match:
                    current_date = line_dates[-1]
                    lesson_counter = 0

                if current_date and line_dates and not pair_match:
                    for d_str in line_dates:
                        if d_str != current_date:
                            current_date = d_str
                            lesson_counter = 0
                            break

                if current_date and LESSON_NUM_RE.match(line) and i + 1 < len(full_lines):
                    p_num = int(line)
                    next_line = full_lines[i + 1]
                    if not _is_date_header_line(next_line):
                        for g_name in target_groups:
                            parsed = parse_cell_to_lesson(
                                current_date, g_name, p_num, next_line, warnings
                            )
                            if parsed:
                                if not parsed.teacher and current_teacher:
                                    parsed.teacher = current_teacher
                                parsed.meeting_code = meeting_code
                                parsed.meeting_password = meeting_password
                                items.append(parsed)
                    continue

                elif current_date and _looks_like_lesson_line(line):
                    lesson_counter += 1
                    p_num = min(lesson_counter, 8)
                    for g_name in target_groups:
                        parsed = parse_cell_to_lesson(
                            current_date, g_name, p_num, line, warnings
                        )
                        if parsed:
                            if not parsed.teacher and current_teacher:
                                parsed.teacher = current_teacher
                            parsed.meeting_code = meeting_code
                            parsed.meeting_password = meeting_password
                            items.append(parsed)

    return items


def parse_schedule_pdf_detailed(pdf_path: str) -> dict:
    results: list[LessonItem] = []
    warnings: list[ParseWarning] = []
    current_date: str | None = None
    fallback_year: str | None = _extract_year_from_filename(pdf_path)
    lesson_counter = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_txt = page.extract_text() or ""
            if not fallback_year:
                years_in_page = re.findall(r"\b(202[4-9]|203\d)\b", page_txt)
                if years_in_page:
                    fallback_year = years_in_page[-1]

            date_anchors = _collect_page_date_anchors(page, fallback_year)
            page_groups = _extract_groups_from_text(page_txt)
            group_columns: dict[int, str] = {}

            found_tables = _extract_tables_from_page(page)
            for t_obj in found_tables:
                table = t_obj.extract()
                if not table:
                    continue

                t_bbox = getattr(t_obj, "bbox", None)
                t_top = float(t_bbox[1]) if t_bbox and len(t_bbox) >= 4 else 0.0
                t_bottom = (
                    float(t_bbox[3])
                    if t_bbox and len(t_bbox) >= 4
                    else float(getattr(page, "height", 1000.0))
                )
                t_x0 = float(t_bbox[0]) if t_bbox and len(t_bbox) >= 4 else 0.0
                t_x1 = (
                    float(t_bbox[2])
                    if t_bbox and len(t_bbox) >= 4
                    else float(getattr(page, "width", 1000.0))
                )
                num_rows = max(len(table), 1)
                row_height = (t_bottom - t_top) / num_rows

                for row_idx, row in enumerate(table):
                    if not row:
                        continue

                    row_top = t_top + row_idx * row_height
                    num_cols = max(len(row), 1)
                    col_width = (t_x1 - t_x0) / num_cols

                    cleaned_cells = [clean_cell(c) for c in row]
                    non_empty_cells = [
                        (idx, txt) for idx, txt in enumerate(cleaned_cells) if txt
                    ]
                    if not non_empty_cells:
                        continue

                    detected_groups: dict[int, str] = {}
                    for col_idx, txt in non_empty_cells:
                        groups_in_cell = _extract_groups_from_text(txt)
                        if groups_in_cell:
                            detected_groups[col_idx] = groups_in_cell[0]

                    if detected_groups:
                        group_columns.update(detected_groups)
                        continue

                    joined_row = " ".join(txt for _, txt in non_empty_cells)
                    row_dates: list[str] = []
                    for _, txt in non_empty_cells:
                        for d in _extract_full_dates_from_text(txt):
                            if d not in row_dates:
                                row_dates.append(d)

                    if not row_dates and _is_date_header_line(joined_row):
                        for d in _extract_dates_from_text(
                            joined_row, fallback_year=fallback_year, allow_short=True
                        ):
                            if d not in row_dates:
                                row_dates.append(d)

                    if row_dates:
                        current_date = row_dates[-1]
                        lesson_counter = 0
                        if _is_date_header_line(joined_row) or len(non_empty_cells) == 1:
                            continue

                    base_fallback_date = current_date or _format_default_date(fallback_year)
                    lesson_counter += 1
                    lesson_num = detect_lesson_number(row, lesson_counter)

                    if group_columns:
                        for col_idx, group_name in group_columns.items():
                            raw_cell = row[col_idx] if col_idx < len(row) else ""
                            cell_lines = _split_cell_lines(raw_cell)
                            if not cell_lines:
                                continue

                            cell_x0 = t_x0 + col_idx * col_width
                            cell_x1 = cell_x0 + col_width
                            line_step = row_height / max(len(cell_lines), 1)

                            for line_i, c_line in enumerate(cell_lines):
                                if not c_line or LESSON_NUM_RE.match(c_line):
                                    continue
                                if WEEKDAY_RE.match(c_line):
                                    continue
                                line_top = row_top + line_i * line_step
                                pos_date = _resolve_date_by_position(
                                    date_anchors,
                                    line_top,
                                    cell_x0,
                                    cell_x1,
                                    base_fallback_date,
                                )
                                for seg_date, seg_txt in _split_text_by_dates(
                                    c_line, pos_date
                                ):
                                    item = parse_cell_to_lesson(
                                        seg_date,
                                        group_name,
                                        lesson_num,
                                        seg_txt,
                                        warnings,
                                    )
                                    if item:
                                        results.append(item)
                    else:
                        target_groups = page_groups or ["Загальний потік"]
                        for col_idx, cell_txt in non_empty_cells:
                            if not cell_txt:
                                continue
                            if LESSON_NUM_RE.match(cell_txt) or WEEKDAY_RE.match(
                                cell_txt
                            ):
                                continue
                            cell_x0 = t_x0 + col_idx * col_width
                            cell_x1 = cell_x0 + col_width
                            pos_date = _resolve_date_by_position(
                                date_anchors,
                                row_top,
                                cell_x0,
                                cell_x1,
                                base_fallback_date,
                            )
                            cell_groups = _extract_groups_from_text(cell_txt)
                            groups_for_cell = cell_groups or target_groups
                            for seg_date, seg_txt in _split_text_by_dates(
                                cell_txt, pos_date
                            ):
                                for g_name in groups_for_cell:
                                    item = parse_cell_to_lesson(
                                        seg_date, g_name, lesson_num, seg_txt, warnings
                                    )
                                    if item:
                                        results.append(item)

    if not results:
        results = _parse_text_fallback(pdf_path)

    deduped: list[LessonItem] = []
    seen_keys: set[tuple[str, str, int, str, str]] = set()
    for item in results:
        key = (
            item.date,
            item.group,
            item.lesson_number,
            item.subject.lower(),
            (item.teacher or "").lower(),
        )
        if key not in seen_keys:
            seen_keys.add(key)
            deduped.append(item)

    return {
        "lessons": [asdict(r) for r in deduped],
        "warnings": [asdict(w) for w in warnings],
    }


def _format_default_date(fallback_year: str | None) -> str:
    """Last-resort date when nothing could be extracted from the PDF.

    Prefer a neutral semester-start style date over "today", otherwise every
    lesson without a detected header collapses to the same calendar day
    (the day the sync ran).
    """
    y = fallback_year or str(datetime.now().year)
    # Correspondence sessions typically start mid-November / early March
    month = 11 if int(y) >= datetime.now().year else 3
    return f"01.{month:02d}.{y}"


def parse_schedule_pdf(pdf_path: str) -> list[dict]:
    return parse_schedule_pdf_detailed(pdf_path)["lessons"]
