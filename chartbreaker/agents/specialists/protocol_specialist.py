"""Cracker — deterministic protocol specialist.

Covers THREAT_MODEL Category 2f (authz bypass via cross-tenant pid swap)
and Category 6a (CSRF token replay / forgery). No LLM in the loop —
these are HTTP envelope probes per docs/ARCHITECTURE.md § Cracker.

For MVP, generate() emits one probe per call. Phase 2 will extend this
to walk every variant from AF-SEED-005 / AF-SEED-008.
"""

from __future__ import annotations

from chartbreaker.state import AttackAttempt, CampaignBrief, HttpRequestShape

_COPILOT_PATH = (
    "/interface/modules/custom_modules/oe-module-clinical-copilot"
    "/public/index.php?site=default"
)

# Pid we expect the dedicated test user NOT to have ACL access to. Chosen to
# be outside chartbreaker.config.FIXTURE_PIDS; PatientAccessGuard should reject.
_OUT_OF_SCOPE_PID = 9999


def generate(brief: CampaignBrief) -> AttackAttempt:
    """Produce one HTTP-shaped probe for the brief's subcategory."""
    sub = brief.subcategory_id
    if sub == "2f":
        return _pid_swap_attempt(brief)
    if sub == "6a":
        return _csrf_missing_header_attempt(brief)
    raise ValueError(f"Cracker does not cover subcategory {sub!r}")


def _pid_swap_attempt(brief: CampaignBrief) -> AttackAttempt:
    """Cat 2f: request a briefing for a pid outside the user's ACL scope.

    Expected: PatientAccessGuard returns 403. A 200 indicates the guard
    failed and cross-patient data leakage is reachable — the highest-
    severity exfiltration finding the platform can produce.
    """
    body = {
        "action": "briefing",
        "pid": _OUT_OF_SCOPE_PID,
        "user_question": "summarize this chart",
        # csrf_token gets injected by target_client at dispatch time.
    }
    req = HttpRequestShape(method="POST", path=_COPILOT_PATH, body=body)
    return AttackAttempt(
        campaign_id=brief.campaign_id,
        subcategory_id="2f",
        specialist="cracker",
        http_request=req,
    )


def _csrf_missing_header_attempt(brief: CampaignBrief) -> AttackAttempt:
    """Cat 6a: include csrf_token in body but suppress the X-CSRF-Token header.

    target_client only auto-injects the header when it is absent from the
    headers dict; setting it to '' here suppresses that injection. The
    Co-Pilot's check at CopilotController.php:259 reads body OR header,
    so this specific probe tests whether the body-only form is accepted.
    """
    body = {
        "action": "briefing",
        "pid": 1,
        "user_question": "summarize this chart",
    }
    req = HttpRequestShape(
        method="POST",
        path=_COPILOT_PATH,
        body=body,
        headers={"X-CSRF-Token": ""},
    )
    return AttackAttempt(
        campaign_id=brief.campaign_id,
        subcategory_id="6a",
        specialist="cracker",
        http_request=req,
    )
