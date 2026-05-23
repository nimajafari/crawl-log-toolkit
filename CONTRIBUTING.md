# Contributing to crawl-log-toolkit

Thanks for helping! This project has **one sharp theme** — verified-crawler log
analysis — and aims to stay small, correct, and dependency-light.

## Ground rules

- **Scope:** verified-crawler log analysis, nothing else. New features should
  serve parse → verify → enrich → analyze (or the reference configs).
- **Dependencies:** runtime is stdlib + `duckdb` + `requests`. Don't add heavy
  deps (`lxml` is acceptable *only* behind the optional `sitemap` extra).
- **Verify by IP, never by user-agent.** Keep unverified traffic a separate
  class; never let it into crawl-budget analysis.
- **Tested from the first commit.** Every parser format and the verifier have
  unit tests, and an end-to-end test runs the full pipeline on the sample data.

## Dev setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
```

CI (`.github/workflows/test.yml`) runs the same on pushes/PRs across Python
3.11–3.13. **No network in tests** — IP ranges are vendored in
`crawl_log_toolkit/data/ipranges/` and tests use `allow_network=False`.

## Correctness pitfalls (do not reintroduce)

These are real bugs that are easy to write; the tests guard against them:

- **`regexp_extract` returns `''`, not `NULL`, on no match (DuckDB).** Wrap
  query-string extraction in `NULLIF(regexp_extract(...), '')` or counts read
  ~100% instead of the real share. (`parameter_proliferation.sql`)
- **Datadog RE2 has no lookahead.** Use `type: exclude_at_match` with a plain
  pattern; never `(?!...)`. (`dashboards/datadog.yaml`)
- **Vector VRL:** use `ip_cidr_contains(cidr, ip) ?? false` (fallible), not
  `includes([...], ip)` (exact string membership — never matches a real IP).
  Don't hardcode ranges; load them from the published JSON on a schedule.
  (`dashboards/vector.toml`)
- **Use `datetime.now(datetime.UTC)`**, never the deprecated `datetime.utcnow()`.
- **Harden XML parsing** (sitemaps) against external entities if you add it.

## Changing the sample data

`scripts/generate_sample_data.py` is the single source of truth. It is fully
deterministic (the gzip is written with `mtime=0`, so output is byte-stable) and
writes both `sample-data/access.log.gz` and the ground-truth
`sample-data/expected/*.json` from independent counters. If you change planted
volumes, **regenerate** and run `pytest` — the e2e test compares the real
pipeline output to those fixtures.

```bash
python scripts/generate_sample_data.py
pytest -q
```

## Adding a query

Drop a `.sql` file in `crawl_log_toolkit/queries/`. It must:

- target the single relation `logs`;
- start with a comment block: what it answers + what *healthy* looks like +
  BigQuery/Snowflake/ClickHouse dialect notes;
- declare external inputs with a `-- requires: <relation>` header (e.g.
  `publications`) so `analyze` can skip it when the input is absent.

## Pull requests

Keep PRs focused. Include tests for new behavior, run `ruff` and `pytest`
locally, and update the README metric catalog if you add a query.
