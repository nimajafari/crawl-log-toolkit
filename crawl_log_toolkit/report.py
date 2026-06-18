"""Render the analysis pack as a single self-contained HTML report.

No templating engine and nothing loaded from the network — just Python string
building with inline CSS, inline-SVG charts, and one tiny inline script (a
light/dark theme toggle). The output is one file that opens offline with a
double-click. :func:`render_html` takes the same ``(name, rows)`` results
``analyze`` already produces and turns them into summary cards, per-metric
sections (each annotated with what *healthy* looks like), and a few bar charts.
Light/dark follows the OS preference and can be flipped with the in-page toggle.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence
from datetime import datetime

from .analyze import load_query, query_doc, query_requirements

__all__ = ["render_html"]

# Themeable palette. Light is the default; dark applies when the OS prefers it
# (unless the reader forced light) or when the in-page toggle sets data-theme.
# Chart fills are CSS-driven (see the .bar* classes) so SVG follows the theme too.
_CSS = """
:root {
  --bg: #f4f6f8; --card: #ffffff; --ink: #1b2430; --muted: #5b6876;
  --line: #e7eaef; --row: #fafbfc; --shadow: 0 1px 2px rgba(16,24,40,.05), 0 1px 3px rgba(16,24,40,.04);
  --hero1: #1f2a37; --hero2: #2f6f4f; --on-hero: #ffffff;
  --good: #2f6f4f; --good-soft: #e8f2ec;
  --warn: #9a6a12; --warn-soft: #fbf2df; --warn-line: #f0e2c2;
  --bar-track: #eef3fd; --bar-green: #2f6f4f; --bar-blue: #5b8def; --bar-amber: #c1873a;
  --chip: #eef0f3;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #0f141b; --card: #161d27; --ink: #e7ecf2; --muted: #97a3b2;
    --line: #243040; --row: #1a222d; --shadow: 0 1px 2px rgba(0,0,0,.4);
    --hero1: #11202b; --hero2: #1d5a40; --on-hero: #eaf2ee;
    --good: #6cc59a; --good-soft: #16312663;
    --warn: #e0b766; --warn-soft: #2e260f7a; --warn-line: #4a3c19;
    --bar-track: #223040; --bar-green: #3f9b6d; --bar-blue: #6f9ef0; --bar-amber: #d39a4e;
    --chip: #223040;
  }
}
:root[data-theme="dark"] {
  --bg: #0f141b; --card: #161d27; --ink: #e7ecf2; --muted: #97a3b2;
  --line: #243040; --row: #1a222d; --shadow: 0 1px 2px rgba(0,0,0,.4);
  --hero1: #11202b; --hero2: #1d5a40; --on-hero: #eaf2ee;
  --good: #6cc59a; --good-soft: #16312663;
  --warn: #e0b766; --warn-soft: #2e260f7a; --warn-line: #4a3c19;
  --bar-track: #223040; --bar-green: #3f9b6d; --bar-blue: #6f9ef0; --bar-amber: #d39a4e;
  --chip: #223040;
}
* { box-sizing: border-box; }
html { color-scheme: light dark; }
body {
  margin: 0; background: var(--bg); color: var(--ink); -webkit-font-smoothing: antialiased;
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  transition: background .2s ease, color .2s ease;
}
.wrap { max-width: 980px; margin: 0 auto; padding: 0 22px 64px; }
header.hero {
  background: linear-gradient(135deg, var(--hero1), var(--hero2));
  color: var(--on-hero); padding: 34px 0 30px; margin-bottom: 28px;
}
header.hero .wrap { padding-bottom: 0; }
header.hero .top { display: flex; align-items: start; justify-content: space-between; gap: 16px; }
header.hero h1 { margin: 0 0 6px; font-size: 26px; letter-spacing: -0.3px; }
header.hero .sub { opacity: .9; font-size: 13.5px; }
header.hero .sub code { background: rgba(255,255,255,.16); padding: 1px 6px; border-radius: 5px; }
.toggle {
  flex: none; cursor: pointer; border: 1px solid rgba(255,255,255,.28); background: rgba(255,255,255,.1);
  color: var(--on-hero); border-radius: 999px; padding: 7px 13px; font-size: 13px; font-weight: 600;
  display: inline-flex; align-items: center; gap: 7px; transition: background .15s ease;
}
.toggle:hover { background: rgba(255,255,255,.2); }
.toggle .ico-dark { display: none; }
:root[data-theme="dark"] .toggle .ico-dark, html:not([data-theme="light"]) .toggle .ico-dark { }
.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(168px, 1fr)); gap: 14px; margin: 0 0 30px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px 18px; box-shadow: var(--shadow); }
.card .label { font-size: 11.5px; text-transform: uppercase; letter-spacing: .5px; color: var(--muted); }
.card .value { font-size: 28px; font-weight: 660; margin-top: 5px; letter-spacing: -0.6px; }
.card .value.warn { color: var(--warn); }
.card .value.good { color: var(--good); }
.card .note { font-size: 12px; color: var(--muted); margin-top: 3px; }
section.metric { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 20px 22px; margin-bottom: 18px; box-shadow: var(--shadow); }
section.metric h2 { margin: 0 0 3px; font-size: 18px; letter-spacing: -0.2px; }
section.metric .answers { color: var(--muted); font-size: 13.5px; margin: 0 0 14px; }
.healthy { display: inline-block; font-size: 12.5px; background: var(--good-soft); color: var(--good);
  border-radius: 999px; padding: 3px 11px; margin: 0 0 14px; }
