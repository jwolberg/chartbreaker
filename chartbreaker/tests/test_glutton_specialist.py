"""Tests for the Glutton (DoS / cost-amplification) specialist."""

from __future__ import annotations

import pytest

from chartbreaker.agents.specialists import glutton_specialist
from chartbreaker.state import CampaignBrief


def _brief(subcategory: str) -> CampaignBrief:
    return CampaignBrief(
        subcategory_id=subcategory,
        mutation_budget=1,
        max_cost_usd=0.10,
        rationale="test",
    )


def test_cat_5b_emits_fresh_session_rotation_probe():
    glutton_specialist.reset_rotation_probe_budget()
    attempt = glutton_specialist.generate(_brief("5b"))
    assert attempt.specialist == "glutton"
    assert attempt.subcategory_id == "5b"
    req = attempt.http_request
    assert req is not None
    assert req.bypass_auth is True
    assert req.cookies is not None
    assert req.cookies["PHPSESSID"].startswith("chartbreaker-rotation-probe-")


def test_cat_5b_rotates_session_id_per_probe():
    """Each 5b probe carries a distinct attacker-chosen PHPSESSID."""
    glutton_specialist.reset_rotation_probe_budget()
    first = glutton_specialist.generate(_brief("5b"))
    second = glutton_specialist.generate(_brief("5b"))
    assert (
        first.http_request.cookies["PHPSESSID"]
        != second.http_request.cookies["PHPSESSID"]
    )


def test_cat_5b_respects_budget_exhaustion():
    """After the budget is spent, further 5b probes raise."""
    glutton_specialist.reset_rotation_probe_budget()
    for _ in range(glutton_specialist.ROTATION_PROBE_BUDGET):
        glutton_specialist.generate(_brief("5b"))
    with pytest.raises(glutton_specialist.RotationProbeBudgetExhausted):
        glutton_specialist.generate(_brief("5b"))
    glutton_specialist.reset_rotation_probe_budget()


def test_cat_5c_emits_self_referential_multi_turn_sequence():
    glutton_specialist.reset_rotation_probe_budget()
    attempt = glutton_specialist.generate(_brief("5c"))
    assert attempt.specialist == "glutton"
    assert attempt.subcategory_id == "5c"
    turns = attempt.multi_turn_sequence
    assert turns is not None
    assert 2 <= len(turns) <= 10  # within the server-side turn cap
    # Later turns reference the previous answer — the loop-bait shape.
    assert "previous answer" in turns[1]


def test_unknown_subcategory_raises():
    with pytest.raises(ValueError, match="Glutton does not cover"):
        glutton_specialist.generate(_brief("9z"))
