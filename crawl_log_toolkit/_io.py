"""Tiny gzip-transparent text I/O helpers shared by the CLI and pipeline.

``-`` means stdin/stdout. Files ending in ``.gz`` (or starting with the gzip
magic bytes on read) are de/compressed transparently. Standard streams are
never closed by the context managers.
"""

from __future__ import annotations

import gzip
import io
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_GZIP_MAGIC = b"\x1f\x8b"


def _looks_gzip(path: str | Path) -> bool:
    p = str(path)
    if p.endswith(".gz"):
        return True
    try:
        with open(p, "rb") as fh:
            return fh.read(2) == _GZIP_MAGIC
    except OSError:
        return False


@contextmanager
def reading(path: str | Path | None) -> Iterator[io.TextIOBase]:
    """Open ``path`` for text reading; ``-``/``None`` -> stdin. Gzip-transparent."""
    if path in (None, "-"):
        yield sys.stdin
        return
    if _looks_gzip(path):
        stream = io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", errors="replace")
    else:
        stream = open(path, encoding="utf-8", errors="replace")
    try:
        yield stream
    finally:
        stream.close()


@contextmanager
def writing(path: str | Path | None) -> Iterator[io.TextIOBase]:
    """Open ``path`` for text writing; ``-``/``None`` -> stdout. Gzip by ``.gz``."""
    if path in (None, "-"):
        yield sys.stdout
        return
    if str(path).endswith(".gz"):
        stream = io.TextIOWrapper(gzip.open(path, "wb"), encoding="utf-8")
    else:
        stream = open(path, "w", encoding="utf-8")
    try:
        yield stream
    finally:
        stream.close()
