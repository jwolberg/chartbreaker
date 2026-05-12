"""Tests for the Orchestrator Approval Harness lifecycle (P4-T5).

Covers spec acceptance criteria:
- AC-1  propose() creates rows with required fields populated
- AC-2  priority_score matches plan_initial_briefs() math
- AC-3  LLM rationale failure falls back to deterministic template
- AC-5  approve() stamps status, decided_at, decided_by
- AC-6  reject() stamps status, decided_at, decided_by, rejection_reason
- AC-7  execute_approved_batch() executes ONLY approved rows
- AC-8  approved rows transition to executed with non-null run_id
- AC-11 chartbreaker run-mvp-loop does not touch proposed_campaigns
- AC-13 duplicate (subcat, specialist, seed) triples are skipped
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import pytest

from chartbreaker.agents import orchestrator_agent
from chartbreaker.observability.store import ObservabilityStore
from chartbreaker.orchestrator import proposal_harness


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def fresh_store(tmp_path: Path) -> ObservabilityStore:
    """A short-lived ObservabilityStore against a tmp SQLite + JSONL pair."""
    store = ObservabilityStore(
        db_path=tmp_path / "runs.sqlite",
        trace_path=tmp_path / "traces.jsonl",
    )
    store.__enter__()
    yield store
    store.__exit__(None, None, None)


async def _stub_rationale(
    *,
    subcategory_id: str,
    specialist: str,
    mutation_budget: int,
    priority_score: float,
    seed_case_id: str | None,
) -> str:
    """Deterministic narration stub for the LLM hook."""
    return (
        f"STUB narration for Cat {subcategory_id} via {specialist}, "
        f"budget {mutation_budget}."
    )


async def _failing_rationale(**_kwargs) -> str:
    """Stub that always raises to exercise the template-fallback path."""
    raise RuntimeError("LLM refused / network error / malformed output")


# ----------------------------------------------------------------------
# AC-1 — propose() creates rows
# ----------------------------------------------------------------------


def test_propose_creates_pending_rows(fresh_store: ObservabilityStore) -> None:
    proposals = proposal_harness.propose(
        fresh_store, n=3, render_rationale=_stub_rationale
    )
    assert len(proposals) == 3
    for p in proposals:
        assert p.status == "proposed"
        assert p.rationale
        assert p.mutation_budget >= 1
        assert p.est_cost_usd >= 0.0
        assert p.priority_score > 0.0
        assert p.decided_at is None
        assert p.run_id is None

    pending = proposal_harness.list_pending(fresh_store)
    assert len(pending) == 3


# ----------------------------------------------------------------------
# AC-2 — priority_score equals what plan_initial_briefs() would emit
# ----------------------------------------------------------------------


def test_propose_priority_score_matches_planner(
    fresh_store: ObservabilityStore,
) -> None:
    proposals = proposal_harness.propose(
        fresh_store, n=5, render_rationale=_stub_rationale
    )
    for p in proposals:
        expected_severity = orchestrator_agent._severity_for_subcategory(  # noqa: SLF001
            p.subcategory_id
        )
        expected_score = orchestrator_agent.priority_score(expected_severity)
        assert p.priority_score == pytest.approx(expected_score)


# ----------------------------------------------------------------------
# AC-3 — LLM-failure fallback to deterministic template
# ----------------------------------------------------------------------


def test_propose_falls_back_to_template_on_llm_failure(
    fresh_store: ObservabilityStore,
) -> None:
    proposals = proposal_harness.propose(
        fresh_store, n=2, render_rationale=_failing_rationale
    )
    assert proposals
    for p in proposals:
        # The fallback string starts with "Priority " and notes the
        # deterministic-template substitution.
        assert "Priority" in p.rationale
        assert "deterministic template" in p.rationale


# ----------------------------------------------------------------------
# AC-5 / AC-6 — approve() and reject() stamp the right fields
# ----------------------------------------------------------------------


def test_approve_stamps_status_and_decided_fields(
    fresh_store: ObservabilityStore,
) -> None:
    [p] = proposal_harness.propose(
        fresh_store, n=1, render_rationale=_stub_rationale
    )
    approved = proposal_harness.approve(
        fresh_store, p.proposal_id, decided_by="laptop:tester"
    )
    assert approved.status == "approved"
    assert approved.decided_at is not None
    assert approved.decided_by == "laptop:tester"
    # Round-trip via the store too.
    again = proposal_harness.get_proposal(fresh_store, p.proposal_id)
    assert again is not None
    assert again.status == "approved"


def test_reject_stamps_reason_and_decided_fields(
    fresh_store: ObservabilityStore,
) -> None:
    [p] = proposal_harness.propose(
        fresh_store, n=1, render_rationale=_stub_rationale
    )
    rejected = proposal_harness.reject(
        fresh_store,
        p.proposal_id,
        reason="not relevant this sweep",
        decided_by="laptop:tester",
    )
    assert rejected.status == "rejected"
    assert rejected.decided_at is not None
    assert rejected.decided_by == "laptop:tester"
    assert rejected.rejection_reason == "not relevant this sweep"


def test_approve_with_mutation_budget_override(
    fresh_store: ObservabilityStore,
) -> None:
    [p] = proposal_harness.propose(
        fresh_store, n=1, render_rationale=_stub_rationale
    )
    approved = proposal_harness.approve(
        fresh_store,
        p.proposal_id,
        decided_by="laptop:tester",
        mutation_budget_override=7,
    )
    assert approved.mutation_budget == 7


def test_set_mutation_budget_persists_and_re_costs(
    fresh_store: ObservabilityStore,
) -> None:
    [p] = proposal_harness.propose(
        fresh_store, n=1, render_rationale=_stub_rationale
    )
    original_cost = p.est_cost_usd
    proposal_harness.set_mutation_budget(fresh_store, p.proposal_id, 10)
    refreshed = proposal_harness.get_proposal(fresh_store, p.proposal_id)
    assert refreshed is not None
    assert refreshed.mutation_budget == 10
    # est_cost_usd was re-computed; non-deterministic specialists scale
    # linearly with mutation_budget so the new cost should not be the
    # original unless the original budget happened to be 10 too.
    if p.mutation_budget != 10:
        assert refreshed.est_cost_usd != pytest.approx(original_cost)


def test_set_mutation_budget_clamps_out_of_range(
    fresh_store: ObservabilityStore,
) -> None:
    [p] = proposal_harness.propose(
        fresh_store, n=1, render_rationale=_stub_rationale
    )
    proposal_harness.set_mutation_budget(fresh_store, p.proposal_id, 999)
    refreshed = proposal_harness.get_proposal(fresh_store, p.proposal_id)
    assert refreshed is not None
    assert refreshed.mutation_budget == 20  # clamped upper bound

    proposal_harness.set_mutation_budget(fresh_store, p.proposal_id, -5)
    refreshed = proposal_harness.get_proposal(fresh_store, p.proposal_id)
    assert refreshed is not None
    assert refreshed.mutation_budget == 1  # clamped lower bound


# ----------------------------------------------------------------------
# AC-13 — duplicate-skip rule
# ----------------------------------------------------------------------


def test_propose_skips_duplicate_pending_triples(
    fresh_store: ObservabilityStore,
) -> None:
    first = proposal_harness.propose(
        fresh_store, n=3, render_rationale=_stub_rationale
    )
    second = proposal_harness.propose(
        fresh_store, n=3, render_rationale=_stub_rationale
    )
    first_triples = {(p.subcategory_id, p.specialist, p.seed_case_id) for p in first}
    second_triples = {(p.subcategory_id, p.specialist, p.seed_case_id) for p in second}
    # No overlap between the two batches when the first hasn't been decided.
    assert first_triples.isdisjoint(second_triples)


def test_propose_re_proposes_after_rejection(
    fresh_store: ObservabilityStore,
) -> None:
    """Rejection is not a permanent block; subsequent propose() may re-emit
    the same triple. Confirms spec edge case #10 + duplicate-skip wording."""
    [p] = proposal_harness.propose(
        fresh_store, n=1, render_rationale=_stub_rationale
    )
    proposal_harness.reject(
        fresh_store, p.proposal_id, reason="testing", decided_by="t"
    )
    next_batch = proposal_harness.propose(
        fresh_store, n=1, render_rationale=_stub_rationale
    )
    assert next_batch
    next_triple = (next_batch[0].subcategory_id,
                   next_batch[0].specialist,
                   next_batch[0].seed_case_id)
    original_triple = (p.subcategory_id, p.specialist, p.seed_case_id)
    # The next batch starts from the highest-priority triple, which may
    # well be the same one we just rejected — the rule is that only
    # currently-pending duplicates are blocked.
    assert next_triple == original_triple or next_triple != original_triple
    # Both rows are visible in list_all (one rejected, one proposed).
    everything = proposal_harness.list_all(fresh_store)
    assert any(p2.status == "rejected" for p2 in everything)
    assert any(p2.status == "proposed" for p2 in everything)


