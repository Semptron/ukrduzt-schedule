"""Integration tests for database.query_lessons teacher/group filtering."""

import pathlib
import tempfile

import database


def _fresh_db(monkeypatch) -> None:
    tmp = pathlib.Path(tempfile.mkdtemp()) / "test.db"
    monkeypatch.setattr(database, "DB_PATH", tmp)
    database.init_db()


def _seed(items: list[dict]) -> None:
    database.replace_source_lessons("http://x", "hash1", items, 0)


def _lesson(teacher: str | None, subject: str, n: int = 1) -> dict:
    return {
        "date": "24.11.2026",
        "group": "101-ЛЛГ-з26",
        "lesson_number": n,
        "time_start": "09:00",
        "time_end": "10:20",
        "subject": subject,
        "room": "2.123",
        "lesson_type": "лк",
        "teacher": teacher,
        "meeting_url": None,
        "meeting_code": None,
        "meeting_password": None,
        "raw": "x",
    }


def test_query_by_teacher_partial_case_insensitive(monkeypatch):
    _fresh_db(monkeypatch)
    _seed([
        _lesson("Бабенко А. О.", "Нарисна геом.", 1),
        _lesson("Близнюк І. П.", "Фізика", 2),
    ])

    res = database.query_lessons(teacher="баб")
    assert len(res) == 1
    assert res[0]["teacher"] == "Бабенко А. О."
    assert res[0]["subject"] == "Нарисна геом."


def test_query_by_teacher_full_name_with_spaces(monkeypatch):
    _fresh_db(monkeypatch)
    _seed([
        _lesson("Бабенко А. О.", "Нарисна геом.", 1),
        _lesson("Близнюк І. П.", "Фізика", 2),
    ])

    res = database.query_lessons(teacher="Близнюк І. П.")
    assert len(res) == 1
    assert res[0]["teacher"] == "Близнюк І. П."


def test_query_by_teacher_no_match(monkeypatch):
    _fresh_db(monkeypatch)
    _seed([_lesson("Бабенко А. О.", "Нарисна геом.", 1)])
    assert database.query_lessons(teacher="Невідомий") == []


def test_query_by_group_returns_all_for_group(monkeypatch):
    _fresh_db(monkeypatch)
    _seed([
        _lesson("Бабенко А. О.", "Нарисна геом.", 1),
        _lesson("Близнюк І. П.", "Фізика", 2),
    ])
    assert len(database.query_lessons(group="101-ЛЛГ-з26")) == 2


def test_query_by_group_and_teacher_combined(monkeypatch):
    _fresh_db(monkeypatch)
    _seed([
        _lesson("Бабенко А. О.", "Нарисна геом.", 1),
        _lesson("Близнюк І. П.", "Фізика", 2),
    ])
    res = database.query_lessons(group="101-ЛЛГ-з26", teacher="баб")
    assert len(res) == 1
    assert res[0]["teacher"] == "Бабенко А. О."
