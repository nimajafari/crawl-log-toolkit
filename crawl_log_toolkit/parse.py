"""Multi-format access-log parsing -> a single normalized record schema.

Supported input formats (see :func:`detect_format`):

* ``combined``    - NCSA Combined Log Format. Also covers nginx's default
                    ``combined`` log_format and Apache's ``combined`` LogFormat.
* ``nginx_json``  - newline-delimited JSON as emitted by the recommended
                    ``log_format`` in ``log-formats/nginx-seo.conf``.
* ``cloudflare``  - Cloudflare Logpush "HTTP requests" JSON.
* ``alb``         - AWS Application Load Balancer access logs.

Every parser returns the schema documented in :data:`crawl_log_toolkit.SCHEMA_FIELDS`.
The single most important normalization is ``path`` (the query string stripped
from ``uri``); the rest of the pipeline reasons about URLs by ``path``.
"""

from __future__ import annotations

import json
import re
import shlex
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime

from . import SCHEMA_FIELDS

__all__ = ["iter_records", "parse_line", "detect_format", "ParseError"]


class ParseError(ValueError):
    """Raised when a line cannot be parsed under the selected format."""


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #

_CLF_TIME_RE = re.compile(r"\d{1,2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2}")


def _dash_none(value: str | None) -> str | None:
    """Map the access-log empty sentinel ``-`` (and ``""``) to ``None``."""
    if value is None or value == "-" or value == "":
        return None
    return value


def _to_int(value) -> int | None:
    if value is None or value == "-" or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value) -> float | None:
    if value is None or value == "-" or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    # ALB / nginx use -1 to mean "no measurement"; treat as missing.
    return None if f < 0 else f


def _strip_query(uri: str) -> str:
    """Return the path portion of ``uri`` (everything before the first ``?``)."""
    return uri.split("?", 1)[0]


def parse_timestamp(value) -> str:
    """Normalize any supported timestamp representation to an ISO 8601 UTC string.

    Accepts CLF (``10/Oct/2026:13:55:36 +0000``), ISO 8601 strings (with ``Z``
    or numeric offset), and numeric epoch values (seconds/ms/us/ns auto-detected
    by magnitude — Cloudflare emits unix *nanoseconds* when configured numeric).
    """
    if isinstance(value, (int, float)):
        # Auto-detect epoch units by magnitude.
        v = float(value)
        if v > 1e17:  # nanoseconds
            v /= 1e9
        elif v > 1e14:  # microseconds
            v /= 1e6
        elif v > 1e11:  # milliseconds
            v /= 1e3
        return datetime.fromtimestamp(v, tz=UTC).isoformat()

    s = str(value).strip()
    if _CLF_TIME_RE.search(s):
        dt = datetime.strptime(s, "%d/%b/%Y:%H:%M:%S %z")
        return dt.astimezone(UTC).isoformat()

    # ISO 8601. Python 3.11+ fromisoformat understands a trailing 'Z' and
    # fractional seconds, but normalize 'Z' for older-style safety.
    iso = s[:-1] + "+00:00" if s.endswith("Z") else s
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _record(**kwargs) -> dict:
    """Build a record with every schema field present (missing -> None)."""
    rec = dict.fromkeys(SCHEMA_FIELDS)
    rec.update(kwargs)
    if rec.get("uri") is not None and rec.get("path") is None:
        rec["path"] = _strip_query(rec["uri"])
    return rec


# --------------------------------------------------------------------------- #
# Combined / NCSA (nginx default, Apache combined)
# --------------------------------------------------------------------------- #

# %h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"
# Referer/User-Agent are optional so the same parser also reads the "common"
# variant. An optional trailing number is accepted as request_time for the
# common "$request_time" extension some operators append.
_COMBINED_RE = re.compile(
    r"^(?P<remote_addr>\S+)\s+"
    r"(?P<ident>\S+)\s+"
    r"(?P<user>\S+)\s+"
    r"\[(?P<time>[^\]]+)\]\s+"
    r'"(?P<request>[^"]*)"\s+'
    r"(?P<status>\d{3}|-)\s+"
    r"(?P<bytes>\d+|-)"
    r'(?:\s+"(?P<referer>[^"]*)"\s+"(?P<user_agent>[^"]*)")?'
    r"(?:\s+(?P<request_time>[\d.]+))?"
    r"\s*$"
)


