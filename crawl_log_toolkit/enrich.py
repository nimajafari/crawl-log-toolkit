"""Enrich parsed logs with verified-crawler identity and write ZSTD Parquet.

"Enrich once, query many." This stage attaches two columns the analytical SQL
pack relies on:

* ``crawler_category`` - ``googlebot`` / ``special-crawler`` / ``user-triggered``
  / ``bingbot`` / ``NULL`` (not a verified crawler), decided **by IP**.
* ``verified``          - boolean shortcut for ``crawler_category IS NOT NULL``.

Verification runs in Python (see :mod:`crawl_log_toolkit.verify`) and the result
is materialized as columns before the rows are handed to DuckDB. We deliberately
*avoid* a per-row DuckDB Python UDF on the hot path: DuckDB's Python scalar UDFs
pull in ``numpy``, and this toolkit keeps to "stdlib + requests + duckdb". For
ad-hoc interactive SQL you can still register the UDF explicitly with
:func:`register_verifier_udf` (requires numpy).

DuckDB is used for the part it is best at: writing compressed, columnar Parquet
that the query pack then scans repeatedly.
"""

from __future__ import annotations

import ipaddress
import json
import os
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path

from . import SCHEMA_FIELDS
from ._io import reading
from .parse import iter_records
from .verify import CrawlerVerifier, default_verifier

__all__ = [
    "enrich",
    "enrich_records",
    "register_verifier_udf",
    "ENRICHED_COLUMNS",
    "anonymize_ip",
]

# Column types for DuckDB's read_json. Explicit types keep all-NULL columns
# (e.g. request_time on combined logs) from being inferred away.
ENRICHED_COLUMNS: dict[str, str] = {
    "timestamp": "VARCHAR",
    "remote_addr": "VARCHAR",
    "method": "VARCHAR",
    "host": "VARCHAR",
    "uri": "VARCHAR",
    "path": "VARCHAR",
    "status": "INTEGER",
    "bytes_sent": "BIGINT",
    "referer": "VARCHAR",
    "user_agent": "VARCHAR",
    "request_time": "DOUBLE",
    "crawler_category": "VARCHAR",
    "verified": "BOOLEAN",
}


def anonymize_ip(ip: str | None) -> str | None:
    """Mask the host portion of an IP for longer retention (GDPR-friendlier).

    IPv4 -> last octet zeroed (``66.249.66.1`` -> ``66.249.66.0``);
    IPv6  -> last 80 bits zeroed (keeps the /48). Done *after* verification so
    classification accuracy is unaffected.
    """
    if not ip:
        return ip
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip
    if addr.version == 4:
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
    else:
        net = ipaddress.ip_network(f"{ip}/48", strict=False)
    return str(net.network_address)


def _read_jsonl_records(stream: Iterable[str]) -> Iterator[dict]:
    for line in stream:
        line = line.strip()
        if not line:
            continue
        obj = json.loads(line)
        # Ensure every schema field exists so the NDJSON shape is uniform.
        rec = dict.fromkeys(SCHEMA_FIELDS)
        rec.update({k: v for k, v in obj.items() if k in SCHEMA_FIELDS})
        yield rec


def _classify_records(
    records: Iterable[dict],
    verifier: CrawlerVerifier,
    *,
    anonymize_ips: bool = False,
) -> Iterator[dict]:
    for rec in records:
        ip = rec.get("remote_addr")
        category = verifier.classify(ip) if ip else None
        rec["crawler_category"] = category
        rec["verified"] = category is not None
        if anonymize_ips:
            rec["remote_addr"] = anonymize_ip(ip)
        yield rec


def enrich_records(
    records: Iterable[dict],
    output_path: str | Path,
    *,
    verifier: CrawlerVerifier | None = None,
    anonymize_ips: bool = False,
) -> dict:
    """Classify ``records`` and write them as ZSTD Parquet to ``output_path``.

    Returns a stats dict: total rows, verified rows, and a per-category count.
    """
    import duckdb

    verifier = verifier or default_verifier()
    verifier.load()

    total = verified = 0
    by_category: dict[str, int] = {}

    # Bridge Python records -> DuckDB via a temp NDJSON file (no pandas/pyarrow
    # dependency). DuckDB then writes the compressed columnar Parquet.
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False, encoding="utf-8")
    try:
        with tmp:
            for rec in _classify_records(records, verifier, anonymize_ips=anonymize_ips):
                total += 1
                if rec["verified"]:
                    verified += 1
                    cat = rec["crawler_category"]
                    by_category[cat] = by_category.get(cat, 0) + 1
                tmp.write(json.dumps({k: rec.get(k) for k in ENRICHED_COLUMNS}) + "\n")

        out = str(output_path)
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect()
        try:
            columns = ", ".join(f"'{k}': '{v}'" for k, v in ENRICHED_COLUMNS.items())
            con.execute(
                f"CREATE TABLE raw AS SELECT * FROM "
                f"read_json(?, format='newline_delimited', columns={{{columns}}})",
                [tmp.name],
            )
            # Promote the ISO-8601 string to a real TIMESTAMP (UTC) for date math.
            con.execute(
                "CREATE TABLE enriched AS "
                "SELECT CAST(timestamp AS TIMESTAMP) AS timestamp, "
                "* EXCLUDE (timestamp) FROM raw"
            )
            con.execute(f"COPY enriched TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        finally:
            con.close()
    finally:
        os.unlink(tmp.name)

    return {
        "total": total,
        "verified": verified,
        "unverified": total - verified,
        "by_category": by_category,
        "output": str(output_path),
    }


def enrich(
    input_path: str | Path | None,
    output_path: str | Path,
    *,
    fmt: str = "auto",
    from_jsonl: bool = False,
    host: str | None = None,
    verifier: CrawlerVerifier | None = None,
    anonymize_ips: bool = False,
) -> dict:
    """Read raw logs (or normalized JSONL) and write enriched ZSTD Parquet.

    ``input_path`` may be a path, ``-``/``None`` for stdin, and is gzip-transparent.
    With ``from_jsonl=True`` the input is treated as the JSONL produced by
    ``crawl-log parse`` instead of being re-parsed from a raw log format.
    """
    with reading(input_path) as stream:
        if from_jsonl:
            records = _read_jsonl_records(stream)
        else:
            records = iter_records(stream, fmt=fmt, host=host)
        # Materialize within the open stream so the file isn't closed early.
        return enrich_records(records, output_path, verifier=verifier, anonymize_ips=anonymize_ips)


def register_verifier_udf(con, verifier: CrawlerVerifier | None = None) -> None:
    """Register ``is_verified_crawler(ip)`` / ``crawler_category(ip)`` on ``con``.

    Optional and **not** used by :func:`enrich`. DuckDB's Python scalar UDFs
    require ``numpy``; install it if you want to call these from interactive SQL.
    """
    verifier = verifier or default_verifier()
    verifier.load()

    def _category(ip):
        return verifier.classify(ip) if ip else None

    def _verified(ip):
        return verifier.is_verified(ip) if ip else False

    con.create_function("crawler_category", _category, ["VARCHAR"], "VARCHAR")
    con.create_function("is_verified_crawler", _verified, ["VARCHAR"], "BOOLEAN")
