"""``crawl-log`` command-line interface.

Subcommands compose over pipes and are gzip-transparent:

    crawl-log parse access.log.gz                      # raw logs   -> JSONL
    crawl-log verify access.log.gz --summary-only      # who is a real crawler?
    crawl-log enrich access.log.gz -o enriched.parquet # verify + ZSTD Parquet
    crawl-log analyze enriched.parquet --all           # run the SQL pack

    # piping (parse once, enrich from JSONL):
    crawl-log parse access.log.gz | crawl-log enrich --from-jsonl - -o out.parquet
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime

from . import __version__
from . import analyze as _analyze
from ._io import reading, writing
from .parse import FORMATS, iter_records, parse_line
from .verify import CrawlerVerifier, bundled_ranges_dir, update_ranges

# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #


def _json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def _add_verifier_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("crawler verification")
    g.add_argument(
        "--ranges-dir",
        metavar="DIR",
        help="Load IP-range JSON from this directory (fully offline, authoritative).",
    )
    g.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch fresh IP ranges from the network into the local cache first.",
    )
    g.add_argument(
        "--no-network",
        action="store_true",
        help="Never touch the network; use cache or the bundled snapshot only.",
    )


def _build_verifier(args) -> CrawlerVerifier:
    v = CrawlerVerifier(
        ranges_dir=getattr(args, "ranges_dir", None),
        allow_network=not getattr(args, "no_network", False),
    )
    if getattr(args, "refresh", False):
        if args.no_network:
            sys.exit("error: --refresh and --no-network are mutually exclusive")
        v.refresh()
    else:
        v.load()
    return v


def _print_rows_table(rows: list[dict], stream) -> None:
    if not rows:
        print("(no rows)", file=stream)
        return
    cols = list(rows[0].keys())
    svals = [[("" if r.get(c) is None else str(r.get(c))) for c in cols] for r in rows]
    widths = [max(len(cols[i]), *(len(sv[i]) for sv in svals)) for i in range(len(cols))]
    sep = "  "
    print(sep.join(c.ljust(widths[i]) for i, c in enumerate(cols)), file=stream)
    print(sep.join("-" * widths[i] for i in range(len(cols))), file=stream)
    for sv in svals:
        print(sep.join(sv[i].ljust(widths[i]) for i in range(len(cols))), file=stream)


def _print_rows(rows: list[dict], fmt: str, stream) -> None:
    if fmt == "json":
        print(json.dumps(rows, default=_json_default, indent=2), file=stream)
    elif fmt == "csv":
        if not rows:
            return
        w = csv.DictWriter(stream, fieldnames=list(rows[0].keys()))
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in r.items()})
    else:
        _print_rows_table(rows, stream)


# --------------------------------------------------------------------------- #
# subcommands
# --------------------------------------------------------------------------- #


def cmd_parse(args) -> int:
    count = 0
    with reading(args.input) as src, writing(args.output) as out:
        for rec in iter_records(src, fmt=args.format, host=args.host):
            out.write(json.dumps(rec, default=_json_default) + "\n")
            count += 1
    print(f"parsed {count} records", file=sys.stderr)
    return 0


def cmd_verify(args) -> int:
    verifier = _build_verifier(args)

    # Single-IP classification mode.
    if args.ip:
        category = verifier.classify_fcrdns(args.ip) if args.fcrdns else verifier.classify(args.ip)
        print(category or "not-verified")
        return 0 if category else 1

    total = accepted = 0
    by_category: dict[str, int] = {}
    classify = verifier.classify_fcrdns if args.fcrdns else verifier.classify

    with reading(args.input) as src, writing(args.output) as out:
        for line in src:
            if not line.strip():
                continue
            rec = parse_line(line, fmt=args.format, host=args.host)
            if rec is None:
                continue
            total += 1
            category = classify(rec.get("remote_addr"))
            if category is None:
                continue
            accepted += 1
            by_category[category] = by_category.get(category, 0) + 1
            if not args.summary_only:
                if args.raw:
                    out.write(line if line.endswith("\n") else line + "\n")
                else:
                    rec["crawler_category"] = category
                    rec["verified"] = True
                    out.write(json.dumps(rec, default=_json_default) + "\n")

    summary = {
        "total": total,
        "accepted": accepted,
        "rejected": total - accepted,
        "by_category": by_category,
    }
    print("verify summary: " + json.dumps(summary), file=sys.stderr)
    return 0


def cmd_enrich(args) -> int:
    from .enrich import enrich

    verifier = _build_verifier(args)
    stats = enrich(
        args.input,
        args.output,
        fmt=args.format,
        from_jsonl=args.from_jsonl,
        host=args.host,
        verifier=verifier,
        anonymize_ips=args.anonymize_ips,
    )
    print("enrich summary: " + json.dumps(stats), file=sys.stderr)
    return 0


def cmd_update_ranges(args) -> int:
    dest = args.ranges_dir  # None -> the bundled package dir
    try:
        result = update_ranges(dest)
    except RuntimeError as exc:
        sys.exit(f"error: {exc}")
    except OSError as exc:
        sys.exit(
            f"error: cannot write ranges to {dest or bundled_ranges_dir()}: {exc}\n"
            "(installed packages are often read-only; pass --ranges-dir DIR to write "
            "to a writable location, then use that DIR with --ranges-dir on verify/enrich.)"
        )
    print("update-ranges: " + json.dumps(result), file=sys.stderr)
    return 0


def cmd_analyze(args) -> int:
    if args.list:
        for name in _analyze.list_queries():
            print(name)
        return 0
    if not args.parquet:
        sys.exit("error: a parquet path is required (or use --list)")

    names = args.query or (_analyze.list_queries() if args.all else None)
    if not names:
        sys.exit("error: pass --query NAME (repeatable), --all, or --list")

    with writing(args.output) as out:
        for i, name in enumerate(names):
            rows = _analyze.run_query(
                name,
                args.parquet,
                publication_log=args.publication_log,
                limit=args.limit,
                strict=args.strict,
            )
            if args.format == "json":
                print(json.dumps({name: rows}, default=_json_default, indent=2), file=out)
            else:
                if i:
                    print(file=out)
                print(f"# {name}", file=out)
                if not rows and name in _analyze.list_queries():
                    reqs = _analyze.query_requirements(_analyze.load_query(name))
                    if reqs and not args.publication_log:
                        print(f"(skipped: requires {', '.join(reqs)})", file=out)
                        continue
                _print_rows(rows, args.format, out)
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="crawl-log",
        description="Verified search-crawler analytics from raw access logs.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    # parse
    sp = sub.add_parser("parse", help="Parse raw logs into normalized JSONL.")
    sp.add_argument("input", nargs="?", default="-", help="log file (gzip ok), or - for stdin")
    sp.add_argument("-f", "--format", choices=FORMATS, default="auto")
    sp.add_argument("--host", help="host to attach when the format lacks one (e.g. combined)")
    sp.add_argument("-o", "--output", default="-", help="output JSONL path, or - for stdout")
    sp.set_defaults(func=cmd_parse)

    # verify
    sv = sub.add_parser(
        "verify", help="Filter to verified-crawler lines (IP-based) + print a summary."
    )
    sv.add_argument("input", nargs="?", default="-", help="log file (gzip ok), or - for stdin")
    sv.add_argument("-f", "--format", choices=FORMATS, default="auto")
    sv.add_argument("--host", help="host to attach when the format lacks one")
    sv.add_argument("-o", "--output", default="-", help="verified output, or - for stdout")
    sv.add_argument("--ip", help="classify a single IP and exit (prints the category)")
    sv.add_argument("--raw", action="store_true", help="emit original raw lines, not JSONL")
    sv.add_argument("--summary-only", action="store_true", help="print only the summary")
    sv.add_argument(
        "--fcrdns",
        action="store_true",
        help="verify via forward-confirmed reverse DNS instead of IP ranges (network).",
    )
    _add_verifier_args(sv)
    sv.set_defaults(func=cmd_verify)

    # enrich
    se = sub.add_parser("enrich", help="Verify + extract path, write ZSTD Parquet.")
    se.add_argument("input", nargs="?", default="-", help="log file (gzip ok), or - for stdin")
    se.add_argument("-o", "--output", required=True, help="output .parquet path")
    se.add_argument("-f", "--format", choices=FORMATS, default="auto")
    se.add_argument("--from-jsonl", action="store_true", help="input is parse's JSONL output")
    se.add_argument("--host", help="host to attach when the format lacks one")
    se.add_argument(
        "--anonymize-ips",
        action="store_true",
        help="zero the host octet(s) AFTER verification (longer retention / GDPR).",
    )
    _add_verifier_args(se)
    se.set_defaults(func=cmd_enrich)

    # update-ranges
    su = sub.add_parser(
        "update-ranges",
        help="Fetch the full published crawler IP ranges and write them to disk.",
        description=(
            "Download every source's complete IP-range list (Googlebot, Google "
            "special crawlers, user-triggered fetchers, Bingbot) and write it as "
            "JSON. With no --ranges-dir this refreshes the bundled snapshot in "
            "place; otherwise it writes to the given directory for use with "
            "--ranges-dir on verify/enrich."
        ),
    )
    su.add_argument(
        "-d",
        "--ranges-dir",
        metavar="DIR",
        help="write the lists here instead of the bundled package directory",
    )
    su.set_defaults(func=cmd_update_ranges)

    # analyze
    sa = sub.add_parser("analyze", help="Run the analytical SQL pack on enriched Parquet.")
    sa.add_argument("parquet", nargs="?", help="enriched .parquet path (or glob)")
    sa.add_argument("-q", "--query", action="append", help="query name (repeatable)")
    sa.add_argument("--all", action="store_true", help="run every query")
    sa.add_argument("--list", action="store_true", help="list available queries and exit")
    sa.add_argument("--publication-log", help="CSV (url,published_at) for first-crawl latency")
    sa.add_argument("--limit", type=int, help="cap rows per query")
    sa.add_argument(
        "--format", choices=("table", "json", "csv"), default="table", help="output format"
    )
    sa.add_argument("-o", "--output", default="-", help="output path, or - for stdout")
    sa.add_argument(
        "--strict", action="store_true", help="error (don't skip) on unmet query requirements"
    )
    sa.set_defaults(func=cmd_analyze)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
