"""Verify crawler identity by **IP address**, never by user-agent.

A request claiming to be Googlebot is only trustworthy if its source IP is in
Google's published ranges (or forward-confirmed reverse DNS resolves into
``googlebot.com`` / ``google.com``). User-agent strings are trivially spoofed.

This module loads the published IP-range JSON for Googlebot, Google's special
crawlers, Google's user-triggered fetchers, and Bingbot, and classifies an IP
into one of those categories or ``None`` (not a verified crawler).

Range resolution order (per source file):

1. an explicit ``ranges_dir`` (fully offline, authoritative) if given;
2. a fresh copy in ``cache_dir`` (within ``ttl_seconds``);
3. a network refresh into ``cache_dir`` (only when ``allow_network``);
4. the snapshot vendored inside the package (always available offline).

Step 4 is why the toolkit runs out of the box with no network. In production
you should let it refresh (step 3) on a ~daily TTL — published ranges change.
"""

from __future__ import annotations

import bisect
import ipaddress
import json
import socket
import time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

__all__ = [
    "CrawlerVerifier",
    "Source",
    "SOURCES",
    "default_verifier",
    "classify_ip",
    "update_ranges",
    "bundled_ranges_dir",
]

_IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
_IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

# Cache sentinel: a cached ``None`` means "looked up, not a crawler", which must
# be distinguished from "not yet cached". ``dict.get(ip, _MISS)`` tells them apart.
_MISS = object()


@dataclass(frozen=True)
class Source:
    """A published IP-range list and the category its members belong to."""

    name: str  # also the cache/bundle filename stem
    url: str
    category: str
    # PTR-hostname suffixes used by the optional FCrDNS verification path.
    rdns_suffixes: tuple[str, ...] = ()


# Order matters: classification returns the first matching source's category.
SOURCES: tuple[Source, ...] = (
    Source(
        "googlebot",
        "https://developers.google.com/search/apis/ipranges/googlebot.json",
        "googlebot",
        (".googlebot.com", ".google.com"),
    ),
    Source(
        "special-crawlers",
        "https://developers.google.com/search/apis/ipranges/special-crawlers.json",
        "special-crawler",
        (".googlebot.com", ".google.com"),
    ),
    Source(
        "user-triggered-fetchers",
        "https://developers.google.com/search/apis/ipranges/user-triggered-fetchers.json",
        "user-triggered",
        (".google.com", ".googlebot.com", ".gae.googleusercontent.com"),
    ),
    Source(
        "bingbot",
        "https://www.bing.com/toolbox/bingbot.json",
        "bingbot",
        (".search.msn.com",),
    ),
)

# Apple documents Applebot via reverse DNS rather than a published range list,
# so it participates in FCrDNS verification only.
_FCRDNS_EXTRA = {".applebot.apple.com": "applebot"}

DEFAULT_TTL_SECONDS = 86_400  # one day
_USER_AGENT = "crawl-log-toolkit (+https://pypi.org/project/crawl-log-toolkit)"


def bundled_ranges_dir() -> Path:
    """Filesystem path of the vendored IP-range JSON shipped with the package.

    This is where :func:`update_ranges` writes by default, and the offline
    fallback :meth:`CrawlerVerifier._load_bundled` reads from.
    """
    return Path(str(resources.files("crawl_log_toolkit.data.ipranges")))


def update_ranges(
    dest_dir: str | Path | None = None,
    *,
    sources: Iterable[Source] = SOURCES,
) -> dict:
    """Fetch every source's full published list and write it to ``dest_dir``.

    Manual, explicit refresh of the on-disk range lists (as opposed to the
    runtime TTL cache used by :meth:`CrawlerVerifier.load`). With no ``dest_dir``
    it rewrites the bundled snapshot in place — handy for editable/clone installs
    that want current ranges baked in. Point it at a writable directory and pass
    that directory to ``--ranges-dir`` for non-editable installs or air-gapped
    mirrors. Raises ``RuntimeError`` if any source can't be fetched (nothing is
    written for that source, so a half-broken set never overwrites a good one).
    """
    dest = Path(dest_dir) if dest_dir else bundled_ranges_dir()
    fetched: list[tuple[str, dict, int]] = []
    for source in sources:
        doc = CrawlerVerifier._fetch(source.url)
        if doc is None or "prefixes" not in doc:
            raise RuntimeError(f"failed to fetch ranges for {source.name} from {source.url}")
        fetched.append((source.name, doc, len(doc.get("prefixes", []))))

    # Only write once every fetch succeeded, so a network blip can't leave the
    # directory with a mix of fresh and stale lists.
    dest.mkdir(parents=True, exist_ok=True)
    by_source: dict[str, int] = {}
    for name, doc, count in fetched:
        doc = {
            "_comment": (
                "Published crawler IP ranges fetched by `crawl-log update-ranges`. "
                "Refresh with that command; do not edit by hand unless mirroring."
            ),
            **doc,
        }
        (dest / f"{name}.json").write_text(json.dumps(doc, indent=1) + "\n")
        by_source[name] = count
    return {"dest": str(dest), "by_source": by_source, "prefixes_total": sum(by_source.values())}


