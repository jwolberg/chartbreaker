"""Tests for the Orchestrator (priority math + initial-brief planner)."""

from __future__ import annotations

from chartbreaker.agents import orchestrator_agent
from chartbreaker.state import CampaignBrief


def test_severity_weight_orders_correctly():
    w = orchestrator_agent.severity_weight
    assert w("info") < w("low") < w("medium") < w("high") < w("critical")


def test_priority_score_collapses_to_severity_when_other_inputs_default():
    """With no coverage/cost data, the Phase-1 formula must equal severity_weight."""
    high_score = orchestrator_agent.priority_score("high")
    low_score = orchestrator_agent.priority_score("low")
    assert high_score == orchestrator_agent.severity_weight("high")
    assert low_score == orchestrator_agent.severity_weight("low")
    assert high_score > low_score


def test_priority_score_falls_with_coverage_ratio():
    base = orchestrator_agent.priority_score("high")
    half_covered = orchestrator_agent.priority_score("high", coverage_ratio=0.5)
    assert half_covered == base * 0.5


def test_priority_score_zeros_when_budget_burned():
    burned = orchestrator_agent.priority_score("critical", cost_burn_factor=1.0)
    assert burned == 0.0


def test_plan_initial_briefs_returns_briefs_severity_descending():
    briefs = orchestrator_agent.plan_initial_briefs()
    assert all(isinstance(b, CampaignBrief) for b in briefs)
    # The 2f probe (severity=critical) must outrank 6a (high) and below.
    subcategories_in_order = [b.subcategory_id for b in briefs]
    assert subcategories_in_order[0] == "2f"  # critical
    # 4c and 1a (medium) should come after 1b and 6a (high)
    high_index = max(subcategories_in_order.index(s) for s in ("1b", "6a"))
    medium_index = min(subcategories_in_order.index(s) for s in ("1a", "4c"))
    assert high_index < medium_index


def test_plan_initial_briefs_attaches_seed_for_injector_subcategories():
    briefs = orchestrator_agent.plan_initial_briefs()
    by_sub = {b.subcategory_id: b for b in briefs}
    assert by_sub["1a"].seed_case_id == "AF-SEED-001"
    assert by_sub["1b"].seed_case_id == "AF-SEED-002"
    # Deterministic specialists don't need seeds
    assert by_sub["2f"].seed_case_id is None
    assert by_sub["6a"].seed_case_id is None
    assert by_sub["4c"].seed_case_id is None
