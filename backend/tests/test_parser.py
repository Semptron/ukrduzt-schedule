from pathlib import Path
from parser import (
    BELLS,
    ParseWarning,
    _extract_dates_from_text,
    clean_cell,
    detect_lesson_number,
    parse_cell_to_lesson,
    parse_schedule_pdf_detailed,
)


def test_clean_cell_normalizes_newlines_and_pipes():
    raw = "  Нарисна геом.\n  та інж. граф.,  |  лк.,\n Бабенко А. О.  "
    assert clean_cell(raw) == "Нарисна геом. та інж. граф., лк., Бабенко А. О."


def test_detect_lesson_number_explicit_and_fallback():
    assert detect_lesson_number(["24.11.2026", "3", "text"], fallback_counter=1) == 3
    assert detect_lesson_number(["24.11.2026", "2 пара", "text"], fallback_counter=1) == 2
    assert detect_lesson_number(["Вівторок", "", "text"], fallback_counter=5) == 5
    assert detect_lesson_number(["Вівторок", "", "text"], fallback_counter=12) == 8


def test_parse_cell_standard_lesson():
    warnings: list[ParseWarning] = []
    item = parse_cell_to_lesson(
        date_str="24.11.2026",
        group="101-ЛЛГ-326",
        lesson_number=1,
        raw_text="Нарисна геом. та інж. граф., 2.123, лк., Бабенко А. О.",
        warnings=warnings,
    )
    assert item is not None
    assert item.date == "24.11.2026"
    assert item.group == "101-ЛЛГ-326"
    assert item.lesson_number == 1
    assert (item.time_start, item.time_end) == BELLS[1]
    assert item.subject == "Нарисна геом. та інж. граф."
    assert item.room == "2.123"
    assert item.lesson_type == "лк"
    assert item.teacher == "Бабенко А. О."
    assert warnings == []


def test_parse_cell_consultation_logs_warning():
    warnings: list[ParseWarning] = []
    item = parse_cell_to_lesson(
        date_str="29.11.2026",
        group="102-BTI-326",
        lesson_number=2,
        raw_text="інд. конс. Панчук О. В.",
        warnings=warnings,
    )
    assert item is not None
    assert item.lesson_type == "інд. конс."
    assert len(warnings) == 1
    assert warnings[0].reason == "missing_type_or_location"


def test_parse_cell_ignores_standalone_date_or_number():
    warnings: list[ParseWarning] = []
    assert parse_cell_to_lesson("24.11.2026", "101-ЛЛГ-326", 1, "24.11.2026", warnings) is None
    assert parse_cell_to_lesson("24.11.2026", "101-ЛЛГ-326", 1, "3", warnings) is None
    assert warnings == []


def test_extract_dates_numeric_and_ukrainian_text():
    assert _extract_dates_from_text("24.11.2026") == ["24.11.2026"]
    assert _extract_dates_from_text("5 листопада 2026") == ["05.11.2026"]


def test_parse_schedule_pdf_detailed_text_fallback(tmp_path: Path):
    pdf_file = tmp_path / "sample.pdf"
    # Мінімальний валідний односторінковий PDF із текстовим рядком
    pdf_content = (
        "%PDF-1.4\n"
        "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
        "/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
        "4 0 obj << /Length 55 >> stream\n"
        "BT /F1 12 Tf 20 100 Td (101-LLG-326 24.11.2026 1 para) Tj ET\n"
        "endstream endobj\n"
        "5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        "xref\n0 6\n0000000000 65535 f \n"
        "0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
        "0000000241 00000 n \n0000000346 00000 n \n"
        "trailer << /Size 6 /Root 1 0 R >>\nstartxref\n416\n%%EOF\n"
    )
    pdf_file.write_bytes(pdf_content.encode("latin-1"))

    parsed = parse_schedule_pdf_detailed(str(pdf_file))
    assert "lessons" in parsed and "warnings" in parsed
