"""Tests for the Cracker (protocol) specialist."""

from __future__ import annotations

import pytest

from chartbreaker.agents.specialists import protocol_specialist
from chartbreaker.state import CampaignBrief


def _brief(subcategory: str) -> CampaignBrief:
    return CampaignBrief(
        subcategory_id=subcategory,
        mutation_budget=1,
        max_cost_usd=0.10,
        rationale="test",
    )


def test_cat_2f_emits_pid_swap_attempt():
    attempt = protocol_specialist.generate(_brief("2f"))
    assert attempt.specialist == "cracker"
    assert attempt.subcategory_id == "2f"
    assert attempt.http_request is not None
    assert attempt.http_request.method == "POST"
    assert attempt.http_request.body["pid"] == 9999  # out-of-scope


def test_cat_6a_emits_csrf_missing_header_attempt():
    attempt = protocol_specialist.generate(_brief("6a"))
    assert attempt.specialist == "cracker"
    assert attempt.subcategory_id == "6a"
    assert attempt.http_request is not None
    # The X-CSRF-Token header is explicitly suppressed (set to empty string)
    # so target_client won't auto-inject it.
    assert attempt.http_request.headers.get("X-CSRF-Token") == ""


def test_unknown_subcategory_raises():
    with pytest.raises(ValueError, match="Cracker does not cover"):
        protocol_specialist.generate(_brief("9z"))


def test_cat_6c_injects_privilege_escalation_headers():
    """6c probe sets attacker-claimed admin/proxy headers on the request."""
    attempt = protocol_specialist.generate(_brief("6c"))
    assert attempt.subcategory_id == "6c"
    headers = attempt.http_request.headers
    assert headers.get("X-Forwarded-User") == "admin"
    assert headers.get("X-OpenEMR-BAA-Signed") == "1"


def test_cat_6d_session_fixation_uses_bypass_auth_and_attacker_cookie():
    """6d probe is unauthenticated and pre-seeds an attacker-chosen PHPSESSID."""
    attempt = protocol_specialist.generate(_brief("6d"))
    assert attempt.subcategory_id == "6d"
    req = attempt.http_request
    assert req.bypass_auth is True
    assert req.cookies is not None
    assert req.cookies.get("PHPSESSID") == "chartbreaker-fixation-probe-attacker-chosen"


def test_cat_6e_login_brute_force_budgeted_and_unauthenticated():
    """6e probe is unauthenticated, form-encoded, and consumes a probe token."""
    protocol_specialist.reset_login_probe_budget()
    attempt = protocol_specialist.generate(_brief("6e"))
    assert attempt.subcategory_id == "6e"
    req = attempt.http_request
    assert req.bypass_auth is True
    assert req.form_data is not None
    assert req.form_data["authUser"].startswith("chartbreaker-probe")


def test_cat_6e_respects_budget_exhaustion():
    """After the budget is spent, further 6e probes raise."""
    protocol_specialist.reset_login_probe_budget()
    # Issue probes up to the budget.
    for _ in range(protocol_specialist.LOGIN_PROBE_BUDGET):
        protocol_specialist.generate(_brief("6e"))
    with pytest.raises(protocol_specialist.LoginProbeBudgetExhausted):
        protocol_specialist.generate(_brief("6e"))
    # Reset for other tests.
    protocol_specialist.reset_login_probe_budget()


def test_routing_table_covers_all_five_cracker_subcategories():
    from chartbreaker.agents import red_team_lead

    for sub in ("2f", "6a", "6c", "6d", "6e"):
        assert red_team_lead.specialist_for(sub) == "cracker"