def parse_combined(line: str) -> dict | None:
    m = _COMBINED_RE.match(line.strip())
    if not m:
        return None
    request = m.group("request")
    method = uri = None
    parts = request.split(" ")
    if len(parts) >= 2:
        method, uri = parts[0], parts[1]
    elif request and request != "-":
        uri = request
    return _record(
        timestamp=parse_timestamp(m.group("time")),
        remote_addr=m.group("remote_addr"),
        method=method,
        host=None,  # not present in combined; CLI --host can supply it
        uri=uri or "",
        status=_to_int(m.group("status")),
        bytes_sent=_to_int(m.group("bytes")),
        referer=_dash_none(m.group("referer")),
        user_agent=_dash_none(m.group("user_agent")),
        request_time=_to_float(m.group("request_time")),
    )


# --------------------------------------------------------------------------- #
# nginx JSON (our recommended log_format) and Cloudflare Logpush JSON
# --------------------------------------------------------------------------- #

# Map the many possible JSON key spellings onto our schema. Keys are tried in
# order; the first present wins.
_NGINX_KEYS = {
    "timestamp": ("time", "time_iso8601", "timestamp", "@timestamp"),
    "remote_addr": ("remote_addr", "client_ip", "remote_ip"),
    "method": ("method", "request_method"),
    "host": ("host", "server_name", "http_host"),
    "uri": ("uri", "request_uri"),
    "status": ("status",),
    "bytes_sent": ("bytes_sent", "body_bytes_sent", "bytes"),
    "referer": ("referer", "http_referer", "referrer"),
    "user_agent": ("user_agent", "http_user_agent", "agent"),
    "request_time": ("request_time", "upstream_response_time"),
}

_CLOUDFLARE_KEYS = {
    "timestamp": ("EdgeStartTimestamp",),
    "remote_addr": ("ClientIP",),
    "method": ("ClientRequestMethod",),
    "host": ("ClientRequestHost",),
    "uri": ("ClientRequestURI",),
    "status": ("EdgeResponseStatus",),
    "bytes_sent": ("EdgeResponseBytes",),
    "referer": ("ClientRequestReferer",),
    "user_agent": ("ClientRequestUserAgent",),
    # OriginResponseDurationMs is in milliseconds; converted below.
    "request_time": ("OriginResponseDurationMs", "EdgeTimeToFirstByteMs"),
}


