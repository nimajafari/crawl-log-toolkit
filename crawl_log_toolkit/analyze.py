"""Run the analytical SQL pack against enriched Parquet.

The query files in :mod:`crawl_log_toolkit.queries` are written against a single
relation named ``logs``. :func:`run_query` creates that view over a Parquet file
(or glob), optionally registers a ``publications`` relation from a CSV, and
executes one query file, returning rows as a list of dicts.

A query may declare dependencies with a header comment, e.g.::

    -- requires: publications

Such a query is skipped (or errors, per ``strict``) when the dependency is absent.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

__all__ = [
    "list_queries",
    "load_query",
    "query_requirements",
    "run_query",
    "QueryError",
]

_REQUIRES_RE = re.compile(r"^\s*--\s*requires:\s*(.+?)\s*$", re.MULTILINE)


class QueryError(RuntimeError):
    """Raised when a query is missing or its declared requirements are unmet."""


def _query_files() -> dict[str, str]:
    """Map query name -> SQL text for every bundled .sql file."""
    out: dict[str, str] = {}
    pkg = resources.files("crawl_log_toolkit.queries")
    for entry in pkg.iterdir():
        name = entry.name
        if name.endswith(".sql"):
            out[name[:-4]] = entry.read_text(encoding="utf-8")
    return dict(sorted(out.items()))


def list_queries() -> list[str]:
    """Return the available query names (filenames without ``.sql``)."""
    return list(_query_files().keys())


def load_query(name: str) -> str:
    """Return the SQL text for a query name, or raise :class:`QueryError`."""
    files = _query_files()
    if name not in files:
        raise QueryError(f"unknown query {name!r}; available: {', '.join(files)}")
    return files[name]


def query_requirements(sql: str) -> list[str]:
    """Parse ``-- requires: x, y`` header lines into a list of relation names."""
    reqs: list[str] = []
    for m in _REQUIRES_RE.finditer(sql):
        reqs.extend(part.strip() for part in m.group(1).split(",") if part.strip())
    return reqs


def _sql_str(value: str | Path) -> str:
    """Quote a path as a SQL string literal (CREATE VIEW can't bind parameters)."""
    return "'" + str(value).replace("'", "''") + "'"


def _connect(parquet_path: str | Path, publication_log: str | Path | None):
    import duckdb

    con = duckdb.connect()
    con.execute(f"CREATE VIEW logs AS SELECT * FROM read_parquet({_sql_str(parquet_path)})")
    available = {"logs"}
    if publication_log is not None:
        # url,published_at CSV. Cast published_at to TIMESTAMP for date math.
        con.execute(
            "CREATE VIEW publications AS "
            "SELECT CAST(url AS VARCHAR) AS url, "
            "CAST(published_at AS TIMESTAMP) AS published_at "
            f"FROM read_csv({_sql_str(publication_log)}, header=true)"
        )
        available.add("publications")
    return con, available


def run_query(
    name: str,
    parquet_path: str | Path,
    *,
    publication_log: str | Path | None = None,
    limit: int | None = None,
    strict: bool = False,
) -> list[dict]:
    """Execute query ``name`` and return rows as dicts.

    Returns ``[]`` when the query's declared requirements aren't met, unless
    ``strict`` is set (then raises :class:`QueryError`).
    """
    sql = load_query(name)
    reqs = query_requirements(sql)
    con, available = _connect(parquet_path, publication_log)
    try:
        missing = [r for r in reqs if r not in available]
        if missing:
            if strict:
                raise QueryError(f"query {name!r} requires {missing} which were not provided")
            return []
        if limit is not None:
            sql = f"SELECT * FROM (\n{sql.rstrip().rstrip(';')}\n) AS _q LIMIT {int(limit)}"
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description]
        return [dict(zip(columns, row, strict=True)) for row in cur.fetchall()]
    finally:
        con.close()


def run_all(
    parquet_path: str | Path,
    *,
    publication_log: str | Path | None = None,
    strict: bool = False,
) -> Iterator[tuple[str, list[dict]]]:
    """Yield ``(name, rows)`` for every bundled query (skips unmet requirements)."""
    for name in list_queries():
        yield name, run_query(name, parquet_path, publication_log=publication_log, strict=strict)