def _parse_prefixes(doc: dict) -> list[_IPNetwork]:
    """Extract IP networks from a Google/Bing ip-ranges JSON document."""
    nets: list[_IPNetwork] = []
    for entry in doc.get("prefixes", []):
        cidr = entry.get("ipv4Prefix") or entry.get("ipv6Prefix")
        if cidr:
            nets.append(ipaddress.ip_network(cidr, strict=False))
    return nets


def _default_cache_dir() -> Path:
    return Path.home() / ".cache" / "crawl-log-toolkit" / "ipranges"


class CrawlerVerifier:
    """Classify IPs against published crawler IP-range lists."""

    def __init__(
        self,
        ranges_dir: str | Path | None = None,
        cache_dir: str | Path | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        allow_network: bool = True,
        sources: Iterable[Source] = SOURCES,
    ) -> None:
        self.ranges_dir = Path(ranges_dir) if ranges_dir else None
        self.cache_dir = Path(cache_dir) if cache_dir else _default_cache_dir()
        self.ttl_seconds = ttl_seconds
        self.allow_network = allow_network
        self.sources = tuple(sources)
        # Networks split by IP version for cheaper lookups, preserving the
        # source order so precedence (googlebot first) is deterministic.
        self._v4: list[tuple[_IPNetwork, str]] = []
        self._v6: list[tuple[_IPNetwork, str]] = []
        # Precompiled point-lookup index per version: a sorted segment-boundary
        # array (``_starts``) and the winning category per segment (``_cats``),
        # built once in :meth:`load`. Turns classification from an O(ranges)
        # linear scan into an O(log ranges) bisect (see :meth:`_build_index`).
        self._v4_starts: list[int] = []
        self._v4_cats: list[str | None] = []
        self._v6_starts: list[int] = []
        self._v6_cats: list[str | None] = []
        # Per-IP result cache. Access logs repeat IPs heavily (a handful of
        # crawler hosts, many repeat humans), so memoizing collapses millions
        # of rows down to the unique-IP count on the hot enrich path.
        self._cache: dict[str, str | None] = {}
        self._loaded = False

    # -- loading --------------------------------------------------------- #

    def _load_source_doc(self, source: Source) -> dict:
        filename = f"{source.name}.json"

        # 1. Explicit offline directory wins outright.
        if self.ranges_dir is not None:
            return json.loads((self.ranges_dir / filename).read_text())

        cache_path = self.cache_dir / filename

        # 2. Fresh cache.
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age <= self.ttl_seconds:
                try:
                    return json.loads(cache_path.read_text())
                except ValueError:
                    pass  # corrupt cache -> fall through

        # 3. Network refresh.
        if self.allow_network:
            doc = self._fetch(source.url)
            if doc is not None:
                try:
                    self.cache_dir.mkdir(parents=True, exist_ok=True)
                    cache_path.write_text(json.dumps(doc))
                except OSError:
                    pass
                return doc

        # 3b. Stale cache is better than nothing.
        if cache_path.exists():
            try:
                return json.loads(cache_path.read_text())
            except ValueError:
                pass

        # 4. Vendored snapshot (always available, offline).
        return self._load_bundled(filename)

    @staticmethod
    def _fetch(url: str) -> dict | None:
        try:
            import requests

            # A descriptive User-Agent: some publishers (Google's ipranges
            # endpoint among them) reject the default ``python-requests`` UA.
            resp = requests.get(url, timeout=15, headers={"User-Agent": _USER_AGENT})
            resp.raise_for_status()
            return resp.json()
        except Exception:
            # Any network/parse error degrades gracefully to cache/bundle.
            return None

    @staticmethod
    def _load_bundled(filename: str) -> dict:
        data = resources.files("crawl_log_toolkit.data.ipranges").joinpath(filename)
        return json.loads(data.read_text())

    def load(self, force: bool = False) -> CrawlerVerifier:
        """Load all source ranges into memory (idempotent unless ``force``)."""
        if self._loaded and not force:
            return self
        self._v4.clear()
        self._v6.clear()
        self._cache.clear()
        for source in self.sources:
            doc = self._load_source_doc(source)
            for net in _parse_prefixes(doc):
                bucket = self._v6 if net.version == 6 else self._v4
                bucket.append((net, source.category))
        self._v4_starts, self._v4_cats = self._build_index(self._v4)
        self._v6_starts, self._v6_cats = self._build_index(self._v6)
        self._loaded = True
        return self

    @staticmethod
    def _build_index(
        bucket: list[tuple[_IPNetwork, str]],
    ) -> tuple[list[int], list[str | None]]:
        """Compile ``(network, category)`` pairs into a sorted segment index.

        ``bucket`` is in precedence order (earlier entries win). We flatten the
        ranges onto the integer number line, cut it at every range boundary, and
        record the winning category for each elementary segment. Lookups are then
        a single :func:`bisect.bisect_right` into ``starts``. A trailing sentinel
        with category ``None`` makes addresses past the last range resolve to
        "not verified".
        """
        if not bucket:
            return [], []
        # (start, end_inclusive, precedence) for each range; lower index wins.
        ranges = [
            (int(net.network_address), int(net.broadcast_address), order)
            for order, (net, _cat) in enumerate(bucket)
        ]
        boundaries = sorted({r[0] for r in ranges} | {r[1] + 1 for r in ranges})
        starts: list[int] = []
        cats: list[str | None] = []
        for seg_start in boundaries[:-1]:
            best_order: int | None = None
            for start, end, order in ranges:
                if start <= seg_start <= end and (best_order is None or order < best_order):
                    best_order = order
            starts.append(seg_start)
            cats.append(bucket[best_order][1] if best_order is not None else None)
        # Sentinel: anything at/after the final boundary is outside every range.
        starts.append(boundaries[-1])
        cats.append(None)
        return starts, cats

    def refresh(self) -> CrawlerVerifier:
        """Force a network refresh of every source into the cache, then reload."""
        if not self.allow_network:
            raise RuntimeError("refresh() requires allow_network=True")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        for source in self.sources:
            doc = self._fetch(source.url)
            if doc is not None:
                (self.cache_dir / f"{source.name}.json").write_text(json.dumps(doc))
        return self.load(force=True)

    # -- classification -------------------------------------------------- #

    def classify(self, ip: str) -> str | None:
        """Return the crawler category for ``ip`` or ``None`` if not verified.

        Memoized per ``ip`` (access logs repeat IPs heavily) and backed by a
        sorted-segment bisect, so the hot path is a dict hit and cache misses
        cost O(log ranges) instead of scanning every published range.
        """
        if not self._loaded:
            self.load()
        cached = self._cache.get(ip, _MISS)
        if cached is not _MISS:
            return cached
        try:
            addr: _IPAddress = ipaddress.ip_address(ip)
        except ValueError:
            self._cache[ip] = None
            return None
        if addr.version == 6:
            starts, cats = self._v6_starts, self._v6_cats
        else:
            starts, cats = self._v4_starts, self._v4_cats
        category = None
        if starts:
            idx = bisect.bisect_right(starts, int(addr)) - 1
            if idx >= 0:
                category = cats[idx]
        self._cache[ip] = category
        return category

    def is_verified(self, ip: str) -> bool:
        return self.classify(ip) is not None

    # -- optional reverse-DNS (FCrDNS) ----------------------------------- #

    def classify_fcrdns(self, ip: str) -> str | None:
        """Verify ``ip`` via forward-confirmed reverse DNS (network required).

        Resolves the PTR record, checks its suffix against the known crawler
        domains, then forward-resolves that hostname and confirms it maps back
        to ``ip``. Slower than the IP-range method and unsuitable for batch use,
        but it needs no published list and catches newly added ranges.
        """
        try:
            host, _, _ = socket.gethostbyaddr(ip)
        except (OSError, socket.herror, socket.gaierror):
            return None
        host_l = host.lower().rstrip(".")
        category = None
        for source in self.sources:
            for suffix in source.rdns_suffixes:
                if host_l.endswith(suffix):
                    category = source.category
                    break
            if category:
                break
        if category is None:
            for suffix, cat in _FCRDNS_EXTRA.items():
                if host_l.endswith(suffix):
                    category = cat
                    break
        if category is None:
            return None
        # Forward-confirm.
        try:
            resolved = {info[4][0] for info in socket.getaddrinfo(host, None)}
        except (OSError, socket.gaierror):
            return None
        return category if ip in resolved else None

    # -- introspection --------------------------------------------------- #

    def stats(self) -> dict:
        if not self._loaded:
            self.load()
        by_cat: dict[str, int] = {}
        for _, cat in (*self._v4, *self._v6):
            by_cat[cat] = by_cat.get(cat, 0) + 1
        return {
            "prefixes_total": len(self._v4) + len(self._v6),
            "ipv4_prefixes": len(self._v4),
            "ipv6_prefixes": len(self._v6),
            "by_category": by_cat,
        }


# Module-level convenience: a shared verifier over the offline-safe defaults.
_DEFAULT: CrawlerVerifier | None = None


def default_verifier(**kwargs) -> CrawlerVerifier:
    """Return a cached process-wide verifier (offline-safe by default).

    Network refresh is disabled here so importing the toolkit never blocks on
    the network; pass ``allow_network=True`` (or use the CLI ``--refresh``) to
    fetch fresh ranges.
    """
    global _DEFAULT
    if kwargs:
        return CrawlerVerifier(**kwargs).load()
    if _DEFAULT is None:
        _DEFAULT = CrawlerVerifier(allow_network=False).load()
    return _DEFAULT


def classify_ip(ip: str) -> str | None:
    """Classify a single IP using the default offline verifier."""
    return default_verifier().classify(ip)


def utcnow_iso() -> str:
    """Helper used by the CLI summary; uses the non-deprecated UTC clock."""
    return datetime.now(UTC).isoformat()
