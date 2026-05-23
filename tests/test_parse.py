"""Unit tests for each log format and the auto-detector."""

from __future__ import annotations

import json
from datetime import UTC

import pytest

from crawl_log_toolkit.parse import ParseError, detect_format, iter_records, parse_line

GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def test_combined_full():
    line = (
        "66.249.66.1 - - [22/May/2026:10:00:00 +0000] "
        f'"GET /products?color=red HTTP/1.1" 200 1234 "https://ref.example/" "{GOOGLEBOT_UA}"'
    )
    rec = parse_line(line, fmt="combined")
    assert rec["remote_addr"] == "66.249.66.1"
    assert rec["method"] == "GET"
    assert rec["uri"] == "/products?color=red"
    assert rec["path"] == "/products"  # query stripped
    assert rec["status"] == 200
    assert rec["bytes_sent"] == 1234
    assert rec["referer"] == "https://ref.example/"
    assert rec["user_agent"] == GOOGLEBOT_UA
    assert rec["request_time"] is None  # combined has no request_time
    assert rec["timestamp"] == "2026-05-22T10:00:00+00:00"


def test_combined_common_no_referer_ua_and_dash_bytes():
    line = '203.0.113.5 - - [22/May/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 -'
    rec = parse_line(line, fmt="combined")
    assert rec["status"] == 200
    assert rec["bytes_sent"] is None
    assert rec["referer"] is None and rec["user_agent"] is None


def test_combined_request_time_extension():
    line = '66.249.66.1 - - [22/May/2026:10:00:00 +0000] "GET /a HTTP/1.1" 200 10 "-" "bot" 0.347'
    rec = parse_line(line, fmt="combined")
    assert rec["request_time"] == pytest.approx(0.347)


def test_combined_timezone_conversion_to_utc():
    line = '1.2.3.4 - - [22/May/2026:12:00:00 +0200] "GET /a HTTP/1.1" 200 1'
    rec = parse_line(line, fmt="combined")
    assert rec["timestamp"] == "2026-05-22T10:00:00+00:00"


def test_host_injected_for_combined():
    line = '1.2.3.4 - - [22/May/2026:10:00:00 +0000] "GET /a HTTP/1.1" 200 1'
    rec = parse_line(line, fmt="combined", host="shop.example.com")
    assert rec["host"] == "shop.example.com"


def test_nginx_json_native_keys():
    obj = {
        "time_iso8601": "2026-05-22T10:00:00+00:00",
        "remote_addr": "157.55.39.10",
        "request_method": "GET",
        "host": "ex.com",
        "request_uri": "/a/b?x=1",
        "status": "404",
        "body_bytes_sent": "55",
        "http_referer": "-",
        "http_user_agent": "bingbot",
        "request_time": "0.123",
    }
    rec = parse_line(json.dumps(obj), fmt="nginx_json")
    assert rec["remote_addr"] == "157.55.39.10"
    assert rec["path"] == "/a/b"
    assert rec["status"] == 404
    assert rec["bytes_sent"] == 55
    assert rec["referer"] is None  # "-" normalized to None
    assert rec["request_time"] == pytest.approx(0.123)


def test_cloudflare_ms_to_seconds_and_zulu_time():
    obj = {
        "EdgeStartTimestamp": "2026-05-22T10:00:00Z",
        "ClientIP": "66.249.66.2",
        "ClientRequestMethod": "GET",
        "ClientRequestHost": "ex.com",
        "ClientRequestURI": "/p?q=2",
        "EdgeResponseStatus": 200,
        "EdgeResponseBytes": 99,
        "ClientRequestUserAgent": "Googlebot",
        "OriginResponseDurationMs": 250,
    }
    rec = parse_line(json.dumps(obj), fmt="cloudflare")
    assert rec["remote_addr"] == "66.249.66.2"
    assert rec["path"] == "/p"
    assert rec["request_time"] == pytest.approx(0.25)  # ms -> s
    assert rec["timestamp"] == "2026-05-22T10:00:00+00:00"


def test_cloudflare_numeric_nanosecond_timestamp():
    from datetime import datetime

    # Cloudflare can emit the timestamp as unix nanoseconds.
    seconds = 1779789600
    obj = {
        "EdgeStartTimestamp": seconds * 1_000_000_000,
        "ClientIP": "66.249.66.2",
        "ClientRequestURI": "/x",
    }
    rec = parse_line(json.dumps(obj), fmt="cloudflare")
    expected = datetime.fromtimestamp(seconds, tz=UTC).isoformat()
    assert rec["timestamp"] == expected


def test_alb_full_url_and_processing_times():
    line = (
        "https 2026-05-22T10:00:00.123456Z app/my-lb/abc 66.249.64.5:54321 "
        "10.0.0.1:80 0.001 0.002 0.003 200 200 120 350 "
        '"GET https://ex.com:443/path?z=9 HTTP/1.1" "Googlebot" ECDHE TLSv1.2 '
        'arn "trace" "ex.com" "cert" 0 2026-05-22T10:00:00.100000Z "forward" "-" "-"'
    )
    rec = parse_line(line, fmt="alb")
    assert rec["remote_addr"] == "66.249.64.5"  # port stripped
    assert rec["host"] == "ex.com"  # from the full request URL
    assert rec["path"] == "/path"
    assert rec["status"] == 200
    assert rec["bytes_sent"] == 350
    assert rec["request_time"] == pytest.approx(0.006)  # 0.001+0.002+0.003


def test_alb_missing_processing_times_are_none():
    line = (
        "http 2026-05-22T10:00:00Z app/lb 1.2.3.4:1 - -1 -1 -1 200 - 0 10 "
        '"GET http://ex.com/a HTTP/1.1" "-" - - - "-" "-" "-" 0 2026-05-22T10:00:00Z "-" "-" "-"'
    )
    rec = parse_line(line, fmt="alb")
    assert rec["request_time"] is None


@pytest.mark.parametrize(
    "line,expected",
    [
        ('1.2.3.4 - - [22/May/2026:10:00:00 +0000] "GET / HTTP/1.1" 200 1', "combined"),
        ('{"remote_addr":"1.2.3.4","request_uri":"/a"}', "nginx_json"),
        ('{"ClientIP":"1.2.3.4","ClientRequestURI":"/a"}', "cloudflare"),
        (
            'https 2026-05-22T10:00:00Z app/lb 1.2.3.4:1 - 0 0 0 200 200 0 0 "GET http://e/a H" "-"',
            "alb",
        ),
    ],
)
def test_detect_format(line, expected):
    assert detect_format(line) == expected


def test_iter_records_skips_blank_and_garbage():
    lines = [
        "",
        "not a log line at all",
        '1.2.3.4 - - [22/May/2026:10:00:00 +0000] "GET /a HTTP/1.1" 200 1',
        "   ",
    ]
    recs = list(iter_records(lines, fmt="combined"))
    assert len(recs) == 1
    assert recs[0]["path"] == "/a"


def test_iter_records_raise_mode():
    with pytest.raises(ParseError):
        list(iter_records(["garbage"], fmt="combined", on_error="raise"))
