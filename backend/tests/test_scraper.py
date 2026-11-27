"""Tests for scraper link extraction and the _process_one_file helper.

These do NOT hit the network: fetch_schedule_links accepts an external httpx
client, and we feed it a fake response via httpx.MockTransport.
"""

from __future__ import annotations

import io

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

# A minimal valid PDF that pdfplumber can open. The body is deliberately
# unstructured text so the parser falls through to the text fallback path
# (which is what we want here — we only need _process_one_file to not crash
# and to return a _ParseResult).
_MINIMAL_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
    b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
    b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
    b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >> endobj\n"
    b"4 0 obj << /Length 55 >> stream\n"
    b"BT /F1 12 Tf 20 100 Td (101-LLG-326 24.11.2026 1 para) Tj ET\n"
    b"endstream endobj\n"
    b"5 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
    b"xref\n0 6\n0000000000 65535 f \n"
    b"0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n"
    b"0000000241 00000 n \n0000000346 00000 n \n"
    b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n416\n%%EOF\n"
)


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


def test_process_one_file_marks_error_on_http_4xx(monkeypatch):
    # A permanent 4xx (404) must raise immediately without retrying — we
    # avoid the 5xx retry path's ~6s of backoff here.
    transport = httpx.MockTransport(
        lambda req: httpx.Response(404, text="missing")
    )
    client = httpx.Client(transport=transport, follow_redirects=True)
    monkeypatch.setattr(scraper, "get_file_hash", lambda url: None)

    res = scraper._process_one_file(
        client, "https://kart.edu.ua/x.pdf", force=False
    )
    client.close()
    assert res.error is not None
    assert res.lessons is None
    assert res.skipped is False


def test_process_one_file_parses_cyrillic_named_pdf_in_memory(monkeypatch):
    """Regression for the Cyrillic-filename collision bug.

    Two different Cyrillic-named PDFs used to sanitize to the same on-disk
    temp path and overwrite each other under the parallel download pool.
    The parser now reads straight from an in-memory buffer, so two distinct
    URLs with Cyrillic names both parse to their own _ParseResult.
    """
    def serve(req):
        return httpx.Response(200, content=_MINIMAL_PDF)

    transport = httpx.MockTransport(serve)
    client = httpx.Client(transport=transport, follow_redirects=True)
    monkeypatch.setattr(scraper, "get_file_hash", lambda url: None)

    url_a = "https://kart.edu.ua/wp-content/uploads/2026/10/Розклад-2-курс_3-сем.pdf"
    url_b = "https://kart.edu.ua/wp-content/uploads/2026/10/Розклад-210-SL-D26-_І-семестр.pdf"

    res_a = scraper._process_one_file(client, url_a, force=False)
    res_b = scraper._process_one_file(client, url_b, force=False)
    client.close()

    # Both must succeed without collision — neither should record an error,
    # and the two file hashes must be identical only because the bytes are
    # identical (the bug made them overwrite, not because of hash equality).
    assert res_a.error is None
    assert res_b.error is None
    assert res_a.file_hash is not None
    assert res_b.file_hash is not None
    # Same content → same hash (sanity), but distinct URLs were processed.
    assert res_a.url == url_a
    assert res_b.url == url_b


def test_process_one_file_skips_non_pdf_bytes(monkeypatch):
    """A file with a .pdf URL but non-PDF magic bytes is skipped, not crashed."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=b"not a pdf at all")
    )
    client = httpx.Client(transport=transport, follow_redirects=True)
    monkeypatch.setattr(scraper, "get_file_hash", lambda url: None)

    res = scraper._process_one_file(
        client, "https://kart.edu.ua/weird.pdf", force=False
    )
    client.close()
    assert res.error is None
    assert res.skipped is True
    assert res.lessons is None


def test_process_one_file_skips_unchanged_file(monkeypatch):
    """When the hash matches the stored hash and force=False, the file is skipped."""
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, content=_MINIMAL_PDF)
    )
    client = httpx.Client(transport=transport, follow_redirects=True)
    monkeypatch.setattr(scraper, "get_file_hash", lambda url: scraper.sha256_bytes(_MINIMAL_PDF))

    res = scraper._process_one_file(
        client, "https://kart.edu.ua/x.pdf", force=False
    )
    client.close()
    assert res.skipped is True
    assert res.lessons is None

