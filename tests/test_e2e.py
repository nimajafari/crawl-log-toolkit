"""End-to-end: run parse -> enrich -> analyze on the bundled sample data and
assert the analytics equal the ground-truth fixtures in sample-data/expected/.

No network is used (the vendored IP-range snapshot classifies the IPs). This is
the test the brief's Definition of Done hangs on.
"""

from __future__ import annotations

import duckdb
import pytest

from crawl_log_toolkit.analyze import list_queries, run_all, run_query
from tests.helpers import PUBLICATION_LOG, load_expected

# --------------------------------------------------------------------------- #
# verification (IP-based) — spoofed Googlebot must be rejected
# --------------------------------------------------------------------------- #


def test_verification_summary_matches_expected(enriched_parquet):
    expected = load_expected("verification_summary")
    con = duckdb.connect()
    con.execute(f"CREATE VIEW logs AS SELECT * FROM read_parquet('{enriched_parquet}')")
    total = con.execute("SELECT COUNT(*) FROM logs").fetchone()[0]
    verified = con.execute("SELECT COUNT(*) FROM logs WHERE verified").fetchone()[0]
    by_cat = dict(
        con.execute(
            "SELECT crawler_category, COUNT(*) FROM logs WHERE verified GROUP BY crawler_category"
        ).fetchall()
    )
    assert total == expected["total"]
    assert verified == expected["verified"]
    assert total - verified == expected["unverified"]
    assert by_cat == expected["by_category"]


def test_spoofed_googlebot_rejected(enriched_parquet):
    """40 planted spoofed-Googlebot lines (UA says Googlebot, IP isn't Google's)
    must all be unverified, and none must leak into the verified set."""
    rows = run_query("spoofed_crawler_summary", enriched_parquet)
    assert sum(r["requests"] for r in rows) == 40
    con = duckdb.connect()
    leaked = con.execute(
        f"SELECT COUNT(*) FROM read_parquet('{enriched_parquet}') "
        "WHERE verified AND user_agent ILIKE '%googlebot%' "
        "AND remote_addr NOT LIKE '66.249.%'"
    ).fetchone()[0]
    assert leaked == 0


# --------------------------------------------------------------------------- #
# the analytical SQL pack
# --------------------------------------------------------------------------- #


def test_parameter_proliferation_nullif_fix(enriched_parquet):
    expected = load_expected("parameter_proliferation")
    rows = run_query("parameter_proliferation", enriched_parquet)
    assert len(rows) == 1
    row = rows[0]
    assert row["total_crawls"] == expected["total_crawls"]
    assert row["parameterized_crawls"] == expected["parameterized_crawls"]
    # The NULLIF fix: this is ~17%, NOT ~100% (which the empty-string bug gives).
    assert float(row["parameterized_pct"]) == pytest.approx(expected["parameterized_pct"])
    assert row["parameterized_crawls"] < row["total_crawls"]


def test_top_parameters(enriched_parquet):
    expected = load_expected("top_parameters")
    rows = run_query("top_parameters", enriched_parquet)
    got = {r["parameter"]: r["occurrences"] for r in rows}
    assert got == expected


def test_crawl_trap_detected(enriched_parquet):
    expected = load_expected("crawl_trap")
    rows = run_query("crawl_trap_detection", enriched_parquet)
    traps = {r["prefix"]: r for r in rows}
    assert expected["prefix"] in traps
    trap = traps[expected["prefix"]]
    assert trap["distinct_urls"] == expected["distinct_urls"]
    assert trap["crawls"] == expected["crawls"]


def test_status_distribution(enriched_parquet):
    expected = load_expected("status_distribution")
    rows = run_query("status_distribution_by_day", enriched_parquet)
    totals: dict[str, int] = {}
    for r in rows:
        totals[str(r["status"])] = totals.get(str(r["status"]), 0) + r["crawls"]
    assert totals == expected


def test_response_time_sample_count(enriched_parquet):
    expected = load_expected("response_time")
    rows = run_query("response_time_percentiles", enriched_parquet)
    assert sum(r["samples"] for r in rows) == expected["total_samples"]
    # percentiles must be present and ordered for the busiest category
    g = next(r for r in rows if r["crawler_category"] == "googlebot")
    assert g["p50_seconds"] <= g["p95_seconds"] <= g["p99_seconds"]


def test_redirect_404_waste(enriched_parquet):
    rows = run_query("redirect_404_waste", enriched_parquet)
    row = rows[0]
    assert row["redirects"] == 10
    assert row["not_found"] == 25
    assert row["server_errors"] == 0


def test_first_crawl_latency(enriched_parquet):
    expected = load_expected("publications")
    rows = run_query("first_crawl_latency", enriched_parquet, publication_log=PUBLICATION_LOG)
    assert len(rows) == expected["rows"]
    for r in rows:
        assert r["latency_hours"] is not None
        assert r["latency_hours"] >= 0


def test_first_crawl_latency_skipped_without_publications(enriched_parquet):
    # The query declares `-- requires: publications`; absent it, it is skipped.
    assert run_query("first_crawl_latency", enriched_parquet) == []


def test_run_all_executes_every_query(enriched_parquet):
    names = list_queries()
    assert len(names) >= 10
    ran = dict(run_all(enriched_parquet, publication_log=PUBLICATION_LOG))
    assert set(ran) == set(names)
    # Every query returns at least one row given the sample data + publications.
    for name, rows in ran.items():
        assert rows, f"query {name} returned no rows"
