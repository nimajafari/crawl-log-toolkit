"""crawl-log-toolkit: verified search-crawler analytics from raw access logs.

The pipeline is: parse many log formats -> verify crawler identity by IP (not
user-agent) -> enrich -> analyze with SQL.

Public API:
    parse.iter_records(lines, fmt="auto")  -> normalized record dicts
    verify.CrawlerVerifier                  -> classify an IP by published ranges
    enrich.enrich                           -> raw/JSONL logs -> ZSTD Parquet
"""

from __future__ import annotations

__version__ = "0.1.0"

# Canonical normalized record schema emitted by the parsers and consumed by the
# rest of the pipeline. Order is significant for stable JSON / Parquet output.
SCHEMA_FIELDS = (
    "timestamp",  # ISO 8601 UTC string
    "remote_addr",  # client IP as a string
    "method",  # HTTP method
    "host",  # request host (may be None for host-less formats)
    "uri",  # request target *including* query string
    "path",  # request target with the query string stripped
    "status",  # HTTP status code (int)
    "bytes_sent",  # response body bytes (int)
    "referer",  # Referer header (may be None)
    "user_agent",  # User-Agent header (may be None)
    "request_time",  # seconds the origin took to respond (float, may be None)
)

__all__ = ["__version__", "SCHEMA_FIELDS"]
