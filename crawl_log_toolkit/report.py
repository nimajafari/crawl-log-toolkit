"""Render the analysis pack as a single self-contained HTML report.

No templating engine, no JavaScript, no CDN — just Python string building with
inline CSS and inline-SVG charts, so the output is one file that opens offline
with a double-click. :func:`render_html` takes the same ``(name, rows)`` results
``analyze`` already produces and turns them into summary cards, per-metric
sections (each annotated with what *healthy* looks like), and a few bar charts.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from datetime import datetime

from .analyze import load_query, query_doc, query_requirements

__all__ = ["render_html"]

# A muted, print-friendly palette. Accent greens/ambers flag healthy vs watch.
_CSS = """
:root {
  --bg: #f6f7f9; --card: #ffffff; --ink: #1c2530; --muted: #5b6876;
  --line: #e6e9ee; --accent: #2f6f4f; --accent-soft: #e8f2ec;
  --warn: #9a6a12; --warn-soft: #fbf2df; --bar: #5b8def; --bar-soft: #eef3fd;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
}
.wrap { max-width: 960px; margin: 0 auto; padding: 0 20px 64px; }
header.hero {
  background: linear-gradient(135deg, #1f2a37, #2f6f4f);
  color: #fff; padding: 36px 0 30px; margin-bottom: 28px;
}
header.hero .wrap { padding-bottom: 0; }
header.hero h1 { margin: 0 0 6px; font-size: 26px; letter-spacing: -0.2px; }
header.hero .sub { opacity: 0.85; font-size: 13.5px; }
header.hero .sub code { background: rgba(255,255,255,0.15); padding: 1px 6px; border-radius: 5px; }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 14px; margin: 0 0 30px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 16px 18px; }
.card .label { font-size: 12px; text-transform: uppercase; letter-spacing: 0.4px; color: var(--muted); }
.card .value { font-size: 27px; font-weight: 650; margin-top: 4px; letter-spacing: -0.5px; }
.card .value.warn { color: var(--warn); }
.card .value.good { color: var(--accent); }
.card .note { font-size: 12px; color: var(--muted); margin-top: 2px; }
section.metric { background: var(--card); border: 1px solid var(--line); border-radius: 12px; padding: 20px 22px; margin-bottom: 18px; }
section.metric h2 { margin: 0 0 2px; font-size: 18px; letter-spacing: -0.2px; }
section.metric .answers { color: var(--muted); font-size: 13.5px; margin: 0 0 14px; }
.healthy { display: inline-block; font-size: 12.5px; background: var(--accent-soft); color: var(--accent);
  border-radius: 999px; padding: 3px 11px; margin: 0 0 14px; }
