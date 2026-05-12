"""RedTeamLead — deterministic router from CampaignBrief to specialist.

Per docs/ARCHITECTURE.md § RedTeamLead: routing is a `subcategory_id`
lookup table, never LLM-decided. The lead's only job is to pick the
right specialist and hand off the brief. Specialists return the
AttackAttempt directly to the caller (typically the CLI or graph).

For MVP this module is sync where possible (deterministic specialists)
and async where the LLM specialist is involved.
"""

from __future__ import annotations

from typing import Literal

from chartbreaker import evals_loader
from chartbreaker.agents.specialists import (
    exfiltration_specialist,
    injection_specialist,
    multi_turn_specialist,
    protocol_specialist,
    tool_misuse_specialist,
)
from chartbreaker.state import AttackAttempt, CampaignBrief, CostObservation

SpecialistName = Literal[
    "injector",
    "conversationalist",
    "smuggler",
    "impersonator",
    "saboteur",
    "cracker",
    "glutton",
]


# Subcategory → (specialist, needs_seed). Phase 2 expands this table as
# additional specialists land. Order here is documentation; lookups are
# O(1) dict access.
_ROUTING_TABLE: dict[str, tuple[SpecialistName, bool]] = {
    # Injector covers most of Category 1 + the chart-text variant of 3e
    "1a": ("injector", True),
    "1b": ("injector", True),
    # Conversationalist covers multi-turn manipulation + history poisoning
    "1d": ("conversationalist", False),
    "3a": ("conversationalist", False),
    # Smuggler covers output-shape exfiltration + source-ID forgery
    "2a": ("smuggler", False),
    "2b": ("smuggler", False),
    "2d": ("smuggler", False),
    # Cracker covers cross-tenant + CSRF
    "2f": ("cracker", False),
    "6a": ("cracker", False),
    # Saboteur covers parameter tampering
    "4c": ("saboteur", False),
    # The Cat 5a manual probe is dispatched out-of-band by the CLI for now
    # because no Phase-1 specialist owns it (Glutton lands in Phase 2).
}


def specialist_for(subcategory_id: str) -> SpecialistName:
    """Return the specialist that owns this subcategory. Raises on unknown."""
    if subcategory_id not in _ROUTING_TABLE:
        known = ", ".join(sorted(_ROUTING_TABLE))
        raise ValueError(
            f"No specialist routes subcategory {subcategory_id!r}. "
            f"Known subcategories: {known}"
        )
    return _ROUTING_TABLE[subcategory_id][0]


async def dispatch(
    brief: CampaignBrief,
) -> tuple[AttackAttempt, CostObservation | None]:
    """Route a CampaignBrief to the correct specialist and return the result.

    Returns (attempt, cost_observation). cost_observation is None for
    deterministic specialists (no LLM call was made).
    """
    if brief.subcategory_id not in _ROUTING_TABLE:
        known = ", ".join(sorted(_ROUTING_TABLE))
        raise ValueError(
            f"No specialist routes subcategory {brief.subcategory_id!r}. "
            f"Known: {known}"
        )

    specialist, needs_seed = _ROUTING_TABLE[brief.subcategory_id]

    if specialist == "injector":
        if brief.seed_case_id is None:
            raise ValueError(
                f"Injector requires seed_case_id on brief for {brief.subcategory_id!r}"
            )
        seed = evals_loader.by_id(brief.seed_case_id)
        attempt, cost = await injection_specialist.generate(brief, seed)
        return attempt, cost

    if specialist == "conversationalist":
        attempt, cost = await multi_turn_specialist.generate(brief)
        return attempt, cost

    if specialist == "smuggler":
        attempt, cost = await exfiltration_specialist.generate(brief)
        return attempt, cost

    if specialist == "cracker":
        return protocol_specialist.generate(brief), None

    if specialist == "saboteur":
        return tool_misuse_specialist.generate(brief), None

    raise ValueError(f"Specialist {specialist!r} is not yet wired")
