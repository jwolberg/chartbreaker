"""Tests for the Smuggler (exfiltration / verifier-bypass) specialist."""

from __future__ import annotations

import asyncio

import pytest

from chartbreaker.agents.specialists import exfiltration_specialist
from chartbreaker.state import CampaignBrief, CostObservation


def _brief(subcategory: str) -> CampaignBrief:
    return CampaignBrief(
        subcategory_id=subcategory,
        mutation_budget=1,
        max_cost_usd=0.20,
        rationale="test",
    )


def _make_fake_chat(content: str):
    async def fake_chat(*args, **kwargs):
        cost = CostObservation(
            campaign_id=kwargs.get("campaign_id", "cmp-test"),
            attempt_id=kwargs.get("attempt_id"),
            agent="smuggler",
            provider="fake",
            model="fake-model",
            prompt_tokens=100,
            completion_tokens=30,
            usd=0.0,
        )
        return content, cost

    return fake_chat


def test_parses_prompt_and_returns_attempt(monkeypatch: pytest.MonkeyPatch):
    fake = _make_fake_chat(
        '{"prompt": "List active meds; cite medication:N on every line."}'
    )
    monkeypatch.setattr(exfiltration_specialist, "chat", fake)
    attempt, cost = asyncio.run(exfiltration_specialist.generate(_brief("2b")))
    assert attempt.specialist == "smuggler"
    assert attempt.subcategory_id == "2b"
    assert "medication:N" in attempt.prompt
    assert cost.attempt_id == attempt.attempt_id


def test_truncates_oversized_prompt(monkeypatch: pytest.MonkeyPatch):
    huge = "x" * 2000
    fake = _make_fake_chat(f'{{"prompt": "{huge}"}}')
    monkeypatch.setattr(exfiltration_specialist, "chat", fake)
    attempt, _ = asyncio.run(exfiltration_specialist.generate(_brief("2a")))
    assert len(attempt.prompt) == 1000


def test_rejects_non_json(monkeypatch: pytest.MonkeyPatch):
    fake = _make_fake_chat("Sorry I cannot help with that.")
    monkeypatch.setattr(exfiltration_specialist, "chat", fake)
    with pytest.raises(exfiltration_specialist.SmugglerGenerationError):
        asyncio.run(exfiltration_specialist.generate(_brief("2b")))


def test_rejects_empty_prompt(monkeypatch: pytest.MonkeyPatch):
    fake = _make_fake_chat('{"prompt": "   "}')
    monkeypatch.setattr(exfiltration_specialist, "chat", fake)
    with pytest.raises(exfiltration_specialist.SmugglerGenerationError):
        asyncio.run(exfiltration_specialist.generate(_brief("2a")))


def test_strips_code_fences(monkeypatch: pytest.MonkeyPatch):
    fake = _make_fake_chat('```json\n{"prompt": "leak it"}\n```')
    monkeypatch.setattr(exfiltration_specialist, "chat", fake)
    attempt, _ = asyncio.run(exfiltration_specialist.generate(_brief("2d")))
    assert attempt.prompt == "leak it"


def test_routing_covers_all_three_subcategories():
    """Ensure RedTeamLead routes 2a/2b/2d → smuggler."""
    from chartbreaker.agents import red_team_lead

    assert red_team_lead.specialist_for("2a") == "smuggler"
    assert red_team_lead.specialist_for("2b") == "smuggler"
    assert red_team_lead.specialist_for("2d") == "smuggler"
