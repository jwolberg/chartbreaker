"""OpenAI-compatible chat dispatcher.

Routes every LLM call through MODEL_REGISTRY in config.py. Speaks the
OpenAI /v1/chat/completions surface, which OpenAI / OpenRouter / Ollama /
Anthropic all expose. Records a CostObservation for every dispatch so the
Orchestrator can enforce per-campaign budgets and the observability
dashboard can attribute cost per agent.

Retry policy: 3 attempts with exponential backoff on transient HTTP
errors (429 / 5xx) and network errors. Non-retriable HTTP errors raise
immediately.

Payload tracing (P2.5-T2): when `enable_payload_trace(path)` has been
called for the current process (typically by `chartbreaker run-mvp-loop
--trace-llm-io`), every `chat()` call appends one JSON line containing
the full request messages + response content + token counts to that
file. Off by default — long runs would otherwise write megabytes of
prompt text to disk. See docs/OBSERVABILITY.md § Gap #3.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, TypedDict

import httpx

from chartbreaker.config import (
    PROVIDERS,
    get_provider_api_key,
    get_role_config,
)
from chartbreaker.state import CostObservation

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Payload trace (P2.5-T2) — opt-in I/O capture for forensic debugging.
# ----------------------------------------------------------------------

_payload_trace_path: Path | None = None
_payload_trace_lock = threading.Lock()


def enable_payload_trace(path: str | Path) -> None:
    """Turn on full request/response capture for every chat() call.

    Idempotent. The path is created on first append. Each line is one
    JSON object: {ts, role, provider, model, temperature, max_tokens,
    request_messages, response_content, prompt_tokens, completion_tokens,
    usd, latency_ms}. Designed to be greppable / jq-friendly.
    """
    global _payload_trace_path
    _payload_trace_path = Path(path)


def disable_payload_trace() -> None:
    """Reset the payload-trace destination. Used by tests."""
    global _payload_trace_path
    _payload_trace_path = None


def _maybe_write_trace(record: dict) -> None:
    """Append one record to the active trace file, if any. Best-effort."""
    if _payload_trace_path is None:
        return
    try:
        _payload_trace_path.parent.mkdir(parents=True, exist_ok=True)
        with _payload_trace_lock:
            with _payload_trace_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str, separators=(",", ":")))
                f.write("\n")
    except OSError as exc:
        logger.warning("could not write LLM payload trace: %s", exc)


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


_RETRY_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


# Pricing in USD per 1M tokens, keyed on (provider, model).
# These are PLACEHOLDERS — update from your provider's billing page before
# trusting cost telemetry. Missing entries default to $0 and log a warning.
_PRICING_USD_PER_1M: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-5.4-nano"): (0.150, 0.600),
    # Default offensive model — Dolphin Venice edition, free tier.
    (
        "openrouter",
        "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
    ): (0.0, 0.0),
    # Paid alternative — Nous Hermes 3 70B.
    (
        "openrouter",
        "nousresearch/hermes-3-llama-3.1-70b",
    ): (0.300, 0.300),
}


def _estimate_cost_usd(
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Look up per-million-token pricing and compute call cost."""
    price = _PRICING_USD_PER_1M.get((provider, model))
    if price is None:
        logger.warning(
            "no pricing entry for %s:%s; recording cost=$0. "
            "Update _PRICING_USD_PER_1M in chartbreaker/llm_client.py.",
            provider,
            model,
        )
        return 0.0
    prompt_price, completion_price = price
    return (prompt_tokens * prompt_price + completion_tokens * completion_price) / 1_000_000


async def _post_with_retry(
    url: str,
    headers: dict[str, str],
    payload: dict,
    timeout_s: float,
    max_attempts: int = 3,
) -> dict:
    """POST with exponential-backoff retries on transient failures."""
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for attempt_idx in range(max_attempts):
            try:
                response = await client.post(url, headers=headers, json=payload)
                if response.status_code in _RETRY_STATUS_CODES:
                    if attempt_idx + 1 < max_attempts:
                        delay = 2**attempt_idx
                        logger.warning(
                            "transient HTTP %s from %s; retrying in %ds (attempt %d/%d)",
                            response.status_code,
                            url,
                            delay,
                            attempt_idx + 1,
                            max_attempts,
                        )
                        await asyncio.sleep(delay)
                        continue
                if response.status_code >= 400:
                    # Surface the provider's response body — OpenRouter / OpenAI
                    # typically explain *why* in the body (e.g. "model not found").
                    raise httpx.HTTPStatusError(
                        f"HTTP {response.status_code} from {url}: {response.text[:500]}",
                        request=response.request,
                        response=response,
                    )
                return response.json()
            except httpx.RequestError as exc:
                if attempt_idx + 1 == max_attempts:
                    raise
                delay = 2**attempt_idx
                logger.warning(
                    "network error %s; retrying in %ds (attempt %d/%d)",
                    exc,
                    delay,
                    attempt_idx + 1,
                    max_attempts,
                )
                await asyncio.sleep(delay)
    raise RuntimeError("unreachable: retry loop exited without return or raise")


async def chat(
    role: str,
    messages: list[ChatMessage],
    *,
    campaign_id: str,
    attempt_id: str | None = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout_s: float = 60.0,
) -> tuple[str, CostObservation]:
    """Dispatch a chat-completion request for a role.

    Returns (content_text, cost_observation). Caller is responsible for
    persisting the CostObservation to the observability store.
    """
    role_cfg = get_role_config(role)
    provider_cfg = PROVIDERS[role_cfg.provider]
    api_key = get_provider_api_key(role_cfg.provider)

    headers: dict[str, str] = {"Content-Type": "application/json"}
    if api_key is not None:
        headers["Authorization"] = f"Bearer {api_key}"

    payload: dict = {
        "model": role_cfg.model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    url = f"{provider_cfg.base_url}/chat/completions"

    start = time.perf_counter()
    body = await _post_with_retry(url, headers, payload, timeout_s)
    latency_ms = int((time.perf_counter() - start) * 1000)

    content = body["choices"][0]["message"]["content"]
    usage = body.get("usage", {})
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))
    usd = _estimate_cost_usd(
        role_cfg.provider, role_cfg.model, prompt_tokens, completion_tokens
    )

    cost = CostObservation(
        campaign_id=campaign_id,
        attempt_id=attempt_id,
        agent=role,
        provider=role_cfg.provider,
        model=role_cfg.model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd=usd,
    )

    # P2.5-T2: opt-in full-payload trace for forensic investigation.
    _maybe_write_trace(
        {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "role": role,
            "provider": role_cfg.provider,
            "model": role_cfg.model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "campaign_id": campaign_id,
            "attempt_id": attempt_id,
            "request_messages": messages,
            "response_content": content,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "usd": usd,
            "latency_ms": latency_ms,
        }
    )

    return content, cost
