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


# ---------------------------------------------------------------------------
# Per-tick Orchestrator — telemetry-driven priority re-scoring.
# ---------------------------------------------------------------------------

import pytest

from chartbreaker.config import Budgets
from chartbreaker.observability.store import ObservabilityStore
from chartbreaker.state import (
    AttackAttempt,
    CostObservation,
    TargetResponse,
)


@pytest.fixture
def fresh_store(tmp_path, monkeypatch):
    """Spin up an ObservabilityStore against a tmp SQLite + JSONL pair."""
    monkeypatch.setattr(
        "chartbreaker.agents.orchestrator_agent.regression",
        _StubRegression(open_subcategories=[]),
    )
    store = ObservabilityStore(
        db_path=tmp_path / "runs.sqlite",
        trace_path=tmp_path / "traces.jsonl",
    )
    store.__enter__()
    yield store
    store.__exit__(None, None, None)


class _StubRegression:
    """Stand-in for the regression module so tests don't touch the real YAML."""

    def __init__(self, open_subcategories: list[str]):
        self._open = list(open_subcategories)

    def load_cases(self, include_retired: bool = False):
        return [{"subcategory": sub} for sub in self._open]


def _seed_one_run(store: ObservabilityStore, run_id: str = "run-1") -> str:
    store.start_run(run_id, cli_command="test", operator="test")
    return run_id


def _record_attempt(
    store: ObservabilityStore,
    run_id: str,
    subcategory: str,
    *,
    cost_usd: float = 0.0,
) -> str:
    """Helper: synthesize a campaign + attempt + cost row for telemetry tests."""
    from chartbreaker.state import CampaignBrief

    brief = CampaignBrief(
        subcategory_id=subcategory,
        mutation_budget=1,
        max_cost_usd=0.10,
        rationale="test",
    )
    attempt = AttackAttempt(
        campaign_id=brief.campaign_id,
        subcategory_id=subcategory,
        specialist="injector",
        prompt="x",
    )
    store.write_campaign(run_id, brief)
    store.write_attempt(run_id, attempt)
    if cost_usd > 0:
        store.write_cost(
            run_id,
            CostObservation(
                campaign_id=brief.campaign_id,
                attempt_id=attempt.attempt_id,
                agent="injector",
                provider="openrouter",
                model="hermes",
                prompt_tokens=100,
                completion_tokens=20,
                usd=cost_usd,
            ),
        )
    return attempt.attempt_id


def test_orchestrator_initial_tick_matches_severity_only_planner(fresh_store):
    """First tick before any telemetry should pick the same brief as the legacy
    severity-only planner — 2f or 6c (both critical) first."""
    run_id = _seed_one_run(fresh_store)
    orch = orchestrator_agent.Orchestrator(
        fresh_store, run_id, budgets=Budgets(max_campaign_usd=0.10, max_run_usd=5.0)
    )
    brief = orch.tick_next_brief()
    assert brief is not None
    # 2f and 6c are both critical; whichever comes first is fine.
    assert brief.subcategory_id in {"2f", "6c"}


def test_coverage_ratio_deprioritizes_already_attempted_subcategory(fresh_store):
    """After dispatching 5 attempts on a subcategory, coverage_ratio = 1.0
    so that subcategory's score drops to 0 and the Orchestrator picks elsewhere."""
    run_id = _seed_one_run(fresh_store)
    # Seed 5 prior attempts on the highest-severity subcategory (2f, critical).
    for _ in range(5):
        _record_attempt(fresh_store, run_id, "2f")
    orch = orchestrator_agent.Orchestrator(
        fresh_store, run_id, budgets=Budgets(max_campaign_usd=0.10, max_run_usd=5.0)
    )
    brief = orch.tick_next_brief()
    assert brief is not None
    # 2f should be deprioritized; the Orchestrator picks 6c (other critical)
    # OR the next-highest priority subcategory.
    assert brief.subcategory_id != "2f"


def test_cost_burn_halts_when_budget_exhausted(fresh_store):
    """has_more() must return False once cost exceeds the run budget."""
    run_id = _seed_one_run(fresh_store)
    # Burn $5 in cost rows — exceeds the test budget below.
    _record_attempt(fresh_store, run_id, "1a", cost_usd=5.0)
    orch = orchestrator_agent.Orchestrator(
        fresh_store, run_id, budgets=Budgets(max_campaign_usd=0.10, max_run_usd=1.0)
    )
    assert orch.has_more() is False
    assert orch.tick_next_brief() is None