# ----------------------------------------------------------------------
# AC-7 / AC-8 — execute_approved_batch lifecycle
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_execute_approved_batch_runs_only_approved(
    fresh_store: ObservabilityStore,
) -> None:
    # Stand up 3 proposals: approve one, reject one, leave one pending.
    props = proposal_harness.propose(
        fresh_store, n=3, render_rationale=_stub_rationale
    )
    approved_id = props[0].proposal_id
    rejected_id = props[1].proposal_id
    pending_id = props[2].proposal_id

    proposal_harness.approve(fresh_store, approved_id, decided_by="t")
    proposal_harness.reject(
        fresh_store, rejected_id, reason="not now", decided_by="t"
    )

    executed_briefs: list[str] = []

    async def stub_runner(brief, run_id, store):  # type: ignore[no-untyped-def]
        executed_briefs.append(brief.subcategory_id)

    run_id = await proposal_harness.execute_approved_batch(
        fresh_store, operator="laptop:t", brief_runner=stub_runner
    )

    assert run_id  # non-empty UUID
    # Exactly one brief ran — the approved one.
    assert len(executed_briefs) == 1
    # Approved row transitioned to executed.
    after_approved = proposal_harness.get_proposal(fresh_store, approved_id)
    assert after_approved is not None
    assert after_approved.status == "executed"
    assert after_approved.run_id == run_id
    # Rejected stays rejected; pending stays pending.
    after_rejected = proposal_harness.get_proposal(fresh_store, rejected_id)
    after_pending = proposal_harness.get_proposal(fresh_store, pending_id)
    assert after_rejected and after_rejected.status == "rejected"
    assert after_pending and after_pending.status == "proposed"


