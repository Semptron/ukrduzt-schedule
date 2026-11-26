"""Tests for the data-quality helpers in quality.py."""

from __future__ import annotations

from quality import (
    is_valid_group,
    is_valid_teacher,
    quality_report,
    sanitize_lesson,
    sanitize_lessons,
)


def _lesson(**overrides) -> dict:
    base = {
        "date": "24.11.2026",
        "group": "101-ЛЛГ-з26",
        "lesson_number": 1,
        "time_start": "09:00",
        "time_end": "10:20",
        "subject": "Нарисна геометрія",
        "room": "2.123",
        "lesson_type": "лк",
        "teacher": "Бабенко А. О.",
        "meeting_url": None,
    }
    base.update(overrides)
    return base


# --- is_valid_group ----------------------------------------------------------

def test_is_valid_group_accepts_real_codes():
    assert is_valid_group("101-ЛЛГ-з26")
    assert is_valid_group("201-ІКСТ-з25")
    assert is_valid_group("304-МЕ")


def test_is_valid_group_rejects_noise():
    assert not is_valid_group(None)
    assert not is_valid_group("")
    assert not is_valid_group("загальний потік")
    assert not is_valid_group("потік")
    assert not is_valid_group("1 курс")
    assert not is_valid_group("економічний факультет")


# --- is_valid_teacher --------------------------------------------------------

def test_is_valid_teacher_accepts_names():
    assert is_valid_teacher("Бабенко А. О.")
    assert is_valid_teacher("Близнюк І. П.")


def test_is_valid_teacher_rejects_garbage():
    assert not is_valid_teacher(None)
    assert not is_valid_teacher("")
    assert not is_valid_teacher("https://zoom.us/j/123?pwd=xyz")
    assert not is_valid_teacher("2.123б")
    assert not is_valid_teacher("аб")  # too short
    assert not is_valid_teacher("a" * 80)  # too long


# --- sanitize_lesson ---------------------------------------------------------

def test_sanitize_lesson_strips_subject_noise():
    item = sanitize_lesson(_lesson(subject="Нарисна геометрія, лк"))
    assert item is not None
    assert "лк" not in item["subject"]
    assert item["subject"] == "Нарисна геометрія"


def test_sanitize_lesson_returns_none_for_invalid_group():
    assert sanitize_lesson(_lesson(group="потік")) is None


def test_sanitize_lesson_extracts_room_from_subject():
    item = sanitize_lesson(_lesson(subject="Фізика 3.101", room=None))
    assert item is not None
    assert item["room"] == "3.101"
    assert "3.101" not in item["subject"]


def test_sanitize_lesson_extracts_teacher_from_subject():
    item = sanitize_lesson(
        _lesson(subject="Вища математика Трикоз Л. В.", teacher=None)
    )
    assert item is not None
    assert item["teacher"] == "Трикоз Л. В."
    assert "Трикоз" not in item["subject"]


# --- sanitize_lessons (dedup) ------------------------------------------------

def test_sanitize_lessons_deduplicates_identical_pairs():
    lessons = [
        _lesson(),
        _lesson(),  # exact duplicate
        _lesson(teacher="Близнюк І. П."),  # different teacher → kept
    ]
    cleaned = sanitize_lessons(lessons)
    assert len(cleaned) == 2


# --- quality_report ----------------------------------------------------------

def test_quality_report_counts():
    items = [
        _lesson(),
        _lesson(teacher=None),
        _lesson(group="201-ІКСТ-з25", teacher="Близнюк І. П."),
    ]
    report = quality_report(items)
    assert report["lessons"] == 3
    assert report["groups"] == 2
    assert report["teachers"] == 2  # Бабенко + Близнюк
    assert report["with_room"] == 3
    assert report["with_teacher"] == 2
    assert 0 < report["teacher_coverage"] < 1
