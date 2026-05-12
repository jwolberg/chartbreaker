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
