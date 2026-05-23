#!/usr/bin/env python3
"""Generate the synthetic sample dataset and its expected-analytics fixtures.

The output runs the whole pipeline out of the box and drives the end-to-end CI
test. Everything here is fully deterministic (no RNG), and the expected values
in ``sample-data/expected/`` are computed from independent counters as the log
is built — NOT by running the pipeline — so the e2e test is a genuine check that
``parse -> enrich -> analyze`` reproduces the planted ground truth.

Planted patterns (all the brief asks for):
  * verified Googlebot / Bingbot / special-crawler / user-triggered crawls,
  * SPOOFED Googlebot (Googlebot UA from non-Google IPs) -> must be rejected,
  * a parameter-proliferation cluster on /products,
  * a 404 spike,
  * a crawl trap (/calendar/... with huge URL cardinality),
  * ordinary human browser traffic (also unverified).

Format: gzipped newline-delimited JSON in the nginx ``escape=json`` shape that
``log-formats/nginx-seo.conf`` emits and ``crawl_log_toolkit.parse`` reads.

Run:  python scripts/generate_sample_data.py
"""

from __future__ import annotations

import gzip
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = ROOT / "sample-data"
EXPECTED_DIR = SAMPLE_DIR / "expected"

# --- identities (IPs must live in crawl_log_toolkit/data/ipranges snapshots) --
GOOGLEBOT_IPS = ["66.249.66.1", "66.249.66.2", "66.249.64.5"]
BINGBOT_IPS = ["157.55.39.10", "40.77.167.5"]
SPECIAL_IP = "34.100.182.100"
USER_TRIGGERED_IP = "35.187.132.5"
SPOOFER_IPS = ["45.83.64.10", "185.220.101.5", "91.200.12.7"]  # NOT in any range
HUMAN_IPS = ["203.0.113.10", "198.51.100.22", "192.0.2.44"]  # TEST-NET, not crawlers

GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
BINGBOT_UA = "Mozilla/5.0 (compatible; bingbot/2.0; +http://www.bing.com/bingbot.htm)"
INSPECTION_UA = "Mozilla/5.0 (compatible; Google-InspectionTool/1.0)"
SITEVERIFY_UA = "Mozilla/5.0 (compatible; Google-Site-Verification/1.0)"
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

HOST = "shop.example.com"
BASE = datetime(2026, 5, 18, 0, 0, 0, tzinfo=UTC)


class Builder:
    """Accumulates log records and the ground-truth counters in lockstep."""

    def __init__(self) -> None:
        self.records: list[dict] = []
        self._i = 0
        # ground-truth counters
        self.total = 0
        self.verified = 0
        self.by_category: dict[str, int] = {}
        self.status_counts: dict[int, int] = {}
        self.parameterized = 0
        self.param_counts: dict[str, int] = {}
        self.calendar_paths: set[str] = set()
        self.calendar_crawls = 0
        self.request_time_samples = 0  # verified rows with a request_time

    def _ts(self) -> str:
        ts = (BASE + timedelta(seconds=self._i * 600)).isoformat()
        self._i += 1
        return ts

    def add(
        self,
        ip: str,
        uri: str,
        status: int,
        ua: str,
        *,
        category: str | None,
        bytes_sent: int = 1024,
        request_time: float | None = 0.08,
    ) -> None:
        """Add one line. ``category`` is the *ground-truth* class (None = unverified)."""
        self.records.append(
            {
                "time_iso8601": self._ts(),
                "remote_addr": ip,
                "request_method": "GET",
                "host": HOST,
                "request_uri": uri,
                "status": str(status),
                "body_bytes_sent": str(bytes_sent),
                "http_referer": "-",
                "http_user_agent": ua,
                "request_time": "" if request_time is None else f"{request_time:.3f}",
            }
        )
        self.total += 1
        verified = category is not None
        if verified:
            self.verified += 1
            self.by_category[category] = self.by_category.get(category, 0) + 1
            self.status_counts[status] = self.status_counts.get(status, 0) + 1
            if request_time is not None:
                self.request_time_samples += 1
            query = uri.split("?", 1)[1] if "?" in uri else ""
            if query:
                self.parameterized += 1
                for kv in query.split("&"):
                    key = kv.split("=", 1)[0]
                    self.param_counts[key] = self.param_counts.get(key, 0) + 1
            path = uri.split("?", 1)[0]
            if path.startswith("/calendar/"):
                self.calendar_paths.add(path)
                self.calendar_crawls += 1


