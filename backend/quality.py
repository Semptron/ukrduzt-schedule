"""
Data-quality helpers for UkrDUZT schedule.

Used after PDF parse and when listing teachers/groups, so garbage
(room numbers, Zoom URLs, merged subject+teacher strings) never reaches the API.
"""

from __future__ import annotations

import re
from typing import Any

from parser import (
    FULL_NAME_RE,
    ROOM_RE,
    SURNAME_WITH_INITIALS_RE,
    normalize_teacher_name,
)

# Tokens that must never appear as a teacher name
_BAD_TEACHER_RE = re.compile(
    r"(https?://|zoom\.us|pwd=|passcode|автогальм|"
    r"^[0-9]+(\.[0-9а-яіїєґa-z]+)?$|"
    r"^[A-Za-z0-9+/=_-]{16,}$)",
    re.IGNORECASE,
)

_GROUP_OK_RE = re.compile(
    r"^\d{2,4}[-/][A-Za-zА-Яа-яІіЇїЄєҐґ]{1,12}"
    r"(?:[-/][A-Za-zА-Яа-яІіЇїЄєҐґ0-9]{1,8})?$"
)

_SUBJECT_NOISE_RE = re.compile(
    r"\s*(?:,\s*)?(?:лк|пр|лаб|лек|практ)\.?\s*$",
    re.IGNORECASE,
)


def is_valid_teacher(name: str | None) -> bool:
    if not name or not str(name).strip():
        return False
    n = str(name).strip()
    if _BAD_TEACHER_RE.search(n):
        return False
    if ROOM_RE.fullmatch(n):
        return False
    if len(n) < 4 or len(n) > 60:
        return False
    normalized = normalize_teacher_name(n)
    if not normalized:
        return False
    # Reject subject-like blobs that slipped through
    if "," in normalized or "  " in normalized:
        return False
    return True


def is_valid_group(name: str | None) -> bool:
    if not name:
        return False
    g = str(name).strip()
    if g.lower() in {"загальний потік", "потік", "група"}:
        return False
    return bool(_GROUP_OK_RE.match(g))


def sanitize_lesson(item: dict[str, Any]) -> dict[str, Any] | None:
    """
    Clean one lesson dict. Returns None if the lesson is not usable.
    Mutates a shallow copy — original is left intact.
    """
    out = dict(item)

    group = (out.get("group") or out.get("group_name") or "").strip()
    if not is_valid_group(group):
        return None
    out["group"] = group

    subject = re.sub(r"\s+", " ", str(out.get("subject") or "")).strip(" ,;-")
    subject = _SUBJECT_NOISE_RE.sub("", subject).strip(" ,;-")

    teacher_raw = out.get("teacher")
    teacher = normalize_teacher_name(teacher_raw) if teacher_raw else None
    if teacher and not is_valid_teacher(teacher):
        teacher = None

    # If teacher is stuck inside subject ("... Трикоз Л. В."), peel it out
    if not teacher and subject:
        m = re.search(
            r"\b([А-ЯІЇЄҐ][а-яіїєґ'’`-]{1,25}\s+[А-ЯІЇЄҐ]\.(?:\s*[А-ЯІЇЄҐ]\.){0,2})\s*$",
            subject,
        )
        if m:
            cand = normalize_teacher_name(m.group(1))
            if cand and is_valid_teacher(cand):
                teacher = cand
                subject = subject[: m.start()].strip(" ,;-")

    # Room: keep only real room codes
    room = out.get("room")
    if room:
        room = str(room).strip()
        if not ROOM_RE.fullmatch(room):
            # try to find a room inside the string
            rm = ROOM_RE.search(room)
            room = rm.group(0) if rm else None
    if not room and subject:
        rm = ROOM_RE.search(subject)
        if rm:
            room = rm.group(0)
            subject = subject.replace(room, " ").strip(" ,;-")
            subject = re.sub(r"\s+", " ", subject)

    # Drop teacher echo from subject
    if teacher and teacher in subject:
        subject = subject.replace(teacher, " ")
        subject = re.sub(r"\s+", " ", subject).strip(" ,;-")

    if not subject or len(subject) < 2:
        return None

    # Meeting URL hygiene
    url = out.get("meeting_url")
    if url and not re.match(r"https?://", str(url), re.I):
        url = None

    out["subject"] = subject
    out["teacher"] = teacher
    out["room"] = room
    out["meeting_url"] = url
    return out


def sanitize_lessons(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for raw in items:
        item = sanitize_lesson(raw)
        if not item:
            continue
        key = (
            item.get("date"),
            item.get("group"),
            item.get("lesson_number"),
            (item.get("subject") or "").lower(),
            (item.get("teacher") or "").lower(),
        )
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    return cleaned


def quality_report(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Quick stats for /api/v1/stats."""
    teachers = {
        t
        for it in items
        if (t := it.get("teacher")) and is_valid_teacher(t)
    }
    groups = {
        g
        for it in items
        if (g := it.get("group") or it.get("group_name")) and is_valid_group(g)
    }
    dates = sorted({it.get("date") for it in items if it.get("date")})
    with_room = sum(1 for it in items if it.get("room"))
    with_teacher = sum(1 for it in items if it.get("teacher") and is_valid_teacher(it["teacher"]))
    return {
        "lessons": len(items),
        "groups": len(groups),
        "teachers": len(teachers),
        "dates": dates,
        "with_room": with_room,
        "with_teacher": with_teacher,
        "teacher_coverage": round(with_teacher / len(items), 3) if items else 0.0,
    }
