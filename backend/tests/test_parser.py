from parser import (
    BELLS,
    ParseWarning,
    clean_cell,
    detect_lesson_number,
    parse_cell_to_lesson,
)


def test_clean_cell_normalizes_newlines_and_pipes():
    raw = "  Нарисна геом.\n  та інж. граф.,  |  лк.,\n Бабенко А. О.  "
    assert clean_cell(raw) == "Нарисна геом. та інж. граф., лк., Бабенко А. О."


def test_detect_lesson_number_explicit_and_fallback():
    assert detect_lesson_number(["24.11.2026", "3", "text"], fallback_counter=1) == 3
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
