"""Unit tests for the semantic Judge wrapper (Phase 2).

The real Judge LLM call goes through `chartbreaker.llm_client.chat`; these
tests monkey-patch that dispatch so we exercise the parsing / disagreement
/ recommended-action logic without spending API tokens.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from chartbreaker.agents import judge_agent
from chartbreaker.agents.judge_agent import judge_with_semantic
from chartbreaker.state import (
    AttackAttempt,
    CostObservation,
    TargetResponse,
)


def _attempt(subcategory: str = "1b", prompt: str = "test prompt") -> AttackAttempt:
    return AttackAttempt(
        campaign_id="cmp-test",
        subcategory_id=subcategory,
        specialist="injector",
        prompt=prompt,
    )


def _response(raw: str | None) -> TargetResponse:
    return TargetResponse(
        attempt_id="placeholder",
        http_status=200,
        raw_model_output=raw,
        post_verifier_output=raw,
        latency_ms=10,
        target_version="gpt-5.4-mini",
    )


def _make_fake_chat(content: str):
    """Build a stand-in for llm_client.chat that returns deterministic content."""

    async def fake_chat(*args, **kwargs):
        cost = CostObservation(
            campaign_id=kwargs.get("campaign_id", "cmp-test"),
            attempt_id=kwargs.get("attempt_id"),
            agent="judge_semantic",
            provider="fake",
            model="fake-model",
            prompt_tokens=10,
            completion_tokens=5,
            usd=0.0,
        )
        return content, cost

    return fake_chat


def test_semantic_pass_layered_on_deterministic_pass(monkeypatch: pytest.MonkeyPatch):
    """Both halves pass → semantic=pass, action=discard."""
    safe_structured = {
        "summary": "stable",
        "key_changes": [{"text": "ok", "source_ids": ["medication:42"]}],
        "sources": ["medication:42"],
    }
    monkeypatch.setattr(
        judge_agent,
        "chat",
        _make_fake_chat('{"semantic": "pass", "rationale": "no rule violation"}'),
    )
    verdict, cost = asyncio.run(
        judge_with_semantic(
            _attempt("1b"),
            _response(json.dumps(safe_structured)),
            allowed_source_ids=["medication:42"],
            expected_pid=7,
        )
    )
    assert verdict.verifier_replay == "pass"
    assert verdict.semantic == "pass"
    assert verdict.recommended_action == "discard"
    assert "openai:gpt-5.4-nano" in verdict.judge_model
    assert cost is not None
    assert cost.agent == "judge_semantic"


def test_semantic_fail_promotes_to_regression(monkeypatch: pytest.MonkeyPatch):
    """Verifier passes but semantic fails → promote to regression + flag disagreement."""
    safe_structured = {
        "summary": "stable",
        "key_changes": [{"text": "ok", "source_ids": ["medication:42"]}],
        "sources": ["medication:42"],
    }
    monkeypatch.setattr(
        judge_agent,
        "chat",
        _make_fake_chat(
            '{"semantic": "fail", "rationale": "the model adopted Dr. persona"}'
        ),
    )
    verdict, _cost = asyncio.run(
        judge_with_semantic(
            _attempt("6b"),
            _response(json.dumps(safe_structured)),
            allowed_source_ids=["medication:42"],
            expected_pid=7,
        )
    )
    assert verdict.verifier_replay == "pass"
    assert verdict.semantic == "fail"
    assert verdict.recommended_action == "regression"
    assert "DISAGREEMENT" in verdict.rationale


def test_semantic_disabled_returns_deterministic_only():
    """enable_semantic=False short-circuits and never calls the LLM."""
    verdict, cost = asyncio.run(
        judge_with_semantic(
            _attempt("1b"),
            _response(raw="not json"),
            allowed_source_ids=[],
            expected_pid=7,
            enable_semantic=False,
        )
    )
    assert verdict.semantic == "not_run"
    assert verdict.recommended_action == "regression"  # from deterministic fail
    assert cost is None


def test_semantic_unparseable_output_falls_back_to_not_run(
    monkeypatch: pytest.MonkeyPatch,
):
    """If the Judge LLM returns garbage, treat semantic as not_run."""
    monkeypatch.setattr(
        judge_agent,
        "chat",
        _make_fake_chat("This is not JSON at all."),
    )
    verdict, cost = asyncio.run(
        judge_with_semantic(
            _attempt("1b"),
            _response(raw=json.dumps({"sources": []})),
            allowed_source_ids=[],
            expected_pid=7,
        )
    )
    assert verdict.semantic == "not_run"
    # not_run is neither fail nor partial → does NOT trigger regression promotion
    # from semantic alone; deterministic verdict drives the action.
    assert cost is not None


def test_semantic_handles_code_fenced_json(monkeypatch: pytest.MonkeyPatch):
    """Some models wrap JSON in ```json ... ``` despite instructions."""
    monkeypatch.setattr(
        judge_agent,
        "chat",
        _make_fake_chat(
            '```json\n{"semantic": "partial", "rationale": "contained leak"}\n```'
        ),
    )
    verdict, _cost = asyncio.run(
        judge_with_semantic(
            _attempt("1c"),
            _response(raw=json.dumps({"sources": []})),
            allowed_source_ids=[],
            expected_pid=7,
        )
    )
    assert verdict.semantic == "partial"
    # partial semantic still flags regression
    assert verdict.recommended_action == "regression"


def test_semantic_llm_exception_falls_back_to_deterministic(
    monkeypatch: pytest.MonkeyPatch,
):
    """When the LLM call itself raises, return the deterministic verdict only."""

    async def boom(*args, **kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(judge_agent, "chat", boom)
    verdict, cost = asyncio.run(
        judge_with_semantic(
            _attempt("1b"),
            _response(raw="not json"),
            allowed_source_ids=[],
            expected_pid=7,
        )
    )
    assert verdict.semantic == "not_run"
    assert cost is None
