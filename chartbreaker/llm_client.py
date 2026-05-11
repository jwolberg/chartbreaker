"""OpenAI-compatible chat dispatcher.

Routes every LLM call through MODEL_REGISTRY in config.py. Speaks the
OpenAI /v1/chat/completions surface, which OpenAI / OpenRouter / Ollama /
Anthropic all expose. Records a CostObservation for every dispatch so the
Orchestrator can enforce per-campaign budgets and the observability
dashboard can attribute cost per agent.

Retry policy: 3 attempts with exponential backoff on transient HTTP
errors (429 / 5xx) and network errors. Non-retriable HTTP errors raise
immediately.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal, TypedDict

import httpx

from chartbreaker.config import (
    PROVIDERS,
    get_provider_api_key,
    get_role_config,
)
from chartbreaker.state import CostObservation

logger = logging.getLogger(__name__)


class ChatMessage(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


_RETRY_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


# Pricing in USD per 1M tokens, keyed on (provider, model).
# These are PLACEHOLDERS — update from your provider's billing page before
# trusting cost telemetry. Missing entries default to $0 and log a warning.
_PRICING_USD_PER_1M: dict[tuple[str, str], tuple[float, float]] = {
    ("openai", "gpt-5.4-nano"): (0.150, 0.600),
    (
        "openrouter",
        "cognitivecomputations/dolphin-mixtral-8x22b",
    ): (0.900, 0.900),
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
                response.raise_for_status()
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

    body = await _post_with_retry(url, headers, payload, timeout_s)

    content = body["choices"][0]["message"]["content"]
    usage = body.get("usage", {})
    prompt_tokens = int(usage.get("prompt_tokens", 0))
    completion_tokens = int(usage.get("completion_tokens", 0))

    cost = CostObservation(
        campaign_id=campaign_id,
        attempt_id=attempt_id,
        agent=role,
        provider=role_cfg.provider,
        model=role_cfg.model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        usd=_estimate_cost_usd(role_cfg.provider, role_cfg.model, prompt_tokens, completion_tokens),
    )

    return content, cost