def build() -> Builder:
    b = Builder()
    g = lambda i: GOOGLEBOT_IPS[i % len(GOOGLEBOT_IPS)]  # noqa: E731

    # A. 30 normal product-page crawls (Googlebot, 200), no query string.
    for i in range(1, 31):
        b.add(
            g(i),
            f"/products/item-{i}.html",
            200,
            GOOGLEBOT_UA,
            category="googlebot",
            bytes_sent=4096,
            request_time=0.05 + (i % 7) * 0.01,
        )

    # B. 60 parameter-proliferation crawls on /products (Googlebot, 200).
    #    color in all 60; size in 40; sort in 20.
    colors, sizes, sorts = ["red", "blue", "green", "black"], ["s", "m", "l"], ["price", "name"]
    for i in range(60):
        params = [f"color={colors[i % len(colors)]}"]
        if i < 40:
            params.append(f"size={sizes[i % len(sizes)]}")
        if i < 20:
            params.append(f"sort={sorts[i % len(sorts)]}")
        b.add(
            g(i),
            "/products?" + "&".join(params),
            200,
            GOOGLEBOT_UA,
            category="googlebot",
            bytes_sent=8192,
            request_time=0.12 + (i % 9) * 0.01,
        )

    # C. 25-request 404 spike on removed pages (Googlebot).
    for i in range(1, 26):
        b.add(
            g(i),
            f"/removed/page-{i}.html",
            404,
            GOOGLEBOT_UA,
            category="googlebot",
            bytes_sent=512,
            request_time=0.03,
        )

    # D. 10 redirect crawls (Googlebot, 301).
    for i in range(1, 11):
        b.add(
            g(i),
            f"/redirect/{i}",
            301,
            GOOGLEBOT_UA,
            category="googlebot",
            bytes_sent=256,
            request_time=0.02,
        )

    # E. 200 crawl-trap hits on a (near-)infinite calendar -> 200 DISTINCT URLs.
    made = 0
    for month in range(1, 13):
        for day in range(1, 29):
            if made >= 200:
                break
            b.add(
                g(made),
                f"/calendar/2026/{month:02d}/{day:02d}",
                200,
                GOOGLEBOT_UA,
                category="googlebot",
                bytes_sent=2048,
                request_time=0.09,
            )
            made += 1
        if made >= 200:
            break

    # F. 15 Bingbot crawls (200).
    for i in range(1, 16):
        b.add(
            BINGBOT_IPS[i % len(BINGBOT_IPS)],
            f"/products/item-{i}.html",
            200,
            BINGBOT_UA,
            category="bingbot",
            bytes_sent=4096,
            request_time=0.07,
        )

    # G. 5 special-crawler (Google-InspectionTool) crawls.
    for i in range(1, 6):
        b.add(
            SPECIAL_IP,
            f"/products/item-{i}.html",
            200,
            INSPECTION_UA,
            category="special-crawler",
            bytes_sent=4096,
            request_time=0.06,
        )

    # H. 5 user-triggered fetcher hits (Site Verifier).
    for i in range(1, 6):
        b.add(
            USER_TRIGGERED_IP,
            f"/verify/{i}",
            200,
            SITEVERIFY_UA,
            category="user-triggered",
            bytes_sent=128,
            request_time=0.04,
        )

    # I. 40 SPOOFED Googlebot (Googlebot UA, non-Google IPs) -> must be rejected.
    for i in range(40):
        b.add(
            SPOOFER_IPS[i % len(SPOOFER_IPS)],
            f"/products/item-{(i % 30) + 1}.html",
            200,
            GOOGLEBOT_UA,
            category=None,
            bytes_sent=4096,
        )

    # J. 50 ordinary human browser requests (also unverified).
    human_paths = ["/", "/products/item-1.html", "/cart", "/search?q=shoes", "/account"]
    for i in range(50):
        b.add(
            HUMAN_IPS[i % len(HUMAN_IPS)],
            human_paths[i % len(human_paths)],
            200 if i % 10 else 304,
            BROWSER_UA,
            category=None,
            bytes_sent=20480,
        )

    return b


def write_outputs(b: Builder) -> None:
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
    EXPECTED_DIR.mkdir(parents=True, exist_ok=True)

    # 1. the gzipped log. Write with mtime=0 so the bytes are reproducible
    #    (gzip otherwise stamps the current time into the header).
    log_path = SAMPLE_DIR / "access.log.gz"
    body = "".join(json.dumps(rec) + "\n" for rec in b.records).encode("utf-8")
    with open(log_path, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        gz.write(body)

    # 2. publication log for first-crawl-latency (paths present in verified crawls)
    pub_path = SAMPLE_DIR / "publication_log.csv"
    pub_published = "2026-05-17T12:00:00"
    pub_rows = [(f"/products/item-{i}.html", pub_published) for i in range(1, 11)]
    lines = ["url,published_at"] + [f"{u},{t}" for u, t in pub_rows]
    pub_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # 3. expected analytics (ground truth from counters)
    parameterized_pct = round(100.0 * b.parameterized / b.verified, 2)
    expected = {
        "verification_summary.json": {
            "total": b.total,
            "verified": b.verified,
            "unverified": b.total - b.verified,
            "by_category": dict(sorted(b.by_category.items())),
        },
        "status_distribution.json": {str(k): v for k, v in sorted(b.status_counts.items())},
        "parameter_proliferation.json": {
            "total_crawls": b.verified,
            "parameterized_crawls": b.parameterized,
            "parameterized_pct": parameterized_pct,
        },
        "top_parameters.json": dict(sorted(b.param_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "crawl_trap.json": {
            "prefix": "/calendar",
            "distinct_urls": len(b.calendar_paths),
            "crawls": b.calendar_crawls,
        },
        "response_time.json": {"total_samples": b.request_time_samples},
        "publications.json": {"rows": len(pub_rows)},
    }
    for name, payload in expected.items():
        (EXPECTED_DIR / name).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    print(f"wrote {log_path} ({len(b.records)} lines)")
    print(f"wrote {pub_path} ({len(pub_rows)} rows)")
    for name in expected:
        print(f"wrote {EXPECTED_DIR / name}")


if __name__ == "__main__":
    write_outputs(build())
