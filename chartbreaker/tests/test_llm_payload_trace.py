"""Tests for the P2.5-T2 LLM payload trace.

The chat() function is normally network-bound. These tests patch the
network layer so the trace-writing path runs deterministically and we
can inspect the resulting JSONL file.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from chartbreaker import llm_client


async def _fake_post_with_retry(*args, **kwargs):
    """Drop-in for llm_client._post_with_retry."""
    return {
        "choices": [{"message": {"content": "fake response"}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    }


def _patch_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch dependencies so chat() runs without network or env."""
    monkeypatch.setattr(llm_client, "_post_with_retry", _fake_post_with_retry)
    monkeypatch.setattr(llm_client, "get_provider_api_key", lambda _p: "fake-key")


def test_trace_off_by_default_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_chat(monkeypatch)
    llm_client.disable_payload_trace()

    target = tmp_path / "trace.jsonl"
    asyncio.run(
        llm_client.chat(
            role="judge_semantic",
            messages=[{"role": "user", "content": "hi"}],
            campaign_id="cmp",
        )
    )
    assert not target.exists()


def test_enable_payload_trace_writes_one_jsonl_record_per_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_chat(monkeypatch)
    target = tmp_path / "trace.jsonl"
    llm_client.enable_payload_trace(target)
    try:
        asyncio.run(
            llm_client.chat(
                role="judge_semantic",
                messages=[
                    {"role": "system", "content": "you are a judge"},
                    {"role": "user", "content": "evaluate this"},
                ],
                campaign_id="cmp-1",
                attempt_id="att-1",
                temperature=0.0,
                max_tokens=42,
            )
        )
        asyncio.run(
            llm_client.chat(
                role="injector",
                messages=[{"role": "user", "content": "second call"}],
                campaign_id="cmp-1",
                attempt_id="att-2",
            )
        )
    finally:
        llm_client.disable_payload_trace()

    assert target.exists()
    lines = target.read_text().strip().splitlines()
    assert len(lines) == 2

    first = json.loads(lines[0])
    assert first["role"] == "judge_semantic"
    assert first["temperature"] == 0.0
    assert first["max_tokens"] == 42
    assert first["campaign_id"] == "cmp-1"
    assert first["attempt_id"] == "att-1"
    assert first["request_messages"][0]["content"] == "you are a judge"
    assert first["response_content"] == "fake response"
    assert first["prompt_tokens"] == 12
    assert first["completion_tokens"] == 7
    assert "latency_ms" in first

    second = json.loads(lines[1])
    assert second["role"] == "injector"
    assert second["attempt_id"] == "att-2"


def test_disable_payload_trace_stops_further_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_chat(monkeypatch)
    target = tmp_path / "trace.jsonl"
    llm_client.enable_payload_trace(target)
    asyncio.run(
        llm_client.chat(
            role="judge_semantic",
            messages=[{"role": "user", "content": "hi"}],
            campaign_id="cmp",
        )
    )
    llm_client.disable_payload_trace()
    # Second call must not append.
    asyncio.run(
        llm_client.chat(
            role="judge_semantic",
            messages=[{"role": "user", "content": "second"}],
            campaign_id="cmp",
        )
    )
    lines = target.read_text().strip().splitlines()
    assert len(lines) == 1


def test_trace_write_failure_is_swallowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An OSError on append must not break the LLM call."""
    _patch_chat(monkeypatch)
    # Point trace path at a directory we can't write to (parent is a file).
    bad_parent = tmp_path / "not-a-dir"
    bad_parent.write_text("blocked")
    target = bad_parent / "trace.jsonl"
    llm_client.enable_payload_trace(target)
    try:
        # Should not raise; chat() returns normally.
        content, cost = asyncio.run(
            llm_client.chat(
                role="judge_semantic",
                messages=[{"role": "user", "content": "hi"}],
                campaign_id="cmp",
            )
        )
        assert content == "fake response"
        assert cost.prompt_tokens == 12
    finally:
        llm_client.disable_payload_trace()
