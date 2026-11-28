"""Integration tests for the FastAPI app in main.py.

Covers the two biggest concurrency fixes:
- /api/v1/sync sets the running flag synchronously, so /health observed
  "in_progress=True" the instant a sync was requested (no race window).
- /api/v1/schedule returns 503 (Retry-After) — not 404 — whenever a sync is
  running AND there are no matching rows yet, so the client does not cache
  "not found" during a forced re-sync.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import main

# Capture the real _start_sync once at import time, before any fixture
# patches it. Tests that need to exercise the full path restore this.
_REAL_START_SYNC = main._start_sync


@pytest.fixture(autouse=True)
def _reset_sync_state(monkeypatch) -> Iterator[None]:
    """Reset module-level sync state between tests so they don't leak.

    The app's lifespan kicks off an initial sync on startup; we stub the
    background loop and the startup trigger so tests start from a clean,
    idle state and only the explicit POST /api/v1/sync calls drive it.
    """
    main._sync_running.clear()
    main._initial_sync_done = False
    main._last_sync_stats = None
    main._last_sync_error = None
    # Make run_sync a fast no-op by default; individual tests override this
    # with a blocking variant to exercise the "sync in flight" code paths.
    monkeypatch.setattr(
        main,
        "run_sync",
        lambda force=False: {
            "found_files": 0,
            "updated_files": 0,
            "failed_files": 0,
            "parsed_lessons": 0,
        },
    )
    # Stop the lifespan background loop and the immediate first sync from
    # interfering — tests drive sync state explicitly via POST /api/v1/sync.
    monkeypatch.setattr(main, "SYNC_INTERVAL_SECONDS", 10_000)
    monkeypatch.setattr(main, "_start_sync", lambda force=False: False)
    yield
    main._sync_running.clear()


def _enable_real_sync(monkeypatch):
    """Restore the real _start_sync for tests that exercise the full path."""
    monkeypatch.setattr(main, "_start_sync", _REAL_START_SYNC)


def test_sync_token_guards_force(monkeypatch):
    """When SYNC_TOKEN is set, force=true without the header is 403."""
    monkeypatch.setattr(main, "SYNC_TOKEN", "secret-token", raising=False)
    with TestClient(main.app) as client:
        resp = client.post("/api/v1/sync?force=true")
        assert resp.status_code == 403
        # Non-force sync is still allowed without a token.
        resp2 = client.post("/api/v1/sync")
        assert resp2.status_code == 202


def test_sync_token_allows_force_with_header(monkeypatch):
    monkeypatch.setattr(main, "SYNC_TOKEN", "secret-token", raising=False)
    _enable_real_sync(monkeypatch)

    # The lifespan starts an initial sync on `TestClient` enter. To make the
    # POST /api/v1/sync below observe a clean "started" (not "already_running"),
    # we let that initial sync finish first with a fast no-op, then swap to a
    # blocking slow_sync for the explicit trigger.
    initial_done = threading.Event()

    def fast_sync(force=False):
        initial_done.set()
        return {"found_files": 0, "updated_files": 0, "failed_files": 0, "parsed_lessons": 0}

    monkeypatch.setattr(main, "run_sync", fast_sync)

    started = threading.Event()
    release = threading.Event()

    def slow_sync(force=False):
        started.set()
        release.wait(timeout=5)
        return {"found_files": 0, "updated_files": 0, "failed_files": 0, "parsed_lessons": 0}

    with TestClient(main.app) as client:
        # Wait for the lifespan's initial sync to finish and clear the flag.
        assert initial_done.wait(timeout=5)
        # Now swap to the blocking version for the explicit POST.
        monkeypatch.setattr(main, "run_sync", slow_sync)
        main._sync_running.clear()  # make sure we're starting from idle

        resp = client.post(
            "/api/v1/sync?force=true",
            headers={"X-Sync-Token": "secret-token"},
        )
        assert resp.status_code == 202
        assert resp.json()["status"] == "started"
        assert started.wait(timeout=5), "slow_sync did not start"
        # The running flag must be visible synchronously while the sync is
        # in flight — this is the race-window fix.
        assert main._sync_running.is_set() is True
        release.set()


def test_schedule_returns_503_while_sync_running(monkeypatch):
    """During ANY sync (initial or forced), an empty result must be 503, not 404."""
    _enable_real_sync(monkeypatch)

    # Let the lifespan's initial sync finish fast, then swap to a blocking
    # slow_sync for the explicit POST so the schedule query observes the
    # in-flight state.
    initial_done = threading.Event()

    def fast_sync(force=False):
        initial_done.set()
        return {"found_files": 0, "updated_files": 0, "failed_files": 0, "parsed_lessons": 0}

    monkeypatch.setattr(main, "run_sync", fast_sync)
    monkeypatch.setattr(main, "query_lessons", lambda **kw: [])

    started = threading.Event()
    release = threading.Event()

    def slow_sync(force=False):
        started.set()
        release.wait(timeout=5)
        return {"found_files": 0, "updated_files": 0, "failed_files": 0, "parsed_lessons": 0}

    with TestClient(main.app) as client:
        assert initial_done.wait(timeout=5)
        monkeypatch.setattr(main, "run_sync", slow_sync)
        main._sync_running.clear()

        # Kick off a sync that will block.
        client.post("/api/v1/sync")
        assert started.wait(timeout=5), "slow_sync did not start"
        # While it is running, a schedule query for a missing group must be 503.
        resp = client.get("/api/v1/schedule?group=101-ЛЛГ-з26")
        assert resp.status_code == 503
        assert resp.headers.get("Retry-After") == "10"
        release.set()


def test_schedule_returns_404_when_idle_and_empty(monkeypatch):
    """When no sync is running, an empty result is a genuine 404."""
    monkeypatch.setattr(main, "query_lessons", lambda **kw: [])
    with TestClient(main.app) as client:
        # Make sure no sync is in flight.
        main._sync_running.clear()
        resp = client.get("/api/v1/schedule?group=101-ЛЛГ-з26")
        assert resp.status_code == 404


def test_schedule_requires_group_or_teacher():
    with TestClient(main.app) as client:
        resp = client.get("/api/v1/schedule")
        assert resp.status_code == 400


def test_health_reports_running_flag(monkeypatch):
    _enable_real_sync(monkeypatch)

    initial_done = threading.Event()

    def fast_sync(force=False):
        initial_done.set()
        return {"found_files": 0, "updated_files": 0, "failed_files": 0, "parsed_lessons": 0}

    monkeypatch.setattr(main, "run_sync", fast_sync)

    started = threading.Event()
    release = threading.Event()

    def slow_sync(force=False):
        started.set()
        release.wait(timeout=5)
        return {"found_files": 0, "updated_files": 0, "failed_files": 0, "parsed_lessons": 0}

    with TestClient(main.app) as client:
        assert initial_done.wait(timeout=5)
        monkeypatch.setattr(main, "run_sync", slow_sync)
        main._sync_running.clear()
        client.post("/api/v1/sync")
        assert started.wait(timeout=5), "slow_sync did not start"
        resp = client.get("/health")
        assert resp.json()["sync_in_progress"] is True
        release.set()


# ---------------------------------------------------------------------------
# Manual schedule upload
# ---------------------------------------------------------------------------
# These tests exercise the full upload → parse → persist → query path on an
# isolated SQLite file, so they don't pollute the developer's data/schedule.db.

import database
from pathlib import Path

# A tiny but valid one-page PDF. Its text content is picked up by the parser's
# text-fallback path. We build it from parts so the stream text (which needs
# Cyrillic for a real group code like 101-ЛЛГ-326) can be UTF-8-encoded rather
# than an ASCII-only bytes literal — Python bytes literals cannot contain
# non-ASCII directly.
def _build_minimal_pdf() -> bytes:
    # Multi-line ASCII text so pdfplumber's text extractor (and the parser's
    # text-fallback path) see a clean group line, a "1" pair line, and a
    # subject+room line. Helvetica has no Cyrillic glyphs, so we stick to
    # Latin-script content; the group code 101-LLG-326 is still matched by
    # GROUP_RE and 2.123 by ROOM_RE.
    stream_text = (
        "BT /F1 12 Tf 20 100 Td (101-LLG-326 24.11.2026) Tj "
        "0 -15 Td (1) Tj "
        "0 -15 Td (Math 2.123) Tj ET"
    )
    stream_bytes = stream_text.encode("latin-1")
    stream_len = len(stream_bytes)
    header = (
        b"%PDF-1.4\n"
        b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
    )
    stream_obj = (
        b"4 0 obj << /Length " + str(stream_len).encode() + b" >> stream\n"
        + stream_bytes + b"\n" + b"endstream endobj\n"
    )
    footer = (
        b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
        b"xref\n0 6\n0000000000 65535 f \n"
        b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n0\n%%EOF\n"
    )
    return header + stream_obj + footer


_MINIMAL_PDF_BYTES = _build_minimal_pdf()


@pytest.fixture
def _isolated_db(tmp_path: Path, monkeypatch) -> Path:
    """Point database.DB_PATH and the parser at a throwaway SQLite file."""
    db_path = tmp_path / "test_schedule.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    # main imports `from database import ...` names; rebind those too.
    monkeypatch.setattr(main, "init_db", database.init_db, raising=False)
    database.init_db()
    return db_path


def _uploads_client(monkeypatch, with_token: str | None = None):
    """Return a TestClient whose lifespan won't hit the network."""
    if with_token is not None:
        monkeypatch.setattr(main, "SYNC_TOKEN", with_token, raising=False)
    return TestClient(main.app)