.healthy b { font-weight: 650; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th, td { text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase; letter-spacing: 0.3px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:nth-child(even) { background: #fafbfc; }
td.wrap-cell { white-space: normal; word-break: break-all; max-width: 460px; }
.muted { color: var(--muted); }
.empty { color: var(--muted); font-style: italic; font-size: 13.5px; }
.notice { background: var(--warn-soft); border: 1px solid #f0e2c2; color: var(--warn);
  border-radius: 10px; padding: 11px 14px; font-size: 13.5px; }
.notice code { background: rgba(154,106,18,0.13); padding: 1px 5px; border-radius: 4px; }
.chart { margin: 6px 0 16px; }
.chart svg { display: block; width: 100%; height: auto; }
footer { color: var(--muted); font-size: 12.5px; text-align: center; margin-top: 30px; }
footer code { background: #eef0f3; padding: 1px 5px; border-radius: 4px; }
"""


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _fmt(value: object) -> str:
    """Human-format a cell: thousands separators for ints, 2dp for floats."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.2f}".rstrip("0").rstrip(".") if value % 1 else f"{int(value):,}"
    return _esc(value)


def _rows_by_name(results: Iterable[tuple[str, list[dict]]]) -> dict[str, list[dict]]:
    return {name: rows for name, rows in results}


def _first(rows: list[dict], key: str):
    return rows[0][key] if rows and key in rows[0] else None


# --------------------------------------------------------------------------- #
# building blocks
# --------------------------------------------------------------------------- #


def _table(rows: list[dict]) -> str:
    if not rows:
        return '<p class="empty">No rows.</p>'
    cols = list(rows[0].keys())
    numeric = {c: all(_is_number(r.get(c)) or r.get(c) is None for r in rows) for c in cols}
    # Long free-text columns (user agents, paths) wrap instead of overflowing.
    wrapcol = {
        c: any(isinstance(r.get(c), str) and len(r.get(c, "")) > 60 for r in rows) for c in cols
    }
    head = "".join(f'<th class="{"num" if numeric[c] else ""}">{_esc(c)}</th>' for c in cols)
    body = []
    for r in rows:
        cells = []
        for c in cols:
            cls = "num" if numeric[c] else ("wrap-cell" if wrapcol[c] else "")
            cells.append(f'<td class="{cls}">{_fmt(r.get(c))}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table>"


def _svg_hbars(pairs: Sequence[tuple[str, float]], *, accent: str = "#5b8def") -> str:
    """A horizontal bar chart as inline SVG (no JS). ``pairs`` is (label, value)."""
    pairs = [(str(lbl), float(val or 0)) for lbl, val in pairs if val is not None]
    if not pairs:
        return ""
    maxv = max((v for _, v in pairs), default=0) or 1
    bar_h, gap, width, label_w, val_w = 22, 9, 720, 200, 78
    track_w = width - label_w - val_w
    height = len(pairs) * (bar_h + gap) + gap
    out = [f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="bar chart">']
    y = gap
    for label, val in pairs:
        w = max(2.0, track_w * (val / maxv))
        short = label if len(label) <= 30 else label[:29] + "…"
        out.append(
            f'<text x="0" y="{y + bar_h - 6}" font-size="12.5" fill="#5b6876">{_esc(short)}</text>'
            f'<rect x="{label_w}" y="{y}" width="{track_w}" height="{bar_h}" rx="4" fill="#eef3fd"/>'
            f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="4" fill="{accent}"/>'
            f'<text x="{label_w + track_w + 8}" y="{y + bar_h - 6}" font-size="12.5" '
            f'fill="#1c2530" font-weight="600">{_fmt(int(val) if float(val).is_integer() else val)}</text>'
        )
        y += bar_h + gap
    out.append("</svg>")
    return f'<div class="chart">{"".join(out)}</div>'


# --------------------------------------------------------------------------- #
# summary cards + headline charts (defensive: skip whatever data is absent)
# --------------------------------------------------------------------------- #


def _card(label: str, value: str, *, note: str = "", tone: str = "") -> str:
    cls = f"value {tone}".strip()
    note_html = f'<div class="note">{_esc(note)}</div>' if note else ""
    return f'<div class="card"><div class="label">{_esc(label)}</div><div class="{cls}">{_esc(value)}</div>{note_html}</div>'


def _summary_cards(by: dict[str, list[dict]]) -> str:
    cards: list[str] = []

    total = _first(by.get("crawl_waste_pct", []), "total_crawls")
    if total is None:
        total = _first(by.get("parameter_proliferation", []), "total_crawls")
    if total is not None:
        cards.append(_card("Verified crawls", _fmt(total)))

    waste = _first(by.get("crawl_waste_pct", []), "crawl_waste_pct")
    if waste is not None:
        tone = "good" if waste < 5 else "warn"
        cards.append(
            _card("Crawl waste", f"{waste:.1f}%", note="low single digits is healthy", tone=tone)
        )

    rd = by.get("redirect_404_waste", [])
    rdw = _first(rd, "waste_pct")
    if rdw is not None:
        tone = "good" if rdw < 5 else "warn"
        cards.append(_card("Redirect + 404 waste", f"{rdw:.1f}%", tone=tone))

    param = _first(by.get("parameter_proliferation", []), "parameterized_pct")
    if param is not None:
        tone = "good" if param < 10 else "warn"
        cards.append(_card("Parameterized URLs", f"{param:.1f}%", tone=tone))

    spoof = by.get("spoofed_crawler_summary", [])
    if spoof is not None:
        reqs = sum(int(r.get("requests", 0) or 0) for r in spoof)
        tone = "good" if not spoof else "warn"
        cards.append(
            _card("Spoofers", _fmt(len(spoof)), note=f"{_fmt(reqs)} faked requests", tone=tone)
        )

    cats = by.get("bytes_served", [])
    if cats:
        top = max(cats, key=lambda r: int(r.get("crawls", 0) or 0))
        cards.append(
            _card(
                "Top crawler",
                _esc(top.get("crawler_category", "—")),
                note=f"{_fmt(top.get('crawls'))} crawls",
            )
        )

    return f'<div class="cards">{"".join(cards)}</div>' if cards else ""


def _date_range(by: dict[str, list[dict]]) -> str:
    days = [
        r.get("day") for r in by.get("status_distribution_by_day", []) if r.get("day") is not None
    ]
    if not days:
        return ""
    lo, hi = min(days), max(days)
    return f"{_esc(lo)} → {_esc(hi)}" if lo != hi else _esc(lo)


# --------------------------------------------------------------------------- #
# top-level
# --------------------------------------------------------------------------- #

# Queries we render as a chart in addition to (or instead of) a raw table.
_CHARTS = {
    "bytes_served": ("crawler_category", "crawls", "#2f6f4f"),
    "crawl_trap_detection": ("prefix", "crawls", "#5b8def"),
    "crawl_depth": ("depth", "crawls", "#5b8def"),
    "top_parameters": ("parameter", "occurrences", "#5b8def"),
}

# How to satisfy a query whose required relation wasn't supplied. Keyed by the
# relation name a query declares via `-- requires: <name>`.
_REQUIREMENT_HELP = {
    "publications": (
        "This metric needs a <b>publication log</b> to measure publish→first-crawl "
        "latency. Re-run with <code>--publication-log pubs.csv</code> — a CSV of "
        "<code>url,published_at</code> whose <code>url</code> matches the normalized path."
    ),
}


def _empty_reason(name: str, sql: str) -> str:
    """Explain *why* a metric produced no rows, instead of a bare 'No rows'.

    A query with an unmet ``-- requires:`` relation is empty because that input
    wasn't provided (e.g. ``first_crawl_latency`` without a publication log), not
    because the data lacks it — say so, and how to fix it.
    """
    for req in query_requirements(sql):
        help_text = _REQUIREMENT_HELP.get(
            req,
            f"This metric requires the <code>{_esc(req)}</code> input, which wasn't provided.",
        )
        return f'<p class="notice">{help_text}</p>'
    # No unmet requirement: genuinely nothing matched. For some metrics that's
    # good news (e.g. no spoofers), so keep it neutral rather than alarming.
    return '<p class="empty">No rows — nothing matched in this dataset.</p>'


def render_html(
    results: Iterable[tuple[str, list[dict]]],
    *,
    source: str,
    generated_at: datetime,
    version: str = "",
) -> str:
    """Render ``(name, rows)`` analysis results as a standalone HTML document."""
    results = list(results)
    by = _rows_by_name(results)

    sections: list[str] = []
    for name, rows in results:
        sql = load_query(name)
        doc = query_doc(sql)
        title = doc["title"] or name.replace("_", " ").title()
        answers = f'<p class="answers">{_esc(doc["answers"])}</p>' if doc["answers"] else ""
        healthy = (
            f'<div class="healthy"><b>Healthy:</b> {_esc(doc["healthy"])}</div>'
            if doc["healthy"]
            else ""
        )
        chart = ""
        if name in _CHARTS and rows:
            lk, vk, color = _CHARTS[name]
            if lk in rows[0] and vk in rows[0]:
                chart = _svg_hbars([(r.get(lk), r.get(vk)) for r in rows[:10]], accent=color)
        body = _table(rows) if rows else _empty_reason(name, sql)
        sections.append(
            f'<section class="metric" id="{_esc(name)}">'
            f"<h2>{_esc(title)}</h2>{answers}{healthy}{chart}{body}</section>"
        )

    drange = _date_range(by)
    gen = generated_at.strftime("%Y-%m-%d %H:%M")
    ver = f" v{_esc(version)}" if version else ""
    sub_bits = [f"Source: <code>{_esc(source)}</code>"]
    if drange:
        sub_bits.append(f"Window: {drange}")
    sub_bits.append(f"Generated {_esc(gen)}")

    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Crawl-budget report</title>"
        f"<style>{_CSS}</style></head><body>"
        '<header class="hero"><div class="wrap">'
        "<h1>Verified crawl-budget report</h1>"
        f'<div class="sub">{" &nbsp;·&nbsp; ".join(sub_bits)}</div>'
        "</div></header>"
        '<div class="wrap">'
        f"{_summary_cards(by)}"
        f"{''.join(sections)}"
        f"<footer>Generated by <code>crawl-log-toolkit{ver}</code> — "
        "verified by IP, never by user-agent.</footer>"
        "</div></body></html>"
    )
