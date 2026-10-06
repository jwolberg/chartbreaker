"""Tests for chartbreaker/audit.py — Phase 5 P5-T1.

Each check has a passing-case and a failing-case test. The CLI exit-code
matrix is covered separately at the bottom. All tests use a tmp_path
ObservabilityStore so they are hermetic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chartbreaker import audit
from chartbreaker.observability.store import ObservabilityStore
from chartbreaker.state import (
    AttackAttempt,
    CampaignBrief,
    CostObservation,
    HttpRequestShape,
    TargetResponse,
    Verdict,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path):
    """A short-lived ObservabilityStore against a tmp SQLite + JSONL pair."""
    s = ObservabilityStore(
        db_path=tmp_path / "runs.sqlite",
        trace_path=tmp_path / "traces.jsonl",
    )
    s.__enter__()
    yield s
    s.__exit__(None, None, None)


def _seed_run(store: ObservabilityStore, run_id: str = "run-A") -> str:
    store.start_run(run_id, cli_command="test", operator="pytest")
    return run_id


def _seed_campaign(
    store: ObservabilityStore,
    run_id: str,
    subcategory_id: str = "1a",
    seed_case_id: str | None = None,
) -> CampaignBrief:
    brief = CampaignBrief(
        subcategory_id=subcategory_id,
        seed_case_id=seed_case_id,
        mutation_budget=0,
        max_cost_usd=0.10,
        rationale="test",
    )
    store.write_campaign(run_id, brief)
    return brief


def _seed_attempt(
    store: ObservabilityStore,
    run_id: str,
    campaign_id: str,
    subcategory_id: str = "1a",
    specialist: str = "cracker",
    pid: int | None = None,
    prompt: str | None = "hello",
) -> AttackAttempt:
    http_request = None
    if pid is not None:
        http_request = HttpRequestShape(
            method="POST",
            path="/copilot",
            body={"pid": pid, "user_question": "hi"},
        )
        prompt = None
    attempt = AttackAttempt(
        campaign_id=campaign_id,
        subcategory_id=subcategory_id,
        specialist=specialist,
        prompt=prompt,
        http_request=http_request,
    )
    store.write_attempt(run_id, attempt)
    return attempt


def _seed_verdict(
    store: ObservabilityStore,
    run_id: str,
    attempt: AttackAttempt,
    *,
    verifier_replay: str = "pass",
    semantic: str = "pass",
    severity: str = "info",
    rationale: str = "ok",
) -> Verdict:
    # Bare-minimum TargetResponse so write_verdict has a parent row.
    response = TargetResponse(
        attempt_id=attempt.attempt_id,
        http_status=200,
        raw_model_output="{}",
        post_verifier_output="{}",
        latency_ms=10,
        target_version="test-target",
    )
    store.write_target_response(run_id, response)
    verdict = Verdict(
        attempt_id=attempt.attempt_id,
        verifier_replay=verifier_replay,  # type: ignore[arg-type]
        semantic=semantic,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        exploitability="moderate",
        rationale=rationale,
        recommended_action="discard",
        judge_model="test",
    )
    store.write_verdict(run_id, verdict)
    return verdict


def _db_path(store: ObservabilityStore) -> Path:
    return store._db_path  # test-only access


# ---------------------------------------------------------------------------
# audit_run shape
# ---------------------------------------------------------------------------


def test_audit_run_unknown_run_id_returns_none(store):
    assert audit.audit_run("does-not-exist", db_path=_db_path(store)) is None


def test_audit_run_clean_run_has_no_findings(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    for _ in range(3):
        attempt = _seed_attempt(store, run_id, brief.campaign_id)
        _seed_verdict(store, run_id, attempt, verifier_replay="pass", semantic="pass")
    report = audit.audit_run(run_id, db_path=_db_path(store))
    assert report is not None
    assert report.ok
    assert report.checks_run == 7
    assert report.findings == []


# ---------------------------------------------------------------------------
# Check 1 — ACL breach
# ---------------------------------------------------------------------------


def test_acl_breach_clean(store):
    from chartbreaker.config import FIXTURE_PIDS

    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    _seed_attempt(store, run_id, brief.campaign_id, pid=FIXTURE_PIDS[0])
    if len(FIXTURE_PIDS) > 1:
        _seed_attempt(store, run_id, brief.campaign_id, pid=FIXTURE_PIDS[1])
    report = audit.audit_run(run_id, db_path=_db_path(store))
    assert all(f.check != "acl_breach" for f in report.findings)


def test_acl_breach_flags_pid_outside_fixture(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    _seed_attempt(store, run_id, brief.campaign_id, pid=999)
    report = audit.audit_run(run_id, db_path=_db_path(store))
    breaches = [f for f in report.findings if f.check == "acl_breach"]
    assert len(breaches) == 1
    assert breaches[0].evidence["pid"] == 999
    assert breaches[0].severity == "fail"


# ---------------------------------------------------------------------------
# Check 2 — Budget overrun
# ---------------------------------------------------------------------------


def test_budget_overrun_clean(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    store.write_cost(
        run_id,
        CostObservation(
            campaign_id=brief.campaign_id,
            agent="cracker",
            provider="openai",
            model="gpt-5.4-nano",
            prompt_tokens=1,
            completion_tokens=1,
            usd=0.05,
        ),
    )
    report = audit.audit_run(run_id, db_path=_db_path(store))
    assert all(f.check != "budget_overrun" for f in report.findings)


def test_budget_overrun_flagged(store, monkeypatch):
    from chartbreaker.config import Budgets

    # Rebind audit.BUDGETS to a tighter cap so a small cost trips the check.
    monkeypatch.setattr(audit, "BUDGETS", Budgets(max_run_usd=0.10))
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    store.write_cost(
        run_id,
        CostObservation(
            campaign_id=brief.campaign_id,
            agent="cracker",
            provider="openai",
            model="x",
            prompt_tokens=1,
            completion_tokens=1,
            usd=0.50,
        ),
    )
    report = audit.audit_run(run_id, db_path=_db_path(store))
    overruns = [f for f in report.findings if f.check == "budget_overrun"]
    assert len(overruns) == 1
    assert overruns[0].evidence["total_usd"] == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# Check 3 — Agent looping
# ---------------------------------------------------------------------------


def test_agent_looping_clean(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    for _ in range(audit.LOOPING_THRESHOLD - 1):
        _seed_attempt(store, run_id, brief.campaign_id)
    report = audit.audit_run(run_id, db_path=_db_path(store))
    assert all(f.check != "agent_looping" for f in report.findings)


def test_agent_looping_flagged(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id, seed_case_id="SEED-X")
    for _ in range(audit.LOOPING_THRESHOLD):
        _seed_attempt(store, run_id, brief.campaign_id)
    report = audit.audit_run(run_id, db_path=_db_path(store))
    loops = [f for f in report.findings if f.check == "agent_looping"]
    assert len(loops) == 1
    assert loops[0].evidence["count"] == audit.LOOPING_THRESHOLD
    assert loops[0].evidence["seed_case_id"] == "SEED-X"


# ---------------------------------------------------------------------------
# Check 4 — Verdict disagreement spike
# ---------------------------------------------------------------------------


def test_disagreement_below_threshold_clean(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    # 5 agreeing verdicts, 1 disagreeing → 16.7% disagreement.
    for _ in range(5):
        attempt = _seed_attempt(store, run_id, brief.campaign_id)
        _seed_verdict(store, run_id, attempt, verifier_replay="pass", semantic="pass")
    attempt = _seed_attempt(store, run_id, brief.campaign_id)
    _seed_verdict(store, run_id, attempt, verifier_replay="pass", semantic="fail")
    report = audit.audit_run(run_id, db_path=_db_path(store))
    assert all(f.check != "verdict_disagreement_spike" for f in report.findings)


def test_disagreement_above_threshold_flagged(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    # 5 disagreements + 0 agreement → 100% disagreement.
    for _ in range(5):
        attempt = _seed_attempt(store, run_id, brief.campaign_id)
        _seed_verdict(store, run_id, attempt, verifier_replay="pass", semantic="fail")
    report = audit.audit_run(run_id, db_path=_db_path(store))
    spikes = [f for f in report.findings if f.check == "verdict_disagreement_spike"]
    assert len(spikes) == 1
    assert spikes[0].evidence["disagree_count"] == 5


def test_disagreement_below_minimum_sample_size_skipped(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    # 2 disagreeing verdicts — below MIN_VERDICTS_FOR_DISAGREEMENT.
    for _ in range(2):
        attempt = _seed_attempt(store, run_id, brief.campaign_id)
        _seed_verdict(store, run_id, attempt, verifier_replay="pass", semantic="fail")
    report = audit.audit_run(run_id, db_path=_db_path(store))
    assert all(f.check != "verdict_disagreement_spike" for f in report.findings)


# ---------------------------------------------------------------------------
# Check 5 — Homogeneous verdicts
# ---------------------------------------------------------------------------


def test_homogeneous_below_minimum_sample_size_skipped(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    for _ in range(3):
        attempt = _seed_attempt(store, run_id, brief.campaign_id)
        _seed_verdict(store, run_id, attempt, verifier_replay="pass", semantic="pass")
    report = audit.audit_run(run_id, db_path=_db_path(store))
    assert all(f.check != "homogeneous_verdicts" for f in report.findings)


def test_homogeneous_all_pass_flagged(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    for _ in range(audit.MIN_ATTEMPTS_FOR_HOMOGENEOUS):
        attempt = _seed_attempt(store, run_id, brief.campaign_id)
        _seed_verdict(store, run_id, attempt, verifier_replay="pass", semantic="pass")
    report = audit.audit_run(run_id, db_path=_db_path(store))
    homog = [f for f in report.findings if f.check == "homogeneous_verdicts"]
    assert len(homog) == 1
    assert homog[0].evidence["verdict_value"] == "pass"
    assert homog[0].evidence["total"] == audit.MIN_ATTEMPTS_FOR_HOMOGENEOUS


def test_homogeneous_mixed_clean(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    for _ in range(3):
        attempt = _seed_attempt(store, run_id, brief.campaign_id)
        _seed_verdict(store, run_id, attempt, verifier_replay="pass")
    for _ in range(3):
        attempt = _seed_attempt(store, run_id, brief.campaign_id)
        _seed_verdict(store, run_id, attempt, verifier_replay="fail", semantic="fail")
    report = audit.audit_run(run_id, db_path=_db_path(store))
    assert all(f.check != "homogeneous_verdicts" for f in report.findings)


# ---------------------------------------------------------------------------
# Check 6 — Severity inversion
# ---------------------------------------------------------------------------


def test_severity_inversion_no_signal_rationale_flagged(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    attempt = _seed_attempt(store, run_id, brief.campaign_id)
    _seed_verdict(
        store,
        run_id,
        attempt,
        verifier_replay="pass",
        semantic="not_run",
        severity="critical",
        rationale="no signal",
    )
    report = audit.audit_run(run_id, db_path=_db_path(store))
    inv = [f for f in report.findings if f.check == "severity_inversion"]
    assert len(inv) == 1
    assert inv[0].severity == "warn"


def test_severity_inversion_real_semantic_signal_not_flagged(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    attempt = _seed_attempt(store, run_id, brief.campaign_id)
    _seed_verdict(
        store,
        run_id,
        attempt,
        verifier_replay="pass",
        semantic="fail",
        severity="critical",
        rationale="semantic Judge flagged actual rule violation",
    )
    report = audit.audit_run(run_id, db_path=_db_path(store))
    assert all(f.check != "severity_inversion" for f in report.findings)


# ---------------------------------------------------------------------------
# Check 7 — Specialist failures
# ---------------------------------------------------------------------------


def _emit_specialist_failed(
    store: ObservabilityStore,
    run_id: str,
    campaign_id: str,
    subcategory: str,
    error_type: str = "MultiTurnGenerationError",
    error_message: str = "LLM returned non-JSON",
) -> None:
    """Helper: write a `specialist_failed` event the way cli.py does."""
    store._emit_event(  # test-only access
        run_id,
        agent="red_team_lead",
        event_type="specialist_failed",
        campaign_id=campaign_id,
        payload={
            "subcategory_id": subcategory,
            "error_type": error_type,
            "error_message": error_message,
        },
    )


def test_specialist_failure_clean(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    _seed_attempt(store, run_id, brief.campaign_id)
    report = audit.audit_run(run_id, db_path=_db_path(store))
    assert all(f.check != "specialist_failure" for f in report.findings)


def test_specialist_failure_one_event_one_finding(store):
    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id, subcategory_id="1d")
    _emit_specialist_failed(store, run_id, brief.campaign_id, "1d")
    report = audit.audit_run(run_id, db_path=_db_path(store))
    fails = [f for f in report.findings if f.check == "specialist_failure"]
    assert len(fails) == 1
    assert fails[0].severity == "warn"
    assert fails[0].evidence["subcategory_id"] == "1d"
    assert fails[0].evidence["error_type"] == "MultiTurnGenerationError"


def test_specialist_failure_multiple_events(store):
    run_id = _seed_run(store)
    brief1 = _seed_campaign(store, run_id, subcategory_id="1d")
    brief2 = _seed_campaign(store, run_id, subcategory_id="3a")
    _emit_specialist_failed(store, run_id, brief1.campaign_id, "1d")
    _emit_specialist_failed(
        store,
        run_id,
        brief2.campaign_id,
        "3a",
        error_type="ValueError",
        error_message="bad payload",
    )
    report = audit.audit_run(run_id, db_path=_db_path(store))
    fails = [f for f in report.findings if f.check == "specialist_failure"]
    assert len(fails) == 2
    subs = sorted(f.evidence["subcategory_id"] for f in fails)
    assert subs == ["1d", "3a"]


# ---------------------------------------------------------------------------
# audit_all
# ---------------------------------------------------------------------------


def test_audit_all_empty_store(store):
    assert audit.audit_all(db_path=_db_path(store)) == []


def test_audit_all_returns_one_report_per_run(store):
    for rid in ("run-A", "run-B"):
        _seed_run(store, rid)
        brief = _seed_campaign(store, rid)
        _seed_attempt(store, rid, brief.campaign_id)
    reports = audit.audit_all(db_path=_db_path(store))
    assert {r.run_id for r in reports} == {"run-A", "run-B"}


# ---------------------------------------------------------------------------
# CLI shim — exit code matrix
# ---------------------------------------------------------------------------


class _CliArgs:
    """Lightweight stand-in for argparse.Namespace in CLI tests."""

    def __init__(self, *, run_id=None, audit_all=False, json=False):
        self.run_id = run_id
        self.audit_all = audit_all
        self.json = json


def test_cli_clean_run_exits_zero(store, capsys, monkeypatch):
    from chartbreaker import audit as audit_mod
    from chartbreaker.cli import _audit_run_cli

    monkeypatch.setattr(audit_mod, "RUNS_SQLITE", str(_db_path(store)))

    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    attempt = _seed_attempt(store, run_id, brief.campaign_id)
    _seed_verdict(store, run_id, attempt)

    rc = _audit_run_cli(_CliArgs(run_id=run_id), audit_mod)
    captured = capsys.readouterr()
    assert rc == 0
    assert "OK" in captured.out


def test_cli_unknown_run_id_exits_two(store, capsys, monkeypatch):
    from chartbreaker import audit as audit_mod
    from chartbreaker.cli import _audit_run_cli

    monkeypatch.setattr(audit_mod, "RUNS_SQLITE", str(_db_path(store)))

    rc = _audit_run_cli(_CliArgs(run_id="nope"), audit_mod)
    captured = capsys.readouterr()
    assert rc == 2
    assert "unknown run_id" in captured.err


def test_cli_missing_run_id_and_not_all_exits_two(store, capsys):
    from chartbreaker import audit as audit_mod
    from chartbreaker.cli import _audit_run_cli

    rc = _audit_run_cli(_CliArgs(), audit_mod)
    captured = capsys.readouterr()
    assert rc == 2
    assert "--all" in captured.err


def test_cli_findings_exit_one(store, capsys, monkeypatch):
    from chartbreaker import audit as audit_mod
    from chartbreaker.cli import _audit_run_cli

    monkeypatch.setattr(audit_mod, "RUNS_SQLITE", str(_db_path(store)))

    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    _seed_attempt(store, run_id, brief.campaign_id, pid=999)  # triggers acl_breach

    rc = _audit_run_cli(_CliArgs(run_id=run_id), audit_mod)
    captured = capsys.readouterr()
    assert rc == 1
    assert "FINDINGS" in captured.out
    assert "acl_breach" in captured.out


def test_cli_json_mode_emits_dict(store, capsys, monkeypatch):
    from chartbreaker import audit as audit_mod
    from chartbreaker.cli import _audit_run_cli

    monkeypatch.setattr(audit_mod, "RUNS_SQLITE", str(_db_path(store)))

    run_id = _seed_run(store)
    brief = _seed_campaign(store, run_id)
    _seed_attempt(store, run_id, brief.campaign_id, pid=999)

    rc = _audit_run_cli(_CliArgs(run_id=run_id, json=True), audit_mod)
    assert rc == 1
    import json as _json

    payload = _json.loads(capsys.readouterr().out)
    assert payload["run_id"] == run_id
    assert payload["checks_run"] == 7
    assert any(f["check"] == "acl_breach" for f in payload["findings"])