def test_upload_rejects_non_pdf(monkeypatch, _isolated_db):
    with _uploads_client(monkeypatch) as client:
        resp = client.post(
            "/api/v1/admin/upload",
            files={"file": ("not_a_pdf.txt", b"hello", "text/plain")},
        )
        assert resp.status_code == 400
        assert "PDF" in resp.json()["detail"]


def test_upload_rejects_pdf_named_but_not_pdf(monkeypatch, _isolated_db):
    # A .pdf extension but wrong magic bytes — must be rejected, not crashed.
    with _uploads_client(monkeypatch) as client:
        resp = client.post(
            "/api/v1/admin/upload",
            files={"file": ("fake.pdf", b"not a real pdf", "application/pdf")},
        )
        assert resp.status_code == 400
        assert "%PDF" in resp.json()["detail"] or "заголовок" in resp.json()["detail"]


def test_upload_requires_token_when_set(monkeypatch, _isolated_db):
    monkeypatch.setattr(main, "SYNC_TOKEN", "secret", raising=False)
    with _uploads_client(monkeypatch) as client:
        resp = client.post(
            "/api/v1/admin/upload",
            files={"file": ("rozklad.pdf", _MINIMAL_PDF_BYTES, "application/pdf")},
        )
        assert resp.status_code == 403


