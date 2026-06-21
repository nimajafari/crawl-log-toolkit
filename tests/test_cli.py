"""CLI smoke tests for parse / verify / enrich / analyze and pipe composition."""

from __future__ import annotations

import json

import duckdb

from crawl_log_toolkit.cli import main
from tests.helpers import SAMPLE_LOG


def test_parse_writes_440_records(tmp_path):
    out = tmp_path / "parsed.jsonl"
    rc = main(["parse", str(SAMPLE_LOG), "-o", str(out)])
    assert rc == 0
    lines = out.read_text().strip().splitlines()
    assert len(lines) == 440
    first = json.loads(lines[0])
    assert "path" in first and "timestamp" in first


def test_verify_single_ip(capsys):
    assert main(["verify", "--ip", "66.249.66.1", "--no-network"]) == 0
    assert capsys.readouterr().out.strip() == "googlebot"


def test_verify_single_ip_spoofer_exit_code(capsys):
    rc = main(["verify", "--ip", "45.83.64.10", "--no-network"])
    assert rc == 1
    assert capsys.readouterr().out.strip() == "not-verified"


def test_verify_summary_only(capsys):
    rc = main(["verify", str(SAMPLE_LOG), "--summary-only", "--no-network"])
    assert rc == 0
    err = capsys.readouterr().err
    summary = json.loads(err.split("verify summary: ", 1)[1])
    assert summary["accepted"] == 350
    assert summary["rejected"] == 90


def test_enrich_then_analyze_json(tmp_path, capsys):
    parquet = tmp_path / "e.parquet"
    assert main(["enrich", str(SAMPLE_LOG), "-o", str(parquet), "--no-network"]) == 0
    capsys.readouterr()  # drop enrich summary
    rc = main(["analyze", str(parquet), "-q", "parameter_proliferation", "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    row = payload["parameter_proliferation"][0]
    assert row["parameterized_pct"] == 17.14


def test_analyze_list(capsys):
    assert main(["analyze", "--list"]) == 0
    listed = capsys.readouterr().out.split()
    assert "parameter_proliferation" in listed
    assert "crawl_trap_detection" in listed


def test_verify_directory_input_errors_cleanly(tmp_path, capsys):
    # Passing a directory must produce a one-line error, not a traceback.
    rc = main(["verify", str(tmp_path), "--summary-only", "--no-network"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "Traceback" not in err
    assert str(tmp_path) in err


def test_verify_missing_file_errors_cleanly(tmp_path, capsys):
    rc = main(["verify", str(tmp_path / "nope.log"), "--summary-only", "--no-network"])
    assert rc == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "Traceback" not in err


def test_pipe_parse_to_enrich_from_jsonl(tmp_path, capsys):
    # Emulate: crawl-log parse ... | crawl-log enrich --from-jsonl - -o out.parquet
    jsonl = tmp_path / "p.jsonl"
    assert main(["parse", str(SAMPLE_LOG), "-o", str(jsonl)]) == 0
    parquet = tmp_path / "e.parquet"
    assert main(["enrich", str(jsonl), "--from-jsonl", "-o", str(parquet), "--no-network"]) == 0
    n = (
        duckdb.connect()
        .execute(f"SELECT COUNT(*) FROM read_parquet('{parquet}') WHERE verified")
        .fetchone()[0]
    )
    assert n == 350


def test_anonymize_ips_flag(tmp_path):
    parquet = tmp_path / "a.parquet"
    assert (
        main(["enrich", str(SAMPLE_LOG), "-o", str(parquet), "--no-network", "--anonymize-ips"])
        == 0
    )
    # No stored IP should retain a non-zero final octet.
    bad = (
        duckdb.connect()
        .execute(
            f"SELECT COUNT(*) FROM read_parquet('{parquet}') "
            "WHERE remote_addr LIKE '%.%.%.%' AND split_part(remote_addr, '.', 4) <> '0'"
        )
        .fetchone()[0]
    )
    assert bad == 0
