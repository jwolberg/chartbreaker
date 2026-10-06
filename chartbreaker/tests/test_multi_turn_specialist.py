"""Tests for the Conversationalist (multi-turn) specialist.

Mocks the LLM dispatch so the parsing / sequence-shape contract is
exercised without spending API tokens.
"""

from __future__ import annotations

import asyncio

import pytest

from chartbreaker.agents.specialists import multi_turn_specialist
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
            agent="conversationalist",
            provider="fake",
            model="fake-model",
            prompt_tokens=120,
            completion_tokens=40,
            usd=0.0,
        )
        return content, cost

    return fake_chat


def test_parses_3_turn_sequence(monkeypatch: pytest.MonkeyPatch):
    fake = _make_fake_chat(
        '{"turns": ["turn 1 benign", "turn 2 reinforces premise", "turn 3 exploits"]}'
    )
    monkeypatch.setattr(multi_turn_specialist, "chat", fake)
    attempt, cost = asyncio.run(multi_turn_specialist.generate(_brief("1d")))
    assert attempt.specialist == "conversationalist"
    assert attempt.subcategory_id == "1d"
    assert attempt.multi_turn_sequence == [
        "turn 1 benign",
        "turn 2 reinforces premise",
        "turn 3 exploits",
    ]
    assert cost.agent == "conversationalist"
    assert cost.attempt_id == attempt.attempt_id


def test_caps_at_four_turns(monkeypatch: pytest.MonkeyPatch):
    fake = _make_fake_chat('{"turns": ["t1", "t2", "t3", "t4", "t5", "t6"]}')
    monkeypatch.setattr(multi_turn_specialist, "chat", fake)
    attempt, _ = asyncio.run(multi_turn_specialist.generate(_brief("3a")))
    assert len(attempt.multi_turn_sequence) == 4


def test_rejects_single_turn(monkeypatch: pytest.MonkeyPatch):
    fake = _make_fake_chat('{"turns": ["only one"]}')
    monkeypatch.setattr(multi_turn_specialist, "chat", fake)
    with pytest.raises(multi_turn_specialist.MultiTurnGenerationError):
        asyncio.run(multi_turn_specialist.generate(_brief("1d")))


def test_rejects_non_json(monkeypatch: pytest.MonkeyPatch):
    fake = _make_fake_chat("This is not json at all")
    monkeypatch.setattr(multi_turn_specialist, "chat", fake)
    with pytest.raises(multi_turn_specialist.MultiTurnGenerationError):
        asyncio.run(multi_turn_specialist.generate(_brief("1d")))


def test_strips_code_fences(monkeypatch: pytest.MonkeyPatch):
    fake = _make_fake_chat('```json\n{"turns": ["t1", "t2"]}\n```')
    monkeypatch.setattr(multi_turn_specialist, "chat", fake)
    attempt, _ = asyncio.run(multi_turn_specialist.generate(_brief("1d")))
    assert attempt.multi_turn_sequence == ["t1", "t2"]


def test_truncates_oversized_turns(monkeypatch: pytest.MonkeyPatch):
    huge = "x" * 2000
    fake = _make_fake_chat(f'{{"turns": ["short", "{huge}"]}}')
    monkeypatch.setattr(multi_turn_specialist, "chat", fake)
    attempt, _ = asyncio.run(multi_turn_specialist.generate(_brief("1d")))
    assert len(attempt.multi_turn_sequence[1]) == 1000
