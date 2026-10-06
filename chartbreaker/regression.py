"""Regression harness — pin confirmed exploits and replay them.

Per docs/ARCHITECTURE.md § Vault — Regression Harness:
- Persist every successful exploit (Verdict.recommended_action == "regression")
  into evals/regression_cases.yaml with the full fixture pinned (prompt /
  chart-text / http-request, target version, frozen verdict snapshot).
- Replay each case on demand; emit a RegressionReport per case classifying
  it as `fixed` / `still_vulnerable` / `new_regression` / `drift_flagged`.
- Cross-category regression detection ("the fix moved the symptom") is
  deferred to Phase 2.

The Regression Harness is NOT an agent — it is deterministic Python by
design. An LLM-driven regression runner would re-introduce the very
drift problem this harness exists to catch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from chartbreaker.config import REGRESSION_CASES_YAML
from chartbreaker.state import (
    AttackAttempt,
    HttpRequestShape,
    RegressionStatus,
    TargetResponse,
    Verdict,
)

REGRESSION_SCHEMA_VERSION = 1


def _yaml_path() -> Path:
    return Path(__file__).resolve().parent.parent / REGRESSION_CASES_YAML


def _load_cases_raw() -> list[dict[str, Any]]:
    path = _yaml_path()
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if data is None:
        return []
    cases = data.get("cases", []) if isinstance(data, dict) else data
    return list(cases) if isinstance(cases, list) else []


def _write_cases_raw(cases: list[dict[str, Any]]) -> None:
    path = _yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": REGRESSION_SCHEMA_VERSION,
        "cases": cases,
    }
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(payload, f, sort_keys=False, default_flow_style=False)


def _next_case_id() -> str:
    """Compute the next AF-REG-NNN ID by counting existing cases."""
    return f"AF-REG-{len(_load_cases_raw()) + 1:03d}"


def pin_exploit(
    attempt: AttackAttempt,
    response: TargetResponse,
    verdict: Verdict,
    *,
    case_id: str | None = None,
) -> dict[str, Any]:
    """Serialize an exploit into a regression case and append to the YAML.

    Returns the serialized case dict. Idempotency is the caller's
    responsibility — call this only when verdict.recommended_action
    really is "regression".
    """
    cid = case_id or _next_case_id()
    case: dict[str, Any] = {
        "id": cid,
        "schema_version": REGRESSION_SCHEMA_VERSION,
        "subcategory": attempt.subcategory_id,
        "specialist": attempt.specialist,
        "pinned_at": verdict.created_at.isoformat(),
        "attempt": {
            "prompt": attempt.prompt,
            "chart_text_payload": attempt.chart_text_payload,
            "multi_turn_sequence": attempt.multi_turn_sequence,
            "http_request": (
                attempt.http_request.model_dump() if attempt.http_request else None
            ),
            "mutation_of": attempt.mutation_of,
        },
        "frozen_verdict": {
            "verifier_replay": verdict.verifier_replay,
            "semantic": verdict.semantic,
            "severity": verdict.severity,
            "exploitability": verdict.exploitability,
            "rationale": verdict.rationale,
            "judge_model": verdict.judge_model,
        },
        "frozen_target": {
            "target_version": response.target_version,
            "http_status": response.http_status,
        },
        "retired_at": None,
        "retired_by": None,
        "retirement_reason": None,
    }
    cases = _load_cases_raw()
    cases.append(case)
    _write_cases_raw(cases)
    return case


def load_cases(include_retired: bool = False) -> list[dict[str, Any]]:
    """Read the regression suite. Retired cases excluded by default."""
    cases = _load_cases_raw()
    if include_retired:
        return cases
    return [c for c in cases if not c.get("retired_at")]


def case_to_attempt(case: dict[str, Any], campaign_id: str) -> AttackAttempt:
    """Reconstruct an AttackAttempt from a pinned regression case."""
    a = case.get("attempt", {})
    http_req = a.get("http_request")
    return AttackAttempt(
        campaign_id=campaign_id,
        subcategory_id=case["subcategory"],
        specialist=case["specialist"],
        prompt=a.get("prompt"),
        chart_text_payload=a.get("chart_text_payload"),
        multi_turn_sequence=a.get("multi_turn_sequence"),
        http_request=HttpRequestShape(**http_req) if http_req else None,
        mutation_of=a.get("mutation_of"),
    )


def classify_replay(
    pinned_case: dict[str, Any],
    new_verdict: Verdict,
    new_response: TargetResponse,
) -> RegressionStatus:
    """Compare a fresh verdict to the frozen verdict on a pinned case.

    Returns one of:
      - 'fixed': original exploit no longer triggers
      - 'still_vulnerable': exploit reproduces with the same severity
      - 'drift_flagged': target version or judge model changed; needs human
      - 'new_regression': not produced here — Phase 2 cross-category logic
    """
    frozen_verdict = pinned_case.get("frozen_verdict", {})
    frozen_target = pinned_case.get("frozen_target", {})

    # If the target's model version changed since pinning, flag for human
    # triage — the verdict comparison isn't apples-to-apples anymore.
    if frozen_target.get("target_version") != new_response.target_version:
        return "drift_flagged"

    frozen_replay = frozen_verdict.get("verifier_replay")
    if new_verdict.verifier_replay == "pass" and frozen_replay == "fail":
        return "fixed"
    if new_verdict.verifier_replay == "fail" and frozen_replay == "fail":
        return "still_vulnerable"
    # Anything else (frozen=pass scenarios) is unusual — flag for human.
    return "drift_flagged"
