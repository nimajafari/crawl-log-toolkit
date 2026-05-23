"""Pytest fixtures: an offline verifier and a session-scoped enriched Parquet.

The enriched Parquet is built once per session by running the real enrich stage
on the bundled sample data with NO network (the vendored IP-range snapshot is
used), so the end-to-end tests assert against actual pipeline output.
"""

from __future__ import annotations

import pytest

from tests.helpers import SAMPLE_LOG


@pytest.fixture(scope="session")
def verifier():
    from crawl_log_toolkit.verify import CrawlerVerifier

    return CrawlerVerifier(allow_network=False).load()


@pytest.fixture(scope="session")
def enriched_parquet(tmp_path_factory):
    from crawl_log_toolkit.enrich import enrich
    from crawl_log_toolkit.verify import CrawlerVerifier

    out = tmp_path_factory.mktemp("enriched") / "sample.parquet"
    enrich(
        SAMPLE_LOG,
        out,
        fmt="auto",
        verifier=CrawlerVerifier(allow_network=False).load(),
    )
    return out