def test_upload_accepts_pdf_without_token(monkeypatch, _isolated_db):
    # No SYNC_TOKEN set → open upload (dev mode). Should succeed.
    monkeypatch.setattr(main, "SYNC_TOKEN", None, raising=False)
    with _uploads_client(monkeypatch) as client:
        resp = client.post(
            "/api/v1/admin/upload",
            files={"file": ("Розклад-2-курс.pdf", _MINIMAL_PDF_BYTES, "application/pdf")},
            data={"note": "від куратора 24.11"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["status"] == "ok"
        assert body["original_name"] == "Розклад-2-курс.pdf"
        assert body["note"] == "від куратора 24.11"
        assert isinstance(body["manual_id"], int)
        assert body["source_url"].startswith("manual://")


def test_upload_then_list_then_delete(monkeypatch, _isolated_db):
    monkeypatch.setattr(main, "SYNC_TOKEN", None, raising=False)
    with _uploads_client(monkeypatch) as client:
        # Upload
        up = client.post(
            "/api/v1/admin/upload",
            files={"file": ("rozklad.pdf", _MINIMAL_PDF_BYTES, "application/pdf")},
        )
        assert up.status_code == 201
        manual_id = up.json()["manual_id"]

        # List — the uploaded file should appear.
        lst = client.get("/api/v1/admin/uploads")
        assert lst.status_code == 200
        items = lst.json()["uploads"]
        assert any(i["id"] == manual_id for i in items)
        assert items[0]["original_name"] == "rozklad.pdf"

        # Delete — should remove the file and its lessons.
        dele = client.delete(f"/api/v1/admin/uploads/{manual_id}")
        assert dele.status_code == 200
        assert dele.json()["status"] == "deleted"

        # List again — the file is gone.
        lst2 = client.get("/api/v1/admin/uploads")
        assert not any(i["id"] == manual_id for i in lst2.json()["uploads"])

        # Deleting a non-existent id is 404.
        again = client.delete(f"/api/v1/admin/uploads/{manual_id}")
        assert again.status_code == 404


def test_list_uploads_requires_token_when_set(monkeypatch, _isolated_db):
    monkeypatch.setattr(main, "SYNC_TOKEN", "secret", raising=False)
    with _uploads_client(monkeypatch) as client:
        resp = client.get("/api/v1/admin/uploads")
        assert resp.status_code == 403
        # With the right token it succeeds.
        ok = client.get("/api/v1/admin/uploads", headers={"X-Sync-Token": "secret"})
        assert ok.status_code == 200


def test_manual_overrides_portal_in_query(monkeypatch, _isolated_db):
    """A manual upload shadows the portal row for the same (date, group, pair).

    This is the core of the feature: the curator's file wins over the site.
    We seed a portal row directly, then upload a manual file whose lesson has
    the same key but a different subject, and check query_lessons returns
    only the manual one.
    """
    monkeypatch.setattr(main, "SYNC_TOKEN", None, raising=False)
    with _uploads_client(monkeypatch) as client:
        # Seed a portal lesson for 101-LLG-326 / 24.11.2026 / pair 1.
        database.replace_source_lessons(
            source_url="https://portal.example/x.pdf",
            file_hash="abc",
            items=[
                {
                    "date": "24.11.2026",
                    "group": "101-LLG-326",
                    "lesson_number": 1,
                    "time_start": "09:00",
                    "time_end": "10:20",
                    "subject": "Portal subject",
                    "lesson_type": "лк",
                    "teacher": "Бабенко А. О.",
                    "raw": "portal row",
                }
            ],
        )

        # Upload a manual file whose PDF yields the SAME key but a different
        # subject (Math). The parser's text-fallback produces a manual row for
        # 101-LLG-326 / 24.11.2026 / pair 1 / subject "Math".
        up = client.post(
            "/api/v1/admin/upload",
            files={"file": ("kurator.pdf", _MINIMAL_PDF_BYTES, "application/pdf")},
            data={"note": "від куратора"},
        )
        assert up.status_code == 201, up.text

        # Query the group — the portal row must be shadowed by the manual one.
        rows = database.query_lessons(group="101-LLG-326", date="24.11.2026")
        manual_rows = [r for r in rows if r.get("source_type") == "manual"]
        portal_rows = [r for r in rows if r.get("source_type") == "portal"]
        assert len(manual_rows) >= 1, "manual row should exist"
        assert len(portal_rows) == 0, "portal row should be shadowed for that key"

        # Delete the manual file — the portal row must come back.
        manual_id = up.json()["manual_id"]
        client.delete(f"/api/v1/admin/uploads/{manual_id}")
        rows_after = database.query_lessons(group="101-LLG-326", date="24.11.2026")
        assert any(r.get("source_type") == "portal" for r in rows_after), (
            "portal row should reappear after the manual file is removed"
        )


# ---------------------------------------------------------------------------
# Source filtering (?source=portal|manual|all) for /groups, /teachers, /schedule
# ---------------------------------------------------------------------------

def _seed_two_sources():
    """Seed one portal + one manual group so source filters have something
    to split. Portal-only group: 200-АА-з26; manual-only group: 300-ББ-з26;
    shared group 101-LLG-326 with a different teacher per source."""
    database.replace_source_lessons(
        source_url="https://portal.example/a.pdf",
        file_hash="ha",
        items=[
            {
                "date": "24.11.2026",
                "group": "200-АА-з26",
                "lesson_number": 1,
                "time_start": "09:00",
                "time_end": "10:20",
                "subject": "Портальний предмет А",
                "lesson_type": "лк",
                "teacher": "Бабенко А. О.",
                "raw": "p-a",
            },
            {
                "date": "24.11.2026",
                "group": "101-LLG-326",
                "lesson_number": 1,
                "time_start": "09:00",
                "time_end": "10:20",
                "subject": "Портальний предмет Спільний",
                "lesson_type": "лк",
                "teacher": "Осмаєв О. А.",
                "raw": "p-shared",
            },
        ],
    )
    database.add_manual_file(
        original_name="kurator.pdf",
        file_hash="hm",
        items=[
            {
                "date": "24.11.2026",
                "group": "300-ББ-з26",
                "lesson_number": 1,
                "time_start": "09:00",
                "time_end": "10:20",
                "subject": "Кураторський предмет Б",
                "lesson_type": "лк",
                "teacher": "Сніжко І. А.",
                "raw": "m-b",
            },
            {
                "date": "24.11.2026",
                "group": "101-LLG-326",
                "lesson_number": 1,
                "time_start": "09:00",
                "time_end": "10:20",
                "subject": "Кураторський предмет Спільний",
                "lesson_type": "лк",
                "teacher": "Казанко О. В.",
                "raw": "m-shared",
            },
        ],
        warnings_count=0,
        note="test",
    )


def test_list_groups_filters_by_source(monkeypatch, _isolated_db):
    monkeypatch.setattr(main, "SYNC_TOKEN", None, raising=False)
    _seed_two_sources()
    all_groups = set(database.list_groups())
    portal_groups = set(database.list_groups(source_type="portal"))
    manual_groups = set(database.list_groups(source_type="manual"))
    assert "200-АА-з26" in all_groups and "300-ББ-з26" in all_groups
    assert "200-АА-з26" in portal_groups and "300-ББ-з26" not in portal_groups
    assert "300-ББ-з26" in manual_groups and "200-АА-з26" not in manual_groups
    # 'all' == None (no filter)
    assert set(database.list_groups(source_type="all")) == all_groups


def test_list_teachers_filters_by_source(monkeypatch, _isolated_db):
    monkeypatch.setattr(main, "SYNC_TOKEN", None, raising=False)
    _seed_two_sources()
    portal_t = set(database.list_teachers(source_type="portal"))
    manual_t = set(database.list_teachers(source_type="manual"))
    assert "Бабенко А. О." in portal_t and "Бабенко А. О." not in manual_t
    assert "Сніжко І. А." in manual_t and "Сніжко І. А." not in portal_t


def test_query_lessons_source_manual_skips_dedup(monkeypatch, _isolated_db):
    """?source=manual returns only manual rows for the shared group, even for
    the (date, group, pair) that also has a portal row — and does NOT return
    the portal row."""
    monkeypatch.setattr(main, "SYNC_TOKEN", None, raising=False)
    _seed_two_sources()
    rows = database.query_lessons(group="101-LLG-326", date="24.11.2026", source_type="manual")
    assert len(rows) == 1
    assert rows[0]["source_type"] == "manual"
    assert "Кураторський" in rows[0]["subject"]


def test_query_lessons_source_portal_returns_portal_only(monkeypatch, _isolated_db):
    monkeypatch.setattr(main, "SYNC_TOKEN", None, raising=False)
    _seed_two_sources()
    rows = database.query_lessons(group="101-LLG-326", date="24.11.2026", source_type="portal")
    assert len(rows) == 1
    assert rows[0]["source_type"] == "portal"
    assert "Портальний" in rows[0]["subject"]


def test_query_lessons_source_all_dedups_manual_over_portal(monkeypatch, _isolated_db):
    """Default (no source / 'all') still applies manual-over-portal dedup."""
    monkeypatch.setattr(main, "SYNC_TOKEN", None, raising=False)
    _seed_two_sources()
    rows = database.query_lessons(group="101-LLG-326", date="24.11.2026")
    assert len(rows) == 1
    assert rows[0]["source_type"] == "manual"
    # The portal-only and manual-only groups still show up under 'all'.
    all_rows = database.query_lessons(date="24.11.2026")
    groups = {r["group"] for r in all_rows}
    assert {"200-АА-з26", "300-ББ-з26", "101-LLG-326"} <= groups


def test_groups_endpoint_source_filter(monkeypatch, _isolated_db):
    """The HTTP endpoint forwards ?source= to list_groups and validates it."""
    monkeypatch.setattr(main, "SYNC_TOKEN", None, raising=False)
    _seed_two_sources()
    with TestClient(main.app) as client:
        # bogus source → 422 (pattern validation)
        bad = client.get("/api/v1/groups?source=bogus")
        assert bad.status_code == 422
        # manual only
        m = client.get("/api/v1/groups?source=manual")
        assert m.status_code == 200
        g = set(m.json()["groups"])
        assert "300-ББ-з26" in g and "200-АА-з26" not in g
        # portal only
        p = client.get("/api/v1/groups?source=portal")
        assert p.status_code == 200
        gp = set(p.json()["groups"])
        assert "200-АА-з26" in gp and "300-ББ-з26" not in gp


def test_schedule_endpoint_source_filter(monkeypatch, _isolated_db):
    monkeypatch.setattr(main, "SYNC_TOKEN", None, raising=False)
    _seed_two_sources()
    with TestClient(main.app) as client:
        main._sync_running.clear()
        # manual-only view of the shared group
        r = client.get("/api/v1/schedule?group=101-LLG-326&source=manual")
        assert r.status_code == 200
        lessons = r.json()["items"]
        assert len(lessons) == 1
        assert lessons[0]["source_type"] == "manual"
        # default view dedups to manual too
        r2 = client.get("/api/v1/schedule?group=101-LLG-326")
        assert r2.status_code == 200
        assert r2.json()["items"][0]["source_type"] == "manual"