.healthy b { font-weight: 650; }
.table-scroll { overflow-x: auto; border-radius: 10px; }
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th, td { text-align: left; padding: 8px 11px; border-bottom: 1px solid var(--line); white-space: nowrap; }
th { color: var(--muted); font-weight: 600; font-size: 11.5px; text-transform: uppercase; letter-spacing: .3px; position: sticky; top: 0; background: var(--card); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:nth-child(even) { background: var(--row); }
tbody tr:hover { background: var(--good-soft); }
td.wrap-cell { white-space: normal; word-break: break-all; max-width: 460px; }
.muted { color: var(--muted); }
.empty { color: var(--muted); font-style: italic; font-size: 13.5px; }
.notice { background: var(--warn-soft); border: 1px solid var(--warn-line); color: var(--warn);
  border-radius: 10px; padding: 11px 14px; font-size: 13.5px; }
.notice code { background: rgba(154,106,18,.13); padding: 1px 5px; border-radius: 4px; }
.chart { margin: 6px 0 16px; }
.chart svg { display: block; width: 100%; height: auto; }
.bar-track { fill: var(--bar-track); }
.bar-label { fill: var(--muted); }
.bar-val { fill: var(--ink); font-weight: 600; }
.bar--green { fill: var(--bar-green); } .bar--blue { fill: var(--bar-blue); } .bar--amber { fill: var(--bar-amber); }
footer { color: var(--muted); font-size: 12.5px; text-align: center; margin-top: 30px; }
footer code { background: var(--chip); padding: 1px 5px; border-radius: 4px; }
"""

# Minimal inline theme toggle: persists choice in localStorage and falls back to
# the OS preference. Inline (no network/CDN) so the report stays self-contained.
_THEME_JS = """
(function () {
  var root = document.documentElement, KEY = "crawl-log-theme";
  try { var saved = localStorage.getItem(KEY); if (saved) root.setAttribute("data-theme", saved); } catch (e) {}
  function current() {
    var t = root.getAttribute("data-theme");
    if (t) return t;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  window.__toggleTheme = function () {
    var next = current() === "dark" ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem(KEY, next); } catch (e) {}
  };
})();
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
    return (
        '<div class="table-scroll"><table><thead><tr>'
        f"{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"
    )


def _svg_hbars(pairs: Sequence[tuple[str, float]], *, variant: str = "blue") -> str:
    """A horizontal bar chart as inline SVG. ``pairs`` is (label, value).

    Fills come from CSS classes (``bar--<variant>``, ``bar-track``, ``bar-label``,
    ``bar-val``) so the chart follows the light/dark theme rather than baking in
    colors. No JavaScript involved.
    """
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
        num = _fmt(int(val) if float(val).is_integer() else val)
        out.append(
            f'<text class="bar-label" x="0" y="{y + bar_h - 6}" font-size="12.5">{_esc(short)}</text>'
            f'<rect class="bar-track" x="{label_w}" y="{y}" width="{track_w}" height="{bar_h}" rx="4"/>'
            f'<rect class="bar--{variant}" x="{label_w}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="4"/>'
            f'<text class="bar-val" x="{label_w + track_w + 8}" y="{y + bar_h - 6}" font-size="12.5">{num}</text>'
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
# The third element is a CSS bar variant (green/blue/amber) so charts follow the theme.
_CHARTS = {
    "bytes_served": ("crawler_category", "crawls", "green"),
    "crawl_trap_detection": ("prefix", "crawls", "blue"),
    "crawl_depth": ("depth", "crawls", "blue"),
    "top_parameters": ("parameter", "occurrences", "blue"),
    "redirect_404_urls": ("path", "crawls", "amber"),
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
            lk, vk, variant = _CHARTS[name]
            if lk in rows[0] and vk in rows[0]:
                chart = _svg_hbars([(r.get(lk), r.get(vk)) for r in rows[:10]], variant=variant)
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

    toggle = (
        '<button class="toggle" type="button" onclick="__toggleTheme()" '
        'aria-label="Toggle light or dark theme" title="Toggle light / dark">'
        '<span aria-hidden="true">◐</span> Theme</button>'
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Crawl-budget report</title>"
        # Apply the saved/OS theme before paint to avoid a flash of the wrong one.
        f"<script>{_THEME_JS}</script>"
        f"<style>{_CSS}</style></head><body>"
        '<header class="hero"><div class="wrap">'
        f'<div class="top"><h1>Verified crawl-budget report</h1>{toggle}</div>'
        f'<div class="sub">{" &nbsp;·&nbsp; ".join(sub_bits)}</div>'
        "</div></header>"
        '<div class="wrap">'
        f"{_summary_cards(by)}"
        f"{''.join(sections)}"
        f"<footer>Generated by <code>crawl-log-toolkit{ver}</code> — "
        "verified by IP, never by user-agent.</footer>"
        "</div></body></html>"
    )
