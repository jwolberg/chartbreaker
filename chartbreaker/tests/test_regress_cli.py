"""Tests for the P5-T3 `chartbreaker regress` CI flags.

The end-to-end regression sweep against the live target stays in the
existing manual-only path. These tests cover the pure pieces the new
flags rely on: status aggregation, healthcheck short-circuit, summary
line, JSON shape, and exit-code matrix on the empty-suite path.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from chartbreaker import cli, regression

# ---------------------------------------------------------------------------
# _classify_regress_summary
# ---------------------------------------------------------------------------


def test_classify_regress_summary_empty():
    assert cli._classify_regress_summary([]) == {
        "fixed": 0,
        "still_vulnerable": 0,
        "drift_flagged": 0,
        "new_regression": 0,
    }


def test_classify_regress_summary_mixed():
    per = [
        {"status": "still_vulnerable"},
        {"status": "still_vulnerable"},
        {"status": "drift_flagged"},
        {"status": "fixed"},
    ]
    counts = cli._classify_regress_summary(per)
    assert counts == {
        "fixed": 1,
        "still_vulnerable": 2,
        "drift_flagged": 1,
        "new_regression": 0,
    }


# ---------------------------------------------------------------------------
# Strict-mode failure-status whitelist (AC-3.1)
# ---------------------------------------------------------------------------


def test_strict_fail_statuses_cover_drift_and_new_regression():
    assert "drift_flagged" in cli._REGRESS_STRICT_FAIL_STATUSES
    assert "new_regression" in cli._REGRESS_STRICT_FAIL_STATUSES
    # still_vulnerable + fixed must NOT trigger strict-mode failure.
    assert "still_vulnerable" not in cli._REGRESS_STRICT_FAIL_STATUSES
    assert "fixed" not in cli._REGRESS_STRICT_FAIL_STATUSES


# ---------------------------------------------------------------------------
# Empty-suite path — exercises summary, JSON shape, exit code (AC-3.2, AC-3.3)
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_suite(monkeypatch):
    """Mock the regression suite to be empty so the sweep returns early."""
    monkeypatch.setattr(regression, "load_cases", list)


def test_empty_suite_human_output_has_summary_line(empty_suite, capsys):
    rc = asyncio.run(cli.run_regression_sweep(operator="pytest"))
    out = capsys.readouterr().out
    assert rc == 0
    assert "Summary:" in out
    assert "0 passed" in out


def test_empty_suite_json_output_shape(empty_suite, capsys):
    rc = asyncio.run(cli.run_regression_sweep(operator="pytest", emit_json=True))
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["cases"] == []
    assert payload["summary"] == {
        "passed": 0,
        "regressed": 0,
        "drifted": 0,
        "fixed": 0,
    }
    assert "run_id" in payload


def test_empty_suite_strict_mode_still_exits_zero(empty_suite, capsys):
    rc = asyncio.run(cli.run_regression_sweep(operator="pytest", strict=True))
    assert rc == 0


# ---------------------------------------------------------------------------
# Healthcheck short-circuit (AC-3.5)
# ---------------------------------------------------------------------------


def test_healthcheck_short_circuits_when_target_unreachable(monkeypatch, capsys):
    async def _down():
        return False

    monkeypatch.setattr(cli, "_target_reachable", _down)
    rc = asyncio.run(
        cli.run_regression_sweep(operator="pytest", require_healthcheck=True)
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "unreachable" in err


def test_healthcheck_short_circuits_in_json_mode(monkeypatch, capsys):
    async def _down():
        return False

    monkeypatch.setattr(cli, "_target_reachable", _down)
    rc = asyncio.run(
        cli.run_regression_sweep(
            operator="pytest",
            require_healthcheck=True,
            emit_json=True,
        )
    )
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload == {"skipped": True, "reason": "target_unreachable"}


def test_healthcheck_proceeds_when_target_reachable(monkeypatch, empty_suite, capsys):
    async def _up():
        return True

    monkeypatch.setattr(cli, "_target_reachable", _up)
    rc = asyncio.run(
        cli.run_regression_sweep(
            operator="pytest",
            require_healthcheck=True,
        )
    )
    # Suite is empty → exit 0 + summary line.
    assert rc == 0
    assert "Summary:" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Argparse wiring (AC-3.1, AC-3.2, AC-3.5)
# ---------------------------------------------------------------------------


def test_regress_subparser_accepts_strict_json_healthcheck_flags():
    parser = cli._build_parser()
    args = parser.parse_args(
        ["regress", "--strict", "--json", "--require-target-healthcheck"]
    )
    assert args.cmd == "regress"
    assert args.strict is True
    assert args.json is True
    assert args.require_target_healthcheck is True


def test_regress_subparser_defaults_preserve_backcompat():
    parser = cli._build_parser()
    args = parser.parse_args(["regress"])
    assert args.cmd == "regress"
    assert args.strict is False
    assert args.json is False
    assert args.require_target_healthcheck is False
