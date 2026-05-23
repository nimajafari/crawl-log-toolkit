"""Tests for the enrich stage: verification columns, ZSTD Parquet, anonymization."""

from __future__ import annotations

import duckdb
import pytest

from crawl_log_toolkit import SCHEMA_FIELDS
from crawl_log_toolkit.enrich import anonymize_ip, enrich_records
from crawl_log_toolkit.verify import CrawlerVerifier


def _rec(ip, uri, status=200, request_time=0.1):
    base = dict.fromkeys(SCHEMA_FIELDS)
    base.update(
        timestamp="2026-05-18T10:00:00+00:00",
        remote_addr=ip,
        method="GET",
        host="ex.com",
        uri=uri,
        path=uri.split("?", 1)[0],
        status=status,
        bytes_sent=100,
        request_time=request_time,
    )
    return base


@pytest.fixture
def verifier():
    return CrawlerVerifier(allow_network=False).load()


def test_enrich_adds_verification_columns(tmp_path, verifier):
    out = tmp_path / "out.parquet"
    records = [
        _rec("66.249.66.1", "/a?x=1"),  # googlebot
        _rec("157.55.39.10", "/b"),  # bingbot
        _rec("45.83.64.10", "/c"),  # spoofer -> unverified
    ]
    stats = enrich_records(records, out, verifier=verifier)
    assert stats == {
        "total": 3,
        "verified": 2,
        "unverified": 1,
        "by_category": {"googlebot": 1, "bingbot": 1},
        "output": str(out),
    }
    con = duckdb.connect()
    rows = con.execute(
        f"SELECT remote_addr, path, crawler_category, verified "
        f"FROM read_parquet('{out}') ORDER BY remote_addr"
    ).fetchall()
    assert rows == [
        ("157.55.39.10", "/b", "bingbot", True),
        ("45.83.64.10", "/c", None, False),
        ("66.249.66.1", "/a", "googlebot", True),  # path query stripped
    ]


def test_enriched_parquet_is_zstd(tmp_path, verifier):
    out = tmp_path / "z.parquet"
    enrich_records([_rec("66.249.66.1", "/a")], out, verifier=verifier)
    con = duckdb.connect()
    comps = {
        r[0]
        for r in con.execute(
            f"SELECT DISTINCT compression FROM parquet_metadata('{out}')"
        ).fetchall()
    }
    assert comps == {"ZSTD"}


def test_timestamp_is_real_timestamp_type(tmp_path, verifier):
    out = tmp_path / "t.parquet"
    enrich_records([_rec("66.249.66.1", "/a")], out, verifier=verifier)
    con = duckdb.connect()
    coltype = con.execute(
        f"SELECT column_type FROM (DESCRIBE SELECT * FROM read_parquet('{out}')) "
        f"WHERE column_name = 'timestamp'"
    ).fetchone()[0]
    assert coltype == "TIMESTAMP"


def test_anonymize_after_verification(tmp_path, verifier):
    out = tmp_path / "anon.parquet"
    enrich_records([_rec("66.249.66.1", "/a")], out, verifier=verifier, anonymize_ips=True)
    con = duckdb.connect()
    ip, cat = con.execute(
        f"SELECT remote_addr, crawler_category FROM read_parquet('{out}')"
    ).fetchone()
    # Still classified (verification ran on the true IP), but stored masked.
    assert cat == "googlebot"
    assert ip == "66.249.66.0"


@pytest.mark.parametrize(
    "ip,masked",
    [
        ("66.249.66.1", "66.249.66.0"),
        ("203.0.113.255", "203.0.113.0"),
        ("2001:4860:4801:10::abcd", "2001:4860:4801::"),
        (None, None),
        ("garbage", "garbage"),
    ],
)
def test_anonymize_ip(ip, masked):
    assert anonymize_ip(ip) == masked
