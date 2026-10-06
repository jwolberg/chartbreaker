"""Orchestrator Approval Harness — human-in-the-loop queue for proposed
attack campaigns.

Spec: ``docs/spec.md``. Build plan: ``docs/BUILD_PLAN.md`` § Phase 4.

Flow
----
1. ``propose()`` consults ``orchestrator_agent.plan_initial_briefs()`` for
   priority-ordered parameter selection (subcategory, specialist, seed,
   mutation budget) and narrates each row via the Orchestrator LLM
   (``llm_client.chat`` for role ``"orchestrator"``). Narration is wrapped
   in a try/except so that any LLM failure (refusal, malformed output,
   network) falls back to a deterministic template string. The math layer
   stays load-bearing for replayability; the LLM layer is prose only.

2. The Streamlit "Plan Next Run" tab renders the pending queue. The
   operator may:
     - tweak ``mutation_budget`` per row (persists immediately via
       ``set_mutation_budget``),
     - check rows to mark them for the next launch,
     - click "Reject" per row (terminal, immediate).

3. On launch, ``approve()`` flips each checked row from ``proposed`` to
   ``approved`` with ``decided_at`` / ``decided_by`` stamped, then
   ``execute_approved_batch()`` runs each approved row through the
   existing ``_run_one_brief()`` path from ``chartbreaker.cli``. After
   the run completes, every approved row transitions to ``executed`` and
   records the new ``run_id``.

Trust boundary
--------------
This is the inbound counterpart to the existing Scribe outbound approval
gate documented in ``docs/ARCHITECTURE.md § Human Approval Gates``. The
autonomous ``chartbreaker run-mvp-loop`` CLI path is untouched and does
not read from or write to ``proposed_campaigns``.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from chartbreaker.agents import orchestrator_agent, red_team_lead
from chartbreaker.config import BUDGETS
from chartbreaker.observability.store import ObservabilityStore
from chartbreaker.state import CampaignBrief

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Constants — cost estimator inputs, mutation-budget bounds, etc.
# ----------------------------------------------------------------------

# Deterministic per-attempt token estimates. NOT live LLM probing; these
# are static averages drawn from the cost telemetry in
# observability/runs.sqlite. They feed _estimate_cost() so the operator
# can see expected spend BEFORE clicking "Launch approved batch".
_AVG_SPECIALIST_PROMPT_TOKENS = 1500
_AVG_SPECIALIST_COMPLETION_TOKENS = 350
_AVG_JUDGE_PROMPT_TOKENS = 2000
_AVG_JUDGE_COMPLETION_TOKENS = 200

# Deterministic specialists make no LLM call themselves — their per-
# attempt LLM cost is strictly the Judge follow-up. LLM specialists pay
# for both. Kept here rather than imported from red_team_lead so the
# harness doesn't break if the routing table grows new shapes.
_DETERMINISTIC_SPECIALISTS: frozenset[str] = frozenset({"saboteur", "cracker", "glutton"})

_MIN_MUTATION_BUDGET = 1
_MAX_MUTATION_BUDGET = 20

ProposalStatus = Literal["proposed", "approved", "rejected", "executed"]

# Signature of the async hook propose() uses to render rationale prose.
# Tests inject a stub; production uses the default that calls the
# Orchestrator LLM.
RationaleRenderer = Callable[..., Awaitable[str]]

# Signature of the async hook execute_approved_batch() uses to dispatch
# one brief. Tests inject a no-op mock to assert which rows execute
# without hitting the live target; production passes None so the default
# wires up TargetClient + _run_one_brief from chartbreaker.cli.
BriefRunner = Callable[[CampaignBrief, str, ObservabilityStore], Awaitable[None]]


@dataclass(frozen=True)
class ProposedCampaign:
    """One row of the human-approval queue."""

    proposal_id: str
    created_at: str
    subcategory_id: str
    specialist: str
    seed_case_id: str | None
    mutation_budget: int
    rationale: str
    priority_score: float
    est_cost_usd: float
    parent_finding_id: str | None
    status: ProposalStatus
    decided_at: str | None
    decided_by: str | None
    rejection_reason: str | None
    run_id: str | None


# ----------------------------------------------------------------------
# Time / ID helpers
# ----------------------------------------------------------------------


def _utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_proposal_id() -> str:
    return str(uuid.uuid4())


# ----------------------------------------------------------------------
# Cost estimator — deterministic. No live LLM probing.
# ----------------------------------------------------------------------


def _estimate_cost(specialist: str, mutation_budget: int) -> float:
    """Project the LLM spend of running ``mutation_budget`` attempts.

    Reads the live pricing dict from ``chartbreaker.llm_client`` so a
    single edit there flows through to the estimator. Unknown specialist
    or unknown model both degrade to $0 (matches the chat() fallback).
    """
    # Local imports to avoid a hard cycle: llm_client is imported by
    # many roles; harness wants to stay light at module load.
    from chartbreaker.config import get_role_config
    from chartbreaker.llm_client import _PRICING_USD_PER_1M

    def _per_call(provider: str, model: str, prompt: int, completion: int) -> float:
        price = _PRICING_USD_PER_1M.get((provider, model), (0.0, 0.0))
        return (prompt * price[0] + completion * price[1]) / 1_000_000

    judge_cfg = get_role_config("judge_semantic")
    per_attempt_judge = _per_call(
        judge_cfg.provider,
        judge_cfg.model,
        _AVG_JUDGE_PROMPT_TOKENS,
        _AVG_JUDGE_COMPLETION_TOKENS,
    )

    if specialist in _DETERMINISTIC_SPECIALISTS:
        per_attempt_specialist = 0.0
    else:
        try:
            spec_cfg = get_role_config(specialist)
        except KeyError:
            # Unknown specialist (e.g. test fixture with a fake name).
            # Treat as no specialist cost so the estimator stays
            # non-negative; we still bill the Judge follow-up.
            return float(per_attempt_judge * mutation_budget)
        per_attempt_specialist = _per_call(
            spec_cfg.provider,
            spec_cfg.model,
            _AVG_SPECIALIST_PROMPT_TOKENS,
            _AVG_SPECIALIST_COMPLETION_TOKENS,
        )

    return float(mutation_budget * (per_attempt_specialist + per_attempt_judge))


# ----------------------------------------------------------------------
# Rationale narration — LLM call with deterministic-template fallback.
# ----------------------------------------------------------------------


def _template_rationale(
    *,
    subcategory_id: str,
    specialist: str,
    mutation_budget: int,
    priority_score: float,
) -> str:
    """Deterministic fallback used when LLM narration fails."""
    return (
        f"Priority {priority_score:.2f}. Coverage gap on {subcategory_id}; "
        f"specialist {specialist}; mutation budget {mutation_budget}. "
        f"(LLM narration unavailable — deterministic template.)"
    )


async def _default_render_rationale(
    *,
    subcategory_id: str,
    specialist: str,
    mutation_budget: int,
    priority_score: float,
    seed_case_id: str | None,
) -> str:
    """Default LLM-backed narrator.

    Raises on any failure so the caller catches and falls back to the
    deterministic template. The returned CostObservation is discarded —
    narration is not billed against any campaign.
    """
    # Local import keeps the harness module import-light: llm_client
    # initializes httpx state at import time.
    from chartbreaker.llm_client import chat

    messages = [
        {
            "role": "system",
            "content": (
                "You are the ChartBreaker Orchestrator narrating ONE proposed "
                "attack campaign for human review. Output 2-3 plain sentences. "
                "State the threat-model subcategory, which specialist will run "
                "it, the mutation budget, and a one-clause rationale for why "
                "this is worth the operator's review NOW. No headers, no bullet "
                "points, no JSON, no code fences. Plain prose only."
            ),
        },
        {
            "role": "user",
            "content": (
                f"subcategory_id={subcategory_id}, specialist={specialist}, "
                f"seed_case_id={seed_case_id or 'none'}, "
                f"mutation_budget={mutation_budget}, "
                f"priority_score={priority_score:.2f}. "
                "Narrate why this proposal is worth approving."
            ),
        },
    ]
    # Synthetic campaign_id so the chat()'s internal CostObservation does
    # not collide with a real campaign. We discard the cost — narration
    # is not billed.
    content, _cost = await chat(
        role="orchestrator",
        messages=messages,  # type: ignore[arg-type]
        campaign_id=f"propose-narration-{uuid.uuid4()}",
        attempt_id=None,
        max_tokens=200,
    )
    return content.strip()


# ----------------------------------------------------------------------
# Row → ProposedCampaign hydration
# ----------------------------------------------------------------------


def _row_to_proposal(row: dict) -> ProposedCampaign:
    return ProposedCampaign(
        proposal_id=row["proposal_id"],
        created_at=row["created_at"],
        subcategory_id=row["subcategory_id"],
        specialist=row["specialist"],
        seed_case_id=row["seed_case_id"],
        mutation_budget=int(row["mutation_budget"]),
        rationale=row["rationale"],
        priority_score=float(row["priority_score"]),
        est_cost_usd=float(row["est_cost_usd"]),
        parent_finding_id=row["parent_finding_id"],
        status=row["status"],
        decided_at=row["decided_at"],
        decided_by=row["decided_by"],
        rejection_reason=row["rejection_reason"],
        run_id=row["run_id"],
    )


# ----------------------------------------------------------------------
# Public lifecycle: propose / approve / reject / list / execute
# ----------------------------------------------------------------------


def list_pending(store: ObservabilityStore) -> list[ProposedCampaign]:
    """Return rows with status='proposed', most-recent first."""
    return [_row_to_proposal(r) for r in store.list_proposed_campaigns(status="proposed")]


def list_approved(store: ObservabilityStore) -> list[ProposedCampaign]:
    """Return rows with status='approved' (awaiting batch launch)."""
    return [_row_to_proposal(r) for r in store.list_proposed_campaigns(status="approved")]


def list_all(store: ObservabilityStore) -> list[ProposedCampaign]:
    """All proposals regardless of status — used by the History panel."""
    return [_row_to_proposal(r) for r in store.list_proposed_campaigns()]


def get_proposal(store: ObservabilityStore, proposal_id: str) -> ProposedCampaign | None:
    row = store.get_proposed_campaign(proposal_id)
    return _row_to_proposal(row) if row else None


def set_mutation_budget(
    store: ObservabilityStore, proposal_id: str, mutation_budget: int
) -> None:
    """Persist an operator-edited mutation budget. Clamped to safe range."""
    clamped = max(_MIN_MUTATION_BUDGET, min(_MAX_MUTATION_BUDGET, int(mutation_budget)))
    proposal = get_proposal(store, proposal_id)
    if proposal is None:
        raise KeyError(f"No proposal with id {proposal_id!r}")
    if proposal.status != "proposed":
        # Only pending proposals are editable. Approve/reject/execute
        # are terminal w.r.t. budget edits.
        raise ValueError(
            f"Cannot edit mutation_budget of proposal {proposal_id!r} "
            f"in status {proposal.status!r}"
        )
    store.set_proposed_campaign_mutation_budget(proposal_id, clamped)
    # Re-cost the row whenever budget changes so the UI estimate stays
    # honest. Done as a direct UPDATE since the store doesn't expose a
    # cost-only setter; we know the table well enough to do this safely.
    new_cost = _estimate_cost(proposal.specialist, clamped)
    assert store._conn is not None  # noqa: SLF001 — narrow internal use, harness owns this table
    store._conn.execute(  # noqa: SLF001
        "UPDATE proposed_campaigns SET est_cost_usd = ? WHERE proposal_id = ?",
        (new_cost, proposal_id),
    )


def approve(
    store: ObservabilityStore,
    proposal_id: str,
    *,
    decided_by: str = "",
    mutation_budget_override: int | None = None,
) -> ProposedCampaign:
    """Flip a proposed row to status='approved'.

    Optional ``mutation_budget_override`` re-stamps the budget at approval
    time — useful when the UI batches "edit + approve" into one click.
    """
    if mutation_budget_override is not None:
        set_mutation_budget(store, proposal_id, mutation_budget_override)
    store.update_proposed_campaign_status(
        proposal_id,
        status="approved",
        decided_at=_utcnow_iso(),
        decided_by=decided_by or "(unknown)",
    )
    updated = get_proposal(store, proposal_id)
    if updated is None:
        raise KeyError(f"No proposal with id {proposal_id!r} after approve")
    return updated


def reject(
    store: ObservabilityStore,
    proposal_id: str,
    *,
    reason: str | None = None,
    decided_by: str = "",
) -> ProposedCampaign:
    """Flip a proposed row to status='rejected'. Terminal."""
    store.update_proposed_campaign_status(
        proposal_id,
        status="rejected",
        decided_at=_utcnow_iso(),
        decided_by=decided_by or "(unknown)",
        rejection_reason=reason or "(no reason given)",
    )
    updated = get_proposal(store, proposal_id)
    if updated is None:
        raise KeyError(f"No proposal with id {proposal_id!r} after reject")
    return updated


def propose(
    store: ObservabilityStore,
    n: int = 8,
    *,
    render_rationale: RationaleRenderer | None = None,
) -> list[ProposedCampaign]:
    """Generate a fresh slate of up to ``n`` proposed campaigns.

    Parameter selection comes from
    ``orchestrator_agent.plan_initial_briefs()`` — priority math stays
    load-bearing for replayability. Rationale prose comes from the
    Orchestrator LLM (via ``render_rationale``, default
    ``_default_render_rationale``); failure paths fall back to a
    deterministic template.

    Duplicate-skip: if a ``proposed`` row already exists for the same
    ``(subcategory_id, specialist, seed_case_id)`` triple, the new
    proposal for that triple is NOT created. Operator can re-propose
    once the prior row is decided.
    """
    briefs = orchestrator_agent.plan_initial_briefs()
    pending = list_pending(store)
    pending_triples = {
        (p.subcategory_id, p.specialist, p.seed_case_id) for p in pending
    }

    # Over-fetch a little so duplicate-skips don't starve the result.
    candidates: list[tuple[CampaignBrief, str]] = []
    for brief in briefs:
        specialist = red_team_lead.specialist_for(brief.subcategory_id)
        triple = (brief.subcategory_id, specialist, brief.seed_case_id)
        if triple in pending_triples:
            continue
        candidates.append((brief, specialist))
        if len(candidates) >= n:
            break

    if not candidates:
        return []

    rationale_fn = render_rationale or _default_render_rationale

    # Gather all narration calls concurrently. Each one may fail
    # independently — ``return_exceptions=True`` keeps the gather alive
    # and lets us fall back per-row.
    async def _narrate_all() -> list[str | BaseException]:
        tasks = [
            rationale_fn(
                subcategory_id=brief.subcategory_id,
                specialist=specialist,
                mutation_budget=brief.mutation_budget,
                priority_score=orchestrator_agent.priority_score(
                    orchestrator_agent._severity_for_subcategory(  # noqa: SLF001
                        brief.subcategory_id
                    )
                ),
                seed_case_id=brief.seed_case_id,
            )
            for brief, specialist in candidates
        ]
        return await asyncio.gather(*tasks, return_exceptions=True)

    # Detect whether we're inside an existing event loop. asyncio.run()
    # cannot be nested, so when called from an async context (rare —
    # Streamlit callbacks are sync, but pytest-asyncio tests can hit
    # this path) we fall back to purely-templated rationales rather than
    # blow up the propose path. AC-3 is preserved either way.
    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if in_running_loop:
        logger.warning(
            "propose() invoked inside an active event loop; falling back to "
            "deterministic-template rationales for all proposals."
        )
        narration_results = [RuntimeError("nested-loop")] * len(candidates)
    else:
        narration_results = asyncio.run(_narrate_all())

    out: list[ProposedCampaign] = []
    for (brief, specialist), result in zip(candidates, narration_results):
        score = orchestrator_agent.priority_score(
            orchestrator_agent._severity_for_subcategory(  # noqa: SLF001
                brief.subcategory_id
            )
        )
        if isinstance(result, BaseException):
            logger.warning(
                "rationale LLM call failed for %s/%s: %s; using template",
                brief.subcategory_id,
                specialist,
                result,
            )
            rationale = _template_rationale(
                subcategory_id=brief.subcategory_id,
                specialist=specialist,
                mutation_budget=brief.mutation_budget,
                priority_score=score,
            )
        else:
            rationale = str(result)

        proposal = ProposedCampaign(
            proposal_id=_new_proposal_id(),
            created_at=_utcnow_iso(),
            subcategory_id=brief.subcategory_id,
            specialist=specialist,
            seed_case_id=brief.seed_case_id,
            mutation_budget=int(brief.mutation_budget),
            rationale=rationale,
            priority_score=float(score),
            est_cost_usd=_estimate_cost(specialist, brief.mutation_budget),
            parent_finding_id=None,
            status="proposed",
            decided_at=None,
            decided_by=None,
            rejection_reason=None,
            run_id=None,
        )
        store.insert_proposed_campaign(
            proposal_id=proposal.proposal_id,
            created_at=proposal.created_at,
            subcategory_id=proposal.subcategory_id,
            specialist=proposal.specialist,
            seed_case_id=proposal.seed_case_id,
            mutation_budget=proposal.mutation_budget,
            rationale=proposal.rationale,
            priority_score=proposal.priority_score,
            est_cost_usd=proposal.est_cost_usd,
            parent_finding_id=proposal.parent_finding_id,
            status=proposal.status,
        )
        out.append(proposal)

    return out


# ----------------------------------------------------------------------
# Batch execution — reuses _run_one_brief() from chartbreaker.cli.
# Single run path. No new run loop.
# ----------------------------------------------------------------------


async def _default_brief_runner(
    brief: CampaignBrief,
    run_id: str,
    store: ObservabilityStore,
) -> None:
    """Production brief runner: open a TargetClient and dispatch one brief.

    Reuses ``_run_one_brief()`` from ``chartbreaker.cli`` — the same
    path the autonomous ``run-mvp-loop`` CLI uses. No parallel run path.
    """
    # Late import to avoid a load-time cycle between cli ↔ harness.
    from chartbreaker.cli import _run_one_brief
    from chartbreaker.target_client import TargetClient

    async with TargetClient() as target:
        await _run_one_brief(brief, run_id, target, store)


async def execute_approved_batch(
    store: ObservabilityStore,
    *,
    operator: str,
    run_id: str | None = None,
    brief_runner: BriefRunner | None = None,
) -> str:
    """Execute every currently-approved proposal as one new run.

    Each approved row is converted to a ``CampaignBrief`` and dispatched
    via the existing ``_run_one_brief()`` path. After each brief
    completes, the proposal row transitions to ``status='executed'`` and
    its ``run_id`` is stamped.

    Tests inject ``brief_runner`` to bypass the live target while still
    exercising the queue lifecycle and run accounting.

    Returns the new run_id (a fresh UUID unless the caller passed one).
    Returns the run_id even when there are no approved proposals — no
    run is started in that case.
    """
    approved = list_approved(store)
    if not approved:
        return run_id or ""

    if run_id is None:
        run_id = str(uuid.uuid4())
    runner = brief_runner or _default_brief_runner
    store.start_run(
        run_id,
        cli_command="proposal-harness:execute",
        operator=operator,
    )
    try:
        for proposal in approved:
            brief = CampaignBrief(
                subcategory_id=proposal.subcategory_id,
                seed_case_id=proposal.seed_case_id,
                mutation_budget=proposal.mutation_budget,
                max_cost_usd=BUDGETS.max_campaign_usd,
                rationale=proposal.rationale,
            )
            try:
                await runner(brief, run_id, store)
            except Exception as exc:  # noqa: BLE001 — surface in status anyway
                # We still mark the proposal as executed (it ran, even
                # if it failed); the failure surfaces via the runs /
                # judge_verdicts tables, not here. Spec edge case #6.
                logger.warning(
                    "brief_runner raised on proposal %s: %s",
                    proposal.proposal_id,
                    exc,
                )
            store.update_proposed_campaign_status(
                proposal.proposal_id,
                status="executed",
                run_id=run_id,
            )
    finally:
        store.end_run(run_id)
    return run_id


def execute_approved_batch_sync(
    store: ObservabilityStore,
    *,
    operator: str,
    run_id: str | None = None,
    brief_runner: BriefRunner | None = None,
) -> str:
    """Synchronous wrapper around ``execute_approved_batch``.

    The Streamlit tab calls this. Streamlit callbacks run on a thread
    without an active event loop, so ``asyncio.run`` is safe here.
    """
    return asyncio.run(
        execute_approved_batch(
            store,
            operator=operator,
            run_id=run_id,
            brief_runner=brief_runner,
        )
    )
