"""Tests for scraper link extraction and the _process_one_file helper.

These do NOT hit the network: fetch_schedule_links accepts an external httpx
client, and we feed it a fake response via httpx.MockTransport.
"""

from __future__ import annotations

import httpx

import scraper


PORTAL_HTML = """
<html><body>
  <a href="https://kart.edu.ua/wp-content/uploads/2026/11/meh-1-kurs.pdf">Мех 1 курс</a>
  <a href="https://kart.edu.ua/wp-content/uploads/2026/11/ikst-2-kurs.xlsx">ІКСТ 2 курс</a>
  <!-- should be skipped: a learning-process chart -->
  <a href="https://kart.edu.ua/wp-content/uploads/2026/10/grafik-zaochna-26-27.pdf">Графік заочна</a>
  <!-- should be skipped: not a schedule file extension -->
  <a href="https://kart.edu.ua/wp-content/uploads/2026/11/rozklad.html">HTML</a>
  <!-- relative URL should be resolved -->
  <a href="/wp-content/uploads/2026/11/upp-3-kurs.pdf">УПП 3 курс</a>
  <!-- duplicate should be de-duplicated -->
  <a href="https://kart.edu.ua/wp-content/uploads/2026/11/meh-1-kurs.pdf">again</a>
</body></html>
"""


def _mock_client(html: str = PORTAL_HTML) -> httpx.Client:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=html))
    return httpx.Client(
        transport=transport, follow_redirects=True, headers=scraper.DEFAULT_HEADERS
    )


def test_fetch_schedule_links_filters_and_resolves():
    with _mock_client() as client:
        links = scraper.fetch_schedule_links(
            portal_url="https://kart.edu.ua/osvita/portal-rz", client=client
        )

    # Three real schedule files: meh-1-kurs.pdf, ikst-2-kurs.xlsx, upp-3-kurs.pdf
    assert len(links) == 3
    assert any(l.endswith("meh-1-kurs.pdf") for l in links)
    assert any(l.endswith("ikst-2-kurs.xlsx") for l in links)
    assert any(l.endswith("upp-3-kurs.pdf") for l in links)
    # Grafik (chart) and HTML must be excluded
    assert not any("grafik" in l.lower() for l in links)
    assert not any(l.endswith(".html") for l in links)
    # Relative URL must have been resolved to absolute
    assert all(l.startswith("https://") for l in links)


def test_should_skip_url_matches_chart_and_scan():
    assert scraper._should_skip_url(
        "https://kart.edu.ua/wp-content/uploads/2026/10/grafik-zaochna-26-27.pdf"
    )
    assert scraper._should_skip_url(
        "https://kart.edu.ua/wp-content/uploads/2024/12/adobe-scan-18-grud.pdf"
    )
    assert not scraper._should_skip_url(
        "https://kart.edu.ua/wp-content/uploads/2026/11/meh-1-kurs.pdf"
    )


def test_process_one_file_marks_error_on_http_failure(monkeypatch):
    # An unreachable URL → _process_one_file must not raise; it records the
    # error so run_sync can count it as a failure.
    transport = httpx.MockTransport(
        lambda req: httpx.Response(500, text="boom")
    )
    client = httpx.Client(transport=transport, follow_redirects=True)
    # Avoid hitting the DB for the hash check.
    monkeypatch.setattr(scraper, "get_file_hash", lambda url: None)

    res = scraper._process_one_file(
        client, "https://kart.edu.ua/x.pdf", force=False
    )
    client.close()
    assert res.error is not None
    assert res.lessons is None
    assert res.skipped is False
