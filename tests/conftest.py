"""Pytest fixtures: an offline verifier and a session-scoped enriched Parquet.

Both pin the verifier to the synthetic ``tests/fixtures/ipranges`` set via
``ranges_dir`` rather than the bundled production lists. That keeps the suite
deterministic: it neither churns when the real published ranges change (the
bundled lists are full and refreshable with ``crawl-log update-ranges``) nor
silently reads whatever happens to sit in the shared ``~/.cache`` range cache.
The enriched Parquet is built once per session from the bundled sample data so
the end-to-end tests assert against actual pipeline output.
"""

from __future__ import annotations

import pytest

from tests.helpers import FIXTURE_RANGES, SAMPLE_LOG


@pytest.fixture(scope="session")
def verifier():
    from crawl_log_toolkit.verify import CrawlerVerifier

    return CrawlerVerifier(ranges_dir=FIXTURE_RANGES, allow_network=False).load()


@pytest.fixture(scope="session")
def enriched_parquet(tmp_path_factory):
    from crawl_log_toolkit.enrich import enrich
    from crawl_log_toolkit.verify import CrawlerVerifier

    out = tmp_path_factory.mktemp("enriched") / "sample.parquet"
    enrich(
        SAMPLE_LOG,
        out,
        fmt="auto",
        verifier=CrawlerVerifier(ranges_dir=FIXTURE_RANGES, allow_network=False).load(),
    )
    return out
