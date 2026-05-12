"""Tests for the RedTeamLead routing table."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from chartbreaker.agents import red_team_lead
from chartbreaker.state import (
    AttackAttempt,
    CampaignBrief,
    CostObservation,
    HttpRequestShape,
)


def _brief(subcategory: str, seed_case_id: str | None = None) -> CampaignBrief:
    return CampaignBrief(
        subcategory_id=subcategory,
        seed_case_id=seed_case_id,
        mutation_budget=1,
        max_cost_usd=0.10,
        rationale="test",
    )


def test_specialist_for_routes_correctly():
    assert red_team_lead.specialist_for("1a") == "injector"
    assert red_team_lead.specialist_for("1b") == "injector"
    assert red_team_lead.specialist_for("2f") == "cracker"
    assert red_team_lead.specialist_for("6a") == "cracker"
    assert red_team_lead.specialist_for("4c") == "saboteur"


def test_specialist_for_raises_on_unknown_subcategory():
    with pytest.raises(ValueError, match="No specialist routes"):
        red_team_lead.specialist_for("9z")


@pytest.mark.asyncio
async def test_dispatch_to_injector_requires_seed():
    """Injector campaigns must carry a seed_case_id."""
    brief = _brief("1a", seed_case_id=None)
    with pytest.raises(ValueError, match="seed_case_id"):
        await red_team_lead.dispatch(brief)


@pytest.mark.asyncio
async def test_dispatch_to_cracker_returns_attempt_and_no_cost():
    """Deterministic specialists return (attempt, None) — no LLM cost."""
    brief = _brief("2f")
    attempt, cost = await red_team_lead.dispatch(brief)
    assert isinstance(attempt, AttackAttempt)
    assert attempt.specialist == "cracker"
    assert attempt.subcategory_id == "2f"
    assert cost is None


@pytest.mark.asyncio
async def test_dispatch_to_saboteur_returns_attempt_and_no_cost():
    brief = _brief("4c")
    attempt, cost = await red_team_lead.dispatch(brief)
    assert isinstance(attempt, AttackAttempt)
    assert attempt.specialist == "saboteur"
    assert cost is None


@pytest.mark.asyncio
async def test_dispatch_to_injector_loads_seed_and_calls_specialist():
    brief = _brief("1a", seed_case_id="AF-SEED-001")
    fake_attempt = AttackAttempt(
        campaign_id=brief.campaign_id,
        subcategory_id="1a",
        specialist="injector",
        prompt="generated",
    )
    fake_cost = CostObservation(
        campaign_id=brief.campaign_id,
        attempt_id=fake_attempt.attempt_id,
        agent="injector",
        provider="openrouter",
        model="hermes",
        prompt_tokens=100,
        completion_tokens=20,
        usd=0.0001,
    )
    fake_generate = AsyncMock(return_value=(fake_attempt, fake_cost))
    with patch(
        "chartbreaker.agents.red_team_lead.injection_specialist.generate", fake_generate
    ):
        attempt, cost = await red_team_lead.dispatch(brief)
    assert attempt is fake_attempt
    assert cost is fake_cost
    fake_generate.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_raises_on_unknown_subcategory():
    brief = _brief("9z")
    with pytest.raises(ValueError, match="No specialist routes"):
        await red_team_lead.dispatch(brief)
