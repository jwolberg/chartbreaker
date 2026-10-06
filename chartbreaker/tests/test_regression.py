"""Tests for the regression harness (pin, load, replay-classify)."""

from __future__ import annotations

from pathlib import Path

import pytest

from chartbreaker import regression
from chartbreaker.state import (
    AttackAttempt,
    HttpRequestShape,
    TargetResponse,
    Verdict,
)


@pytest.fixture()
def tmp_regression_yaml(tmp_path: Path, monkeypatch):
    """Redirect the harness to a temp yaml so tests don't pollute the repo."""
    fake = tmp_path / "evals" / "regression_cases.yaml"
    monkeypatch.setattr(regression, "_yaml_path", lambda: fake)
    return fake


def _attempt() -> AttackAttempt:
    return AttackAttempt(
        campaign_id="cmp-test",
        subcategory_id="1b",
        specialist="injector",
        chart_text_payload="malicious content",
    )


def _http_attempt() -> AttackAttempt:
    return AttackAttempt(
        campaign_id="cmp-test",
        subcategory_id="2f",
        specialist="cracker",
        http_request=HttpRequestShape(method="POST", path="/x", body={"pid": 9999}),
    )


def _response(
    target_version: str = "gpt-5.4-mini", status: int = 200
) -> TargetResponse:
    return TargetResponse(
        attempt_id="placeholder",
        http_status=status,
        raw_model_output=None,
        post_verifier_output=None,
        latency_ms=100,
        target_version=target_version,
    )


def _verdict(
    verifier_replay: str = "fail",
    severity: str = "high",
    action: str = "regression",
    judge_model: str = "deterministic",
) -> Verdict:
    return Verdict(
        attempt_id="placeholder",
        verifier_replay=verifier_replay,  # type: ignore[arg-type]
        semantic="not_run",
        severity=severity,  # type: ignore[arg-type]
        exploitability="easy",
        rationale="test",
        recommended_action=action,  # type: ignore[arg-type]
        judge_model=judge_model,
    )


def test_pin_exploit_creates_file_with_one_case(tmp_regression_yaml):
    case = regression.pin_exploit(_attempt(), _response(), _verdict())
    assert case["id"] == "AF-REG-001"
    assert case["subcategory"] == "1b"
    assert case["frozen_verdict"]["verifier_replay"] == "fail"
    cases = regression.load_cases()
    assert len(cases) == 1
    assert cases[0]["id"] == "AF-REG-001"


def test_pin_exploit_increments_ids(tmp_regression_yaml):
    regression.pin_exploit(_attempt(), _response(), _verdict())
    regression.pin_exploit(_http_attempt(), _response(), _verdict())
    cases = regression.load_cases()
    assert [c["id"] for c in cases] == ["AF-REG-001", "AF-REG-002"]


def test_pin_exploit_preserves_http_request_shape(tmp_regression_yaml):
    case = regression.pin_exploit(_http_attempt(), _response(), _verdict())
    assert case["attempt"]["http_request"]["body"]["pid"] == 9999
    # Round-trip back into an AttackAttempt
    rebuilt = regression.case_to_attempt(case, campaign_id="new-cmp")
    assert rebuilt.subcategory_id == "2f"
    assert rebuilt.http_request is not None
    assert rebuilt.http_request.body == {"pid": 9999}


def test_load_cases_excludes_retired_by_default(tmp_regression_yaml):
    regression.pin_exploit(_attempt(), _response(), _verdict())
    # Manually mark the case as retired in the yaml
    cases = regression._load_cases_raw()
    cases[0]["retired_at"] = "2026-05-15T00:00:00Z"
    cases[0]["retired_by"] = "test"
    cases[0]["retirement_reason"] = "deliberate"
    regression._write_cases_raw(cases)

    assert regression.load_cases() == []
    assert len(regression.load_cases(include_retired=True)) == 1


def test_classify_replay_fixed_when_now_passes(tmp_regression_yaml):
    case = regression.pin_exploit(
        _attempt(), _response(), _verdict(verifier_replay="fail")
    )
    new_verdict = _verdict(verifier_replay="pass", action="discard")
    status = regression.classify_replay(case, new_verdict, _response())
    assert status == "fixed"


def test_classify_replay_still_vulnerable(tmp_regression_yaml):
    case = regression.pin_exploit(
        _attempt(), _response(), _verdict(verifier_replay="fail")
    )
    new_verdict = _verdict(verifier_replay="fail")
    status = regression.classify_replay(case, new_verdict, _response())
    assert status == "still_vulnerable"


def test_classify_replay_drift_when_target_version_changes(tmp_regression_yaml):
    case = regression.pin_exploit(_attempt(), _response("gpt-5.4-mini"), _verdict())
    new_verdict = _verdict(verifier_replay="pass", action="discard")
    new_response = _response("gpt-5.4-pro")
    status = regression.classify_replay(case, new_verdict, new_response)
    assert status == "drift_flagged"