def test_target_change_signal_boosts_priority(fresh_store):
    """When two recent runs have different target_versions, _target_change_signal
    returns 1.0 and every score doubles. Per-subcategory ordering is preserved."""
    # Older run with a previous target version.
    fresh_store.start_run("run-old", cli_command="test", operator="test")
    older_response = TargetResponse(
        attempt_id="old-attempt",
        http_status=200,
        raw_model_output=None,
        post_verifier_output=None,
        latency_ms=0,
        target_version="gpt-5.4-mini-v1",
    )
    fresh_store._conn.execute(
        "UPDATE runs SET target_version = ? WHERE run_id = ?",
        (older_response.target_version, "run-old"),
    )
    # New run with a different target version.
    fresh_store.start_run("run-new", cli_command="test", operator="test")
    fresh_store._conn.execute(
        "UPDATE runs SET target_version = ? WHERE run_id = ?",
        ("gpt-5.4-mini-v2", "run-new"),
    )

    orch = orchestrator_agent.Orchestrator(
        fresh_store, "run-new", budgets=Budgets(max_campaign_usd=0.10, max_run_usd=5.0)
    )
    assert orch._target_change_signal() == 1.0


def test_regression_multiplier_boosts_subcategories_with_open_cases(fresh_store, monkeypatch):
    """Subcategories with open regression cases get a score multiplier above 1."""
    monkeypatch.setattr(
        orchestrator_agent,
        "regression",
        _StubRegression(open_subcategories=["1a", "1a", "1a"]),  # 3 open cases on 1a
    )
    run_id = _seed_one_run(fresh_store)
    orch = orchestrator_agent.Orchestrator(
        fresh_store, run_id, budgets=Budgets(max_campaign_usd=0.10, max_run_usd=5.0)
    )
    mult_1a = orch._regression_multiplier("1a")
    mult_2f = orch._regression_multiplier("2f")
    # 3 open cases × 0.5 boost = 1.0 + 1.5 = 2.5
    assert mult_1a == pytest.approx(2.5)
    assert mult_2f == 1.0


def test_tick_brief_rationale_includes_telemetry_breakdown(fresh_store):
    """The Orchestrator embeds the priority breakdown in the brief's rationale
    so the dashboard timeline shows why each campaign was picked."""
    run_id = _seed_one_run(fresh_store)
    orch = orchestrator_agent.Orchestrator(
        fresh_store, run_id, budgets=Budgets(max_campaign_usd=0.10, max_run_usd=5.0)
    )
    brief = orch.tick_next_brief()
    assert brief is not None
    assert "priority=" in brief.rationale
    assert "severity=" in brief.rationale
    assert "coverage=" in brief.rationale
    assert "burn=" in brief.rationale


def test_remaining_count_decreases_per_tick(fresh_store):
    run_id = _seed_one_run(fresh_store)
    orch = orchestrator_agent.Orchestrator(
        fresh_store, run_id, budgets=Budgets(max_campaign_usd=0.10, max_run_usd=5.0)
    )
    start = orch.remaining_count()
    orch.tick_next_brief()
    assert orch.remaining_count() == start - 1
    orch.tick_next_brief()
    assert orch.remaining_count() == start - 2


def test_has_more_returns_false_after_queue_drained(fresh_store):
    run_id = _seed_one_run(fresh_store)
    orch = orchestrator_agent.Orchestrator(
        fresh_store, run_id, budgets=Budgets(max_campaign_usd=0.10, max_run_usd=5.0)
    )
    # Drain everything.
    while orch.has_more():
        brief = orch.tick_next_brief()
        if brief is None:
            break
    assert orch.has_more() is False
    assert orch.tick_next_brief() is None


def test_priority_score_clamps_out_of_range_inputs():
    """coverage_ratio > 1 or < 0 should not flip the sign of the score."""
    base = orchestrator_agent.priority_score("high")
    over_covered = orchestrator_agent.priority_score("high", coverage_ratio=1.5)
    assert over_covered == 0.0  # clamped to 1.0 → (1 - 1.0) = 0
    under_covered = orchestrator_agent.priority_score("high", coverage_ratio=-0.5)
    assert under_covered == base  # clamped to 0.0


def test_store_cost_total_for_run_returns_zero_when_no_costs(fresh_store):
    run_id = _seed_one_run(fresh_store)
    assert fresh_store.cost_total_for_run(run_id) == 0.0


def test_store_cost_total_for_run_sums_correctly(fresh_store):
    run_id = _seed_one_run(fresh_store)
    _record_attempt(fresh_store, run_id, "1a", cost_usd=0.001)
    _record_attempt(fresh_store, run_id, "1b", cost_usd=0.002)
    _record_attempt(fresh_store, run_id, "2f", cost_usd=0.003)
    assert fresh_store.cost_total_for_run(run_id) == pytest.approx(0.006)


def test_store_attempts_in_subcategory_for_run_counts_correctly(fresh_store):
    run_id = _seed_one_run(fresh_store)
    for _ in range(3):
        _record_attempt(fresh_store, run_id, "1b")
    _record_attempt(fresh_store, run_id, "1a")
    assert fresh_store.attempts_in_subcategory_for_run(run_id, "1b") == 3
    assert fresh_store.attempts_in_subcategory_for_run(run_id, "1a") == 1
    assert fresh_store.attempts_in_subcategory_for_run(run_id, "9z") == 0
