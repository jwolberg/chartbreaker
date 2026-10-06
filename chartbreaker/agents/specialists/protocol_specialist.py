"""Cracker — deterministic protocol specialist.

Covers THREAT_MODEL:
- Cat 2f: cross-tenant pid swap (authz bypass)
- Cat 6a: CSRF token replay / forgery
- Cat 6c: BAA-gate / privilege-escalation header injection
- Cat 6d: session fixation (pre-seeded PHPSESSID + cookie-flag audit)
- Cat 6e: login brute-force / lockout-bypass

No LLM in the loop — these are HTTP envelope probes per
docs/ARCHITECTURE.md § Cracker.

Login-probe rate cap is enforced HERE (not server-side) so we cannot
lock out the dedicated ChartBreaker test user even if the Orchestrator
mistakenly retries.
"""

from __future__ import annotations

import os

from chartbreaker.config import FIXTURE_PIDS
from chartbreaker.state import AttackAttempt, CampaignBrief, HttpRequestShape
from chartbreaker.target_endpoints import (
    COPILOT_PATH,
    LOGIN_FORM_PATH,
    LOGIN_SUBMIT_PATH,
)

_COPILOT_PATH = COPILOT_PATH

# Pid we expect the dedicated test user NOT to have ACL access to. Chosen to
# be outside chartbreaker.config.FIXTURE_PIDS; PatientAccessGuard should reject.
_OUT_OF_SCOPE_PID = 9999

# ----------------------------------------------------------------------
# Login-probe rate cap (process-local).
# ----------------------------------------------------------------------
# OpenEMR's lockout policy locks the account after a small number of failed
# logins. We must NOT cross that line for the dedicated ChartBreaker test
# user — losing the account would kill the platform until manual unlock.
# The cap is intentionally small: the platform's job is to demonstrate that
# brute-force defenses *exist*, not to actually defeat them.
LOGIN_PROBE_BUDGET = int(os.environ.get("CHARTBREAKER_LOGIN_PROBE_BUDGET", "3"))
_login_probes_issued = 0


class LoginProbeBudgetExhausted(RuntimeError):
    """Cracker refused to emit another login probe to protect the test user."""


def _spend_login_probe_token() -> None:
    """Consume one login-probe token or raise. Process-local — does not persist."""
    global _login_probes_issued
    if _login_probes_issued >= LOGIN_PROBE_BUDGET:
        raise LoginProbeBudgetExhausted(
            f"Cracker login-probe budget ({LOGIN_PROBE_BUDGET}) exhausted. "
            "Further bad-credential attempts could lock out the dedicated test "
            "user. Override via CHARTBREAKER_LOGIN_PROBE_BUDGET only if you "
            "understand the lockout risk."
        )
    _login_probes_issued += 1


def reset_login_probe_budget() -> None:
    """Test helper. Resets the process-local counter."""
    global _login_probes_issued
    _login_probes_issued = 0


def generate(brief: CampaignBrief) -> AttackAttempt:
    """Produce one HTTP-shaped probe for the brief's subcategory."""
    sub = brief.subcategory_id
    if sub == "2f":
        return _pid_swap_attempt(brief)
    if sub == "6a":
        return _csrf_missing_header_attempt(brief)
    if sub == "6c":
        return _baa_gate_flip_attempt(brief)
    if sub == "6d":
        return _session_fixation_attempt(brief)
    if sub == "6e":
        return _login_brute_force_attempt(brief)
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
        "pid": FIXTURE_PIDS[0],
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


# -----------------------------------------------------------------------------
# Cat 6c — BAA-gate flip / privilege-escalation header injection
# -----------------------------------------------------------------------------

# Headers an attacker might try to inject hoping the application trusts a
# proxy/reverse-proxy claim. Each one is the kind of header that, if honored,
# would flip an admin / BAA-signed / impersonation claim without a real
# session change.
_PRIV_ESC_HEADERS: dict[str, str] = {
    "X-Forwarded-User": "admin",
    "X-Remote-User": "admin",
    "X-OpenEMR-Role": "administrator",
    "X-OpenEMR-BAA-Signed": "1",
    "X-Original-User": "admin",
    "X-Impersonate": "admin",
}


def _baa_gate_flip_attempt(brief: CampaignBrief) -> AttackAttempt:
    """Cat 6c: post a Co-Pilot briefing with injected admin-claim headers.

    The dedicated test user has standard clinician privileges, NOT admin.
    If the response materially changes when these headers are present
    (e.g. surfaces facilities outside the user's scope), the application
    is trusting a header it should not. The Judge compares this response
    against a vanilla 6a/6b-style probe to spot drift.
    """
    body = {
        "action": "briefing",
        "pid": FIXTURE_PIDS[0],
        "user_question": "summarize this chart",
    }
    req = HttpRequestShape(
        method="POST",
        path=_COPILOT_PATH,
        body=body,
        headers=dict(_PRIV_ESC_HEADERS),
    )
    return AttackAttempt(
        campaign_id=brief.campaign_id,
        subcategory_id="6c",
        specialist="cracker",
        http_request=req,
    )


# -----------------------------------------------------------------------------
# Cat 6d — Session fixation probe
# -----------------------------------------------------------------------------

# Attacker-chosen PHPSESSID value pre-seeded into the request. A
# fixation-vulnerable server will keep this session ID instead of issuing a
# fresh one on successful login. The probe also checks Set-Cookie flags
# (HttpOnly, Secure) — both should be set for PHPSESSID; missing flags are
# their own (lower-severity) finding.
_FIXATION_SESSION_ID = "chartbreaker-fixation-probe-attacker-chosen"


def _session_fixation_attempt(brief: CampaignBrief) -> AttackAttempt:
    """Cat 6d: GET the login form with an attacker-chosen PHPSESSID cookie.

    Expected: server rotates PHPSESSID before/after auth and the response's
    Set-Cookie does NOT echo back the attacker value. The Judge compares
    the request cookie value vs response cookies; a match (or no rotation)
    is the finding. bypass_auth=True so we don't pollute the live session.
    """
    req = HttpRequestShape(
        method="GET",
        path=LOGIN_FORM_PATH,
        cookies={"PHPSESSID": _FIXATION_SESSION_ID},
        bypass_auth=True,
    )
    return AttackAttempt(
        campaign_id=brief.campaign_id,
        subcategory_id="6d",
        specialist="cracker",
        http_request=req,
    )


# -----------------------------------------------------------------------------
# Cat 6e — Login brute-force probe (rate-capped)
# -----------------------------------------------------------------------------


def _login_brute_force_attempt(brief: CampaignBrief) -> AttackAttempt:
    """Cat 6e: one bad-credential login attempt, rate-capped.

    The intent is to confirm a lockout/CAPTCHA defense fires — NOT to
    actually defeat it. The Cracker-layer LOGIN_PROBE_BUDGET caps issuance
    to a small number per process. Raises LoginProbeBudgetExhausted if
    the caller (Orchestrator) loops past the budget.
    """
    _spend_login_probe_token()
    req = HttpRequestShape(
        method="POST",
        path=LOGIN_SUBMIT_PATH,
        form_data={
            "new_login_session_management": "1",
            "languageChoice": "1",
            "authUser": "chartbreaker-probe-user-does-not-exist",
            "clearPass": "definitely-not-the-password",
        },
        bypass_auth=True,
    )
    return AttackAttempt(
        campaign_id=brief.campaign_id,
        subcategory_id="6e",
        specialist="cracker",
        http_request=req,
    )
