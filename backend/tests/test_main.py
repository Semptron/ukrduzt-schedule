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
