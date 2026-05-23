"""Shared paths and helpers for the test suite (importable, non-conftest)."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_LOG = REPO_ROOT / "sample-data" / "access.log.gz"
PUBLICATION_LOG = REPO_ROOT / "sample-data" / "publication_log.csv"
EXPECTED_DIR = REPO_ROOT / "sample-data" / "expected"
FIXTURE_RANGES = Path(__file__).resolve().parent / "fixtures" / "ipranges"


def load_expected(name: str) -> dict:
    return json.loads((EXPECTED_DIR / f"{name}.json").read_text())
