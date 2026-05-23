"""Unit tests for IP-based crawler verification (incl. caching/TTL with a mock)."""

from __future__ import annotations

import json
import time

import pytest

from crawl_log_toolkit.verify import CrawlerVerifier
from tests.helpers import FIXTURE_RANGES

# --- classification against the vendored snapshot (offline) ------------------


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("66.249.66.1", "googlebot"),
        ("66.249.64.5", "googlebot"),
        ("157.55.39.10", "bingbot"),
        ("40.77.167.5", "bingbot"),
        ("34.100.182.100", "special-crawler"),
        ("35.187.132.5", "user-triggered"),
        # spoofers / humans -> not verified
        ("45.83.64.10", None),
        ("185.220.101.5", None),
        ("203.0.113.10", None),
        ("192.0.2.44", None),
        ("not-an-ip", None),
    ],
)
def test_classify_known_ips(verifier, ip, expected):
    assert verifier.classify(ip) == expected
    assert verifier.is_verified(ip) is (expected is not None)


def test_ipv6_googlebot():
    v = CrawlerVerifier(allow_network=False).load()
    assert v.classify("2001:4860:4801:10::abcd") == "googlebot"


def test_spoofed_googlebot_ua_is_not_verified(verifier):
    # The whole premise: a Googlebot UA from a random IP must NOT be verified.
    assert verifier.classify("45.83.64.10") is None


def test_ranges_dir_offline_path():
    v = CrawlerVerifier(ranges_dir=FIXTURE_RANGES, allow_network=False).load()
    assert v.classify("66.249.66.1") == "googlebot"
    assert v.classify("157.55.39.10") == "bingbot"
    assert v.classify("8.8.8.8") is None


def test_stats_counts_prefixes(verifier):
    s = verifier.stats()
    assert s["prefixes_total"] > 0
    assert set(s["by_category"]) == {
        "googlebot",
        "special-crawler",
        "user-triggered",
        "bingbot",
    }


# --- network fetch + caching + TTL, all mocked (no real network) -------------


def _fake_doc(prefix: str) -> dict:
    return {"creationTime": "x", "prefixes": [{"ipv4Prefix": prefix}]}


def test_network_fetch_writes_cache(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_fetch(url):
        calls["n"] += 1
        # Give googlebot a unique prefix; everything else empty.
        return _fake_doc("11.22.33.0/24") if "googlebot" in url else {"prefixes": []}

    monkeypatch.setattr(CrawlerVerifier, "_fetch", staticmethod(fake_fetch))
    v = CrawlerVerifier(cache_dir=tmp_path, allow_network=True).load()
    assert v.classify("11.22.33.5") == "googlebot"
    assert calls["n"] == 4  # one per source
    assert (tmp_path / "googlebot.json").exists()


def test_fresh_cache_is_used_without_network(monkeypatch, tmp_path):
    (tmp_path / "googlebot.json").write_text(json.dumps(_fake_doc("99.0.0.0/24")))
    for name in ("special-crawlers", "user-triggered-fetchers", "bingbot"):
        (tmp_path / f"{name}.json").write_text(json.dumps({"prefixes": []}))

    def boom(url):  # pragma: no cover - must never be called
        raise AssertionError("network must not be touched for a fresh cache")

    monkeypatch.setattr(CrawlerVerifier, "_fetch", staticmethod(boom))
    v = CrawlerVerifier(cache_dir=tmp_path, allow_network=True, ttl_seconds=3600).load()
    assert v.classify("99.0.0.5") == "googlebot"


def test_stale_cache_triggers_refresh(monkeypatch, tmp_path):
    gb = tmp_path / "googlebot.json"
    gb.write_text(json.dumps(_fake_doc("99.0.0.0/24")))
    for name in ("special-crawlers", "user-triggered-fetchers", "bingbot"):
        (tmp_path / f"{name}.json").write_text(json.dumps({"prefixes": []}))
    # Make the cache look old.
    old = time.time() - 10_000
    import os

    os.utime(gb, (old, old))

    def fake_fetch(url):
        return _fake_doc("77.0.0.0/24") if "googlebot" in url else {"prefixes": []}

    monkeypatch.setattr(CrawlerVerifier, "_fetch", staticmethod(fake_fetch))
    v = CrawlerVerifier(cache_dir=tmp_path, allow_network=True, ttl_seconds=3600).load()
    # Refreshed range wins; the stale one no longer matches.
    assert v.classify("77.0.0.5") == "googlebot"
    assert v.classify("99.0.0.5") is None


def test_falls_back_to_bundled_when_offline_and_no_cache(tmp_path):
    # Empty cache dir, no network -> must still classify using the bundled snapshot.
    v = CrawlerVerifier(cache_dir=tmp_path / "empty", allow_network=False).load()
    assert v.classify("66.249.66.1") == "googlebot"
