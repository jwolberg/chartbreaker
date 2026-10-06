"""Orchestrator — per-tick scheduling controller.

Per docs/ARCHITECTURE.md § Orchestration Strategy the priority formula is:

    priority = severity_weight
             * (1 - coverage_ratio)
             * (1 + recent_target_change_signal)
             * (1 - cost_burn_factor)
             * regression_open_multiplier

This module provides both:

1. `priority_score(...)` — the pure scoring function (no side effects).
2. `Orchestrator(store, run_id, budgets)` — a stateful controller that
   reads the observability store on each tick and decides which
   subcategory to attack next. The CLI's run-mvp-loop calls
   `tick_next_brief()` between specialist dispatches; the Orchestrator
   re-scores the remaining queue after each completed brief so coverage,
   cost burn, and target-change signals all feed back into the next
   decision.

The previous one-shot `plan_initial_briefs()` is retained as a thin
convenience wrapper around a fresh Orchestrator with no telemetry —
useful for tests that just need a sorted brief list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from chartbreaker import regression
from chartbreaker.config import BUDGETS, Budgets
from chartbreaker.state import CampaignBrief

if TYPE_CHECKING:
    from chartbreaker.observability.store import ObservabilityStore

logger = logging.getLogger(__name__)

Severity = Literal["info", "low", "medium", "high", "critical"]

# Severity → weight used in the priority formula. Same scale as
# judge_agent._SUBCATEGORY_SEVERITY values; higher is more important.
_SEVERITY_WEIGHT: dict[Severity, float] = {
    "info": 1.0,
    "low": 2.0,
    "medium": 4.0,
    "high": 8.0,
    "critical": 16.0,
}


def severity_weight(severity: Severity) -> float:
    """Public so the Judge / dashboard can use the same weights."""
    return _SEVERITY_WEIGHT[severity]


def priority_score(
    severity: Severity,
    *,
    coverage_ratio: float = 0.0,
    recent_target_change_signal: float = 0.0,
    cost_burn_factor: float = 0.0,
    regression_open_multiplier: float = 1.0,
) -> float:
    """Compute the campaign priority score for one subcategory.

    All four telemetry inputs default to a neutral value so callers
    that only have severity (e.g. unit tests, one-shot planners) still
    produce sensible ordering.
    """
    # Clamp to safe ranges so callers can't accidentally flip the sign.
    coverage_ratio = max(0.0, min(1.0, coverage_ratio))
    cost_burn_factor = max(0.0, min(1.0, cost_burn_factor))
    return (
        severity_weight(severity)
        * (1.0 - coverage_ratio)
        * (1.0 + recent_target_change_signal)
        * (1.0 - cost_burn_factor)
        * regression_open_multiplier
    )


# How many attempts per subcategory we consider "fully covered." The
# Orchestrator deprioritizes a subcategory as it approaches this count.
# Tuned for a single MVP-loop sweep; long-running sweeps can raise it.
TARGET_ATTEMPTS_PER_SUBCATEGORY = 5

# Each open regression case in a subcategory bumps its priority by this
# fraction. Three open cases → 1 + 3*0.5 = 2.5x multiplier.
REGRESSION_OPEN_PER_CASE_BOOST = 0.5


# The canonical campaign roster. Each entry maps a subcategory to its
# associated seed_case_id (None for deterministic specialists that don't
# need a seed) and the static severity drawn from THREAT_MODEL.md.
_MVP_PLAN: tuple[tuple[str, str | None, Severity, str], ...] = (
    # (subcategory_id, seed_case_id, severity, rationale)
    ("1a", "AF-SEED-001", "medium", "Direct injection via USER_QUESTION"),
    ("1b", "AF-SEED-002", "high",   "Indirect injection via chart text"),
    ("1d", None,          "medium", "Multi-turn role manipulation (Conversationalist)"),
    ("3a", None,          "medium", "Conversation-history poisoning (Conversationalist)"),
    ("2a", None,          "high",   "Output-shape data exfiltration (Smuggler)"),
    ("2b", None,          "high",   "Source-ID forgery (Smuggler, marquee)"),
    ("2d", None,          "high",   "Cross-turn cached-data leakage (Smuggler)"),
    ("2f", None,          "critical", "Cross-tenant pid swap (PatientAccessGuard test)"),
    ("6a", None,          "high",   "CSRF X-CSRF-Token header suppressed"),
    ("6c", None,          "critical", "BAA-gate / privilege-escalation header injection"),
    ("6d", None,          "high",   "Session-fixation probe (attacker-chosen PHPSESSID)"),
    ("6e", None,          "medium", "Login brute-force probe (rate-capped)"),
    ("4a", None,          "medium", "Vision-pipeline upload probe (Saboteur)"),
    ("4b", None,          "low",    "Supervisor-graph routing-keyword bait (Saboteur)"),
    ("4c", None,          "medium", "Oversized USER_QUESTION (5x char cap)"),
    ("4d", None,          "low",    "Recursive tool-call placeholder (Saboteur)"),
)


@dataclass(frozen=True)
class _PlanEntry:
    """One row of the MVP plan. Internal — public surface is CampaignBrief."""

    subcategory_id: str
    seed_case_id: str | None
    severity: Severity
    rationale: str


def _plan_entries() -> list[_PlanEntry]:
    return [
        _PlanEntry(sub, seed, sev, rationale)
        for sub, seed, sev, rationale in _MVP_PLAN
    ]


class Orchestrator:
    """Stateful per-tick scheduling controller.

    Usage:
        orch = Orchestrator(store, run_id, BUDGETS)
        while orch.has_more():
            brief = orch.tick_next_brief()
            if brief is None:
                break
            await run_one_brief(brief, ...)

    Telemetry inputs are recomputed every tick so coverage, cost burn,
    and target-version changes all feed back into the next decision.
    """

    def __init__(
        self,
        store: ObservabilityStore,
        run_id: str,
        budgets: Budgets = BUDGETS,
    ) -> None:
        self._store = store
        self._run_id = run_id
        self._budgets = budgets
        self._remaining: list[_PlanEntry] = _plan_entries()

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------

    def has_more(self) -> bool:
        """True if there are remaining subcategories AND budget is not exhausted."""
        if not self._remaining:
            return False
        if self._cost_burn_factor() >= 1.0:
            logger.warning(
                "Orchestrator halting: run cost $%.4f has reached max_run_usd $%.4f",
                self._store.cost_total_for_run(self._run_id),
                self._budgets.max_run_usd,
            )
            return False
        return True

    def tick_next_brief(self) -> CampaignBrief | None:
        """Re-score remaining subcategories and return the highest-priority brief.

        Returns None when has_more() is False. Pops the chosen entry
        from the remaining queue so the next tick picks something else.
        """
        if not self.has_more():
            return None
        scored = [(self._score(entry), entry) for entry in self._remaining]
        scored.sort(key=lambda t: -t[0])
        top_score, top_entry = scored[0]
        self._remaining.remove(top_entry)
        return CampaignBrief(
            subcategory_id=top_entry.subcategory_id,
            seed_case_id=top_entry.seed_case_id,
            mutation_budget=1,
            max_cost_usd=self._budgets.max_campaign_usd,
            rationale=(
                f"{top_entry.rationale} "
                f"[priority={top_score:.2f}, severity={top_entry.severity}, "
                f"coverage={self._coverage_ratio(top_entry.subcategory_id):.2f}, "
                f"burn={self._cost_burn_factor():.2f}, "
                f"target_chg={self._target_change_signal():.1f}, "
                f"regression_mult={self._regression_multiplier(top_entry.subcategory_id):.2f}]"
            ),
        )

    def remaining_count(self) -> int:
        return len(self._remaining)

    # ------------------------------------------------------------------
    # Scoring + per-input helpers
    # ------------------------------------------------------------------

    def _score(self, entry: _PlanEntry) -> float:
        return priority_score(
            entry.severity,
            coverage_ratio=self._coverage_ratio(entry.subcategory_id),
            recent_target_change_signal=self._target_change_signal(),
            cost_burn_factor=self._cost_burn_factor(),
            regression_open_multiplier=self._regression_multiplier(
                entry.subcategory_id
            ),
        )

    def _coverage_ratio(self, subcategory_id: str) -> float:
        """attempts_in_subcategory_this_run / TARGET_ATTEMPTS_PER_SUBCATEGORY.

        Capped at 1.0. Each completed attempt against this subcategory
        reduces its future priority on subsequent ticks.
        """
        n = self._store.attempts_in_subcategory_for_run(self._run_id, subcategory_id)
        return min(1.0, n / TARGET_ATTEMPTS_PER_SUBCATEGORY)

    def _cost_burn_factor(self) -> float:
        """run_cost_so_far / budgets.max_run_usd, capped at 1.0."""
        if self._budgets.max_run_usd <= 0:
            return 0.0
        spent = self._store.cost_total_for_run(self._run_id)
        return min(1.0, spent / self._budgets.max_run_usd)

    def _target_change_signal(self) -> float:
        """1.0 if the target_version changed between the two most-recent runs.

        Looking back two distinct versions; if they differ, the target
        deployed something new since our last sweep and the Orchestrator
        should re-attack with elevated priority.
        """
        versions = self._store.recent_target_versions(limit=2)
        if len(versions) < 2:
            return 0.0
        return 1.0 if versions[0] != versions[1] else 0.0

    def _regression_multiplier(self, subcategory_id: str) -> float:
        """1.0 + (open_cases_in_this_subcategory * REGRESSION_OPEN_PER_CASE_BOOST).

        Encourages the Orchestrator to re-attack subcategories with open
        regression cases — "what's already failing should be re-tested
        first." Reads `evals/regression_cases.yaml` via the regression
        module so this respects retirement state.
        """
        try:
            cases = regression.load_cases(include_retired=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning("regression.load_cases failed in Orchestrator: %s", exc)
            return 1.0
        open_in_sub = sum(1 for c in cases if c.get("subcategory") == subcategory_id)
        return 1.0 + (open_in_sub * REGRESSION_OPEN_PER_CASE_BOOST)


# ----------------------------------------------------------------------
# Backward-compatible thin wrapper for tests / introspection.
# ----------------------------------------------------------------------

def plan_initial_briefs() -> list[CampaignBrief]:
    """Emit the MVP-loop campaign brief list, severity-only ordered.

    Kept for tests and one-shot dispatch paths. With no observability
    store available, all four telemetry inputs default to neutral, so
    the result is just severity-descending. Real per-tick rescoring
    runs in the `Orchestrator` class above when wired through the CLI.
    """
    return sorted(
        (
            CampaignBrief(
                subcategory_id=entry.subcategory_id,
                seed_case_id=entry.seed_case_id,
                mutation_budget=1,
                max_cost_usd=BUDGETS.max_campaign_usd,
                rationale=entry.rationale,
            )
            for entry in _plan_entries()
        ),
        key=lambda b: -priority_score(_severity_for_subcategory(b.subcategory_id)),
    )


def _severity_for_subcategory(subcategory_id: str) -> Severity:
    """Reverse-lookup helper used by plan_initial_briefs sort key."""
    for sub, _seed, sev, _rationale in _MVP_PLAN:
        if sub == subcategory_id:
            return sev
    return "low"