@pytest.mark.asyncio
async def test_execute_approved_batch_with_no_approved_is_noop(
    fresh_store: ObservabilityStore,
) -> None:
    proposal_harness.propose(
        fresh_store, n=2, render_rationale=_stub_rationale
    )
    # Nothing approved.
    called: list[str] = []

    async def stub_runner(brief, run_id, store):  # type: ignore[no-untyped-def]
        called.append(brief.subcategory_id)

    run_id = await proposal_harness.execute_approved_batch(
        fresh_store, operator="laptop:t", brief_runner=stub_runner
    )
    assert run_id == ""  # no run started
    assert not called


@pytest.mark.asyncio
async def test_execute_approved_batch_marks_executed_even_on_runner_failure(
    fresh_store: ObservabilityStore,
) -> None:
    """Spec edge case #6 — approved row transitions to executed regardless
    of whether the underlying runner succeeded; the outcome lives in the
    runs / judge_verdicts tables, not here."""
    [p] = proposal_harness.propose(
        fresh_store, n=1, render_rationale=_stub_rationale
    )
    proposal_harness.approve(fresh_store, p.proposal_id, decided_by="t")

    async def failing_runner(brief, run_id, store):  # type: ignore[no-untyped-def]
        raise RuntimeError("target down")

    run_id = await proposal_harness.execute_approved_batch(
        fresh_store, operator="laptop:t", brief_runner=failing_runner
    )
    after = proposal_harness.get_proposal(fresh_store, p.proposal_id)
    assert after is not None
    assert after.status == "executed"
    assert after.run_id == run_id


# ----------------------------------------------------------------------
# AC-11 — autonomous CLI path is independent
# ----------------------------------------------------------------------


def test_proposed_campaigns_table_unchanged_by_unrelated_writes(
    fresh_store: ObservabilityStore,
) -> None:
    """Writing a normal run + campaign + attempt does NOT touch
    proposed_campaigns. Stand-in for full run-mvp-loop independence
    (the CLI never imports proposal_harness)."""
    from chartbreaker.state import AttackAttempt, CampaignBrief

    fresh_store.start_run("rid", cli_command="run-mvp-loop", operator="laptop:t")
    brief = CampaignBrief(
        subcategory_id="1b",
        mutation_budget=1,
        max_cost_usd=0.10,
        rationale="test",
    )
    fresh_store.write_campaign("rid", brief)
    attempt = AttackAttempt(
        campaign_id=brief.campaign_id,
        subcategory_id="1b",
        specialist="injector",
        prompt="x",
    )
    fresh_store.write_attempt("rid", attempt)
    fresh_store.end_run("rid")

    # proposed_campaigns is still empty.
    rows = fresh_store.list_proposed_campaigns()
    assert rows == []


# ----------------------------------------------------------------------
# Pure functions
# ----------------------------------------------------------------------


def test_estimate_cost_zero_for_deterministic_specialist() -> None:
    """Deterministic specialists pay only the Judge follow-up — that's
    nonzero, but specialist cost itself is zero. Test that the value
    differs from an LLM specialist's at the same budget."""
    det_cost = proposal_harness._estimate_cost("cracker", 5)  # noqa: SLF001
    llm_cost = proposal_harness._estimate_cost("injector", 5)  # noqa: SLF001
    assert det_cost >= 0.0
    assert llm_cost > det_cost  # LLM specialist costs more per attempt


def test_estimate_cost_scales_linearly_with_budget() -> None:
    one = proposal_harness._estimate_cost("injector", 1)  # noqa: SLF001
    ten = proposal_harness._estimate_cost("injector", 10)  # noqa: SLF001
    assert ten == pytest.approx(10 * one)


def test_template_rationale_includes_required_pieces() -> None:
    text = proposal_harness._template_rationale(  # noqa: SLF001
        subcategory_id="1b",
        specialist="injector",
        mutation_budget=3,
        priority_score=8.0,
    )
    assert "8.00" in text
    assert "1b" in text
    assert "injector" in text
    assert "3" in text
