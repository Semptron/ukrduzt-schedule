from dataclasses import asdict, dataclass
from datetime import datetime
import re
import pdfplumber

GROUP_RE = re.compile(
    r"(?<![A-Za-zА-Яа-яІіЇїЄєҐґ0-9-])"
    r"(?:\d{2,4}[-/][A-Za-zА-Яа-яІіЇїЄєҐґ]{2,8}(?:[-/]\d{1,4})?|[A-Za-zА-Яа-яІіЇїЄєҐґ]{2,6}-\d{2,4})"
    r"(?![A-Za-zА-Яа-яІіЇїЄєҐґ0-9-])"
)
DATE_RE = re.compile(r"\b\d{2}\.\d{2}\.\d{4}\b")
NUMERIC_DATE_RE = re.compile(r"\b(\d{1,2})\.(\d{2})\.(\d{4})\b")
SHORT_DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{2})(?!\.\d|\d)")
LESSON_NUM_RE = re.compile(r"^[1-8]$")
LESSON_TYPE_RE = re.compile(
    r"\b(лк|пр|лаб|лік|інд\.?\s*конс\.?|конс\.?|лекц\w*|практ\w*|лаб\w*)\b\.?",
    re.IGNORECASE,
)
ROOM_RE = re.compile(r"\b\d+\.[А-ЯA-Z0-9]+\b")
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

TEACHER_NOISE_RE = re.compile(
    r"(?:,?\s*\b(?:інд\.?\s*конс\w*|індив\w*\s*конс\w*|конс\w*|екз\.?|залік|м|лекц\w*|практ\w*|лаб\w*)\b\.?)+$",
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

    cleaned = TEACHER_NOISE_RE.sub("", cleaned).strip(" ,;|")
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
        if g and g not in found:
            found.append(g)
    return found


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

    parts = [p.strip() for p in cleaned.split(",") if p.strip()]
    subject = parts[0] if parts else cleaned
    raw_teacher = parts[-1] if len(parts) >= 3 else None
    teacher = normalize_teacher_name(raw_teacher)

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


def _column_ranges_from_words(
    page, table_bbox, group_names_by_col: dict[int, str], num_cols: int
) -> dict[int, tuple[float, float]]:
    x0_t, _, x1_t, _ = table_bbox
    col_width = (x1_t - x0_t) / max(num_cols, 1)
    bounds: dict[int, tuple[float, float]] = {}

    for col_idx in range(num_cols):
        c_x0 = x0_t + col_idx * col_width
        c_x1 = c_x0 + col_width
        bounds[col_idx] = (c_x0, c_x1)

    words = page.within_bbox(table_bbox).extract_words()
    for col_idx, g_name in group_names_by_col.items():
        matching = [w for w in words if g_name in w["text"]]
        if matching:
            left = min(w["x0"] for w in matching) - 6
            right = max(w["x1"] for w in matching) + 6
            bounds[col_idx] = (max(x0_t, left), min(x1_t, right))
    return bounds


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
        }
    )


def _parse_text_fallback(pdf_path: str) -> list[LessonItem]:
    full_lines: list[str] = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            for line in txt.splitlines():
                cleaned = re.sub(r"\s+", " ", line).strip()
                if cleaned:
                    full_lines.append(cleaned)

    if not full_lines:
        return []

    joined = "\n".join(full_lines)
    subj_match = SUBJECT_QUOTES_RE.search(joined)
    default_subject = subj_match.group(1).strip() if subj_match else "Заняття"
    default_type = (
        "консультація"
        if ("консультатив" in joined.lower() or "конс" in joined.lower())
        else "заняття"
    )

    zoom_id_match = ZOOM_ID_RE.search(joined)
    zoom_pass_match = ZOOM_PASS_RE.search(joined)
    meeting_code = (
        re.sub(r"\s+", " ", zoom_id_match.group(1)).strip()
        if zoom_id_match
        else None
    )
    meeting_password = zoom_pass_match.group(1).strip() if zoom_pass_match else None

    year_match = re.search(r"\b(202[4-9]|203\d)\b", joined)
    fallback_year = year_match.group(1) if year_match else str(datetime.now().year)

    current_teacher: str | None = None
    current_group = "Загальний потік"
    current_date: str | None = None
    items: list[LessonItem] = []

    for i, line in enumerate(full_lines):
        g_match = GROUP_RE.search(line)
        if g_match:
            current_group = g_match.group(0).strip()

        if "проводить" in line.lower() and i + 1 < len(full_lines):
            next_line = full_lines[i + 1]
            if not any(
                k in next_line.lower()
                for k in ("ідентифікатор", "код доступу", "курс", "пара")
            ):
                current_teacher = normalize_teacher_name(next_line)

        dates = _extract_dates_from_text(line, fallback_year=fallback_year)
        if dates:
            current_date = dates[0]

        pair_match = PAIR_RE.search(line)
        if dates and pair_match:
            pair_nums: list[int] = []
            for grp_idx in (1, 2, 3):
                val = pair_match.group(grp_idx)
                if val and val.isdigit():
                    p = int(val)
                    if p not in pair_nums:
                        pair_nums.append(p)

            for d_str in dates:
                for p_num in pair_nums:
                    start, end = BELLS.get(p_num, ("00:00", "00:00"))
                    items.append(
                        LessonItem(
                            date=d_str,
                            group=current_group,
                            lesson_number=p_num,
                            time_start=start,
                            time_end=end,
                            subject=default_subject,
                            room=None,
                            lesson_type=default_type,
                            teacher=current_teacher,
                            meeting_url=None,
                            meeting_code=meeting_code,
                            meeting_password=meeting_password,
                            raw=line,
                        )
                    )
        elif current_date and LESSON_NUM_RE.match(line) and i + 1 < len(full_lines):
            p_num = int(line)
            next_line = full_lines[i + 1]
            if not _extract_dates_from_text(next_line, fallback_year=fallback_year):
                start, end = BELLS.get(p_num, ("00:00", "00:00"))
                items.append(
                    LessonItem(
                        date=current_date,
                        group=current_group,
                        lesson_number=p_num,
                        time_start=start,
                        time_end=end,
                        subject=next_line,
                        room=None,
                        lesson_type=default_type,
                        teacher=current_teacher,
                        meeting_url=None,
                        meeting_code=meeting_code,
                        meeting_password=meeting_password,
                        raw=f"{p_num}: {next_line}",
                    )
                )

    return items