def _first(obj: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in obj and obj[k] not in (None, ""):
            return obj[k]
    return None


def _parse_json_mapping(obj: dict, keymap: dict, *, time_ms: bool) -> dict:
    uri = _first(obj, keymap["uri"]) or ""
    req_time = _first(obj, keymap["request_time"])
    if req_time is not None:
        req_time = _to_float(req_time)
        if req_time is not None and time_ms:
            req_time /= 1000.0
    ts = _first(obj, keymap["timestamp"])
    return _record(
        timestamp=parse_timestamp(ts) if ts is not None else None,
        remote_addr=_first(obj, keymap["remote_addr"]),
        method=_first(obj, keymap["method"]),
        host=_first(obj, keymap["host"]),
        uri=str(uri),
        status=_to_int(_first(obj, keymap["status"])),
        bytes_sent=_to_int(_first(obj, keymap["bytes_sent"])),
        referer=_dash_none(_first(obj, keymap["referer"])),
        user_agent=_dash_none(_first(obj, keymap["user_agent"])),
        request_time=req_time,
    )


def parse_nginx_json(line: str) -> dict | None:
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    return _parse_json_mapping(obj, _NGINX_KEYS, time_ms=False)


def parse_cloudflare(line: str) -> dict | None:
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    return _parse_json_mapping(obj, _CLOUDFLARE_KEYS, time_ms=True)


# --------------------------------------------------------------------------- #
# AWS Application Load Balancer
# --------------------------------------------------------------------------- #

_ALB_TYPES = {"http", "https", "h2", "h2c", "grpc", "grpcs", "ws", "wss"}


def parse_alb(line: str) -> dict | None:
    try:
        fields = shlex.split(line.strip())
    except ValueError:
        return None
    # An ALB line has at least the first 13 positional fields we care about.
    if len(fields) < 13 or fields[0] not in _ALB_TYPES:
        return None
    client = fields[3]
    remote_addr = client.rsplit(":", 1)[0] if ":" in client else client
    # request_processing + target_processing + response_processing.
    durations = [_to_float(fields[i]) for i in (5, 6, 7)]
    measured = [d for d in durations if d is not None]
    request_time = sum(measured) if measured else None

    request = fields[12]  # "METHOD scheme://host:port/path?query PROTO"
    method = host = uri = None
    rparts = request.split(" ")
    if len(rparts) >= 2:
        method = rparts[0]
        url = rparts[1]
        m = re.match(r"^[a-zA-Z][\w+.-]*://([^/]+)(/.*)?$", url)
        if m:
            host = m.group(1).rsplit(":", 1)[0]
            uri = m.group(2) or "/"
        else:
            uri = url
    return _record(
        timestamp=parse_timestamp(fields[1]),
        remote_addr=remote_addr,
        method=method,
        host=host,
        uri=uri or "",
        status=_to_int(fields[8]),
        bytes_sent=_to_int(fields[11]),
        referer=None,  # ALB does not log Referer
        user_agent=_dash_none(fields[13]) if len(fields) > 13 else None,
        request_time=request_time,
    )


# --------------------------------------------------------------------------- #
# Format detection & dispatch
# --------------------------------------------------------------------------- #

_PARSERS = {
    "combined": parse_combined,
    "nginx_json": parse_nginx_json,
    "cloudflare": parse_cloudflare,
    "alb": parse_alb,
}

FORMATS = ("auto", *_PARSERS.keys())

_ALB_DETECT_RE = re.compile(r"^(?:http|https|h2|h2c|grpc|grpcs|ws|wss)\s+\S+T\S+Z\s")


def detect_format(line: str) -> str:
    """Best-effort detection of a single line's format.

    Returns one of ``combined``, ``nginx_json``, ``cloudflare``, ``alb``.
    """
    s = line.strip()
    if s.startswith("{"):
        try:
            obj = json.loads(s)
        except ValueError:
            return "combined"
        if isinstance(obj, dict) and any(
            k in obj for k in ("ClientRequestURI", "EdgeStartTimestamp", "ClientIP")
        ):
            return "cloudflare"
        return "nginx_json"
    if _ALB_DETECT_RE.match(s):
        return "alb"
    return "combined"


def parse_line(line: str, fmt: str = "auto", host: str | None = None) -> dict | None:
    """Parse a single line. Returns ``None`` for blank/unparseable input."""
    if not line.strip():
        return None
    f = detect_format(line) if fmt == "auto" else fmt
    if f not in _PARSERS:
        raise ParseError(f"unknown format: {fmt!r}")
    rec = _PARSERS[f](line)
    if rec is None:
        return None
    if host and not rec.get("host"):
        rec["host"] = host
    return rec


def iter_records(
    lines: Iterable[str],
    fmt: str = "auto",
    host: str | None = None,
    on_error: str = "skip",
) -> Iterator[dict]:
    """Yield normalized records from an iterable of log lines.

    With ``fmt="auto"`` the format is detected from the first non-blank line and
    then applied to the whole stream. Unparseable lines are skipped (the default)
    or raise :class:`ParseError` when ``on_error="raise"``.
    """
    detected: str | None = None if fmt == "auto" else fmt
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        if detected is None:
            detected = detect_format(line)
        rec = _PARSERS[detected](line)
        if rec is None:
            if on_error == "raise":
                raise ParseError(f"line {lineno}: cannot parse as {detected!r}: {line!r}")
            continue
        if host and not rec.get("host"):
            rec["host"] = host
        yield rec
