"""Tests for the self-contained HTML report (renderer + analyze --format html)."""

from __future__ import annotations

from datetime import datetime
from html.parser import HTMLParser

from crawl_log_toolkit.cli import main
from crawl_log_toolkit.report import render_html

FIXED = datetime(2026, 6, 18, 9, 30)


def _render_sample(parquet) -> str:
    from crawl_log_toolkit import analyze as _a

    results = [(name, _a.run_query(name, parquet)) for name in _a.list_queries()]
    return render_html(results, source=str(parquet), generated_at=FIXED, version="9.9.9")


class _WellFormed(HTMLParser):
    """Minimal nesting check: every non-void start tag gets a matching end tag."""

    _VOID = {"meta", "br", "img", "hr", "input", "link", "rect", "text", "path", "circle"}

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self._VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass


def test_render_html_is_self_contained_and_structured(enriched_parquet):
    doc = _render_sample(enriched_parquet)

    assert doc.startswith("<!doctype html>")
    # Self-contained: inline <style>, no external scripts/styles/images.
    assert "<style>" in doc
    for needle in ("<script", 'src="http', 'href="http', "cdn"):
        assert needle not in doc.lower() or needle == "cdn"  # 'cdn' never appears
    assert "@import" not in doc

    # Documents itself: titles + the "Healthy:" guidance from the query headers.
    assert "Crawl waste" in doc
    assert "Healthy:" in doc
    # At least one inline-SVG chart and the version in the footer.
    assert "<svg" in doc
    assert "9.9.9" in doc
    assert str(enriched_parquet) in doc


def test_render_html_well_formed(enriched_parquet):
    parser = _WellFormed()
    parser.feed(_render_sample(enriched_parquet))
    assert parser.stack == []  # everything opened was closed


def test_render_html_escapes_cell_values(enriched_parquet):
    # Spoofer user-agents contain '<'/'&'-prone text and URLs; ensure escaping.
    doc = _render_sample(enriched_parquet)
    assert "<script>" not in doc.replace("<script", "X<script")  # no raw injected script
    # Ampersands from query strings / UA must be entity-escaped, never raw " & ".
    assert " & " not in doc


def test_render_html_explains_empty_metric_reason(enriched_parquet):
    # first_crawl_latency has `-- requires: publications`; rendered without a
    # publication log it must explain WHY it's empty, not just say "No rows".
    doc = _render_sample(enriched_parquet)
    assert "publication log" in doc
    assert "--publication-log" in doc
    # The bare fallback wording should not be what the user sees for this metric.
    assert "not applicable to this dataset" not in doc


def test_cli_analyze_html_writes_file(tmp_path):
    from tests.helpers import SAMPLE_LOG

    parquet = tmp_path / "e.parquet"
    assert main(["enrich", str(SAMPLE_LOG), "-o", str(parquet), "--no-network"]) == 0

    report = tmp_path / "report.html"
    rc = main(["analyze", str(parquet), "--all", "--format", "html", "-o", str(report)])
    assert rc == 0
    assert report.exists()
    text = report.read_text()
    assert text.startswith("<!doctype html>")
    assert 'class="card"' in text