def parse_schedule_pdf_detailed(pdf_path: str) -> dict:
    results: list[LessonItem] = []
    warnings: list[ParseWarning] = []
    current_date: str | None = None
    fallback_year: str | None = None
    lesson_counter = 0
    group_columns: dict[int, str] = {}
    doc_groups: list[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            page_txt = page.extract_text() or ""
            if not fallback_year:
                ym = re.search(r"\b(202[4-9]|203\d)\b", page_txt)
                if ym:
                    fallback_year = ym.group(1)

            for g in _extract_groups_from_text(page_txt):
                if g not in doc_groups:
                    doc_groups.append(g)

            found_tables = _extract_tables_from_page(page)
            for t_obj in found_tables:
                table = t_obj.extract()
                if not table:
                    continue

                num_cols = max((len(r) for r in table if r), default=0)
                if num_cols == 0:
                    continue

                col_bounds = _column_ranges_from_words(
                    page, t_obj.bbox, group_columns, num_cols
                )

                for row in table:
                    if not row:
                        continue

                    detected_groups: dict[int, str] = {}
                    for col_idx, cell in enumerate(row):
                        txt = clean_cell(cell)
                        match = GROUP_RE.search(txt)
                        if match:
                            detected_groups[col_idx] = match.group(0).strip()

                    if detected_groups:
                        group_columns.update(detected_groups)
                        col_bounds = _column_ranges_from_words(
                            page, t_obj.bbox, group_columns, num_cols
                        )
                        continue

                    new_date = None
                    for cell in row:
                        txt = clean_cell(cell)
                        dates_in_cell = _extract_dates_from_text(
                            txt, fallback_year=fallback_year
                        )
                        if dates_in_cell:
                            new_date = dates_in_cell[0]
                            break

                    if new_date:
                        current_date = new_date
                        lesson_counter = 0

                    active_date = current_date or _format_default_date(fallback_year)
                    active_groups = (
                        group_columns
                        if group_columns
                        else {
                            idx: g
                            for idx, g in enumerate(
                                doc_groups or ["Загальний потік"]
                            )
                        }
                    )

                    non_empty_cells = [
                        (idx, clean_cell(row[idx]))
                        for idx in range(len(row))
                        if clean_cell(row[idx])
                    ]
                    if not non_empty_cells:
                        continue

                    lesson_counter += 1
                    lesson_num = detect_lesson_number(row, lesson_counter)

                    if group_columns:
                        for col_idx, group_name in active_groups.items():
                            cell_txt = (
                                clean_cell(row[col_idx]) if col_idx < len(row) else ""
                            )
                            if not cell_txt:
                                g_x0, g_x1 = col_bounds.get(col_idx, (0.0, 0.0))
                                g_center = (g_x0 + g_x1) / 2
                                for other_idx, other_txt in non_empty_cells:
                                    if other_idx in group_columns:
                                        continue
                                    o_x0, o_x1 = col_bounds.get(other_idx, (0.0, 0.0))
                                    if o_x0 - 8 <= g_center <= o_x1 + 8:
                                        cell_txt = other_txt
                                        break

                            item = parse_cell_to_lesson(
                                active_date, group_name, lesson_num, cell_txt, warnings
                            )
                            if item:
                                results.append(item)
                    else:
                        target_group = doc_groups[0] if doc_groups else "Загальний потік"
                        for _, cell_txt in non_empty_cells:
                            if _extract_dates_from_text(
                                cell_txt, fallback_year=fallback_year
                            ):
                                continue
                            if LESSON_NUM_RE.match(cell_txt):
                                continue
                            item = parse_cell_to_lesson(
                                active_date, target_group, lesson_num, cell_txt, warnings
                            )
                            if item:
                                results.append(item)

    if not results:
        results = _parse_text_fallback(pdf_path)

    return {
        "lessons": [asdict(r) for r in results],
        "warnings": [asdict(w) for w in warnings],
    }


def _format_default_date(fallback_year: str | None) -> str:
    now = datetime.now()
    y = fallback_year or str(now.year)
    return f"{now.day:02d}.{now.month:02d}.{y}"


def parse_schedule_pdf(pdf_path: str) -> list[dict]:
    return parse_schedule_pdf_detailed(pdf_path)["lessons"]
