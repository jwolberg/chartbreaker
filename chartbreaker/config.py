"""ChartBreaker configuration: target deployment, model registry, budgets.

Single-target invariant: TARGET_BASE_URL is hardcoded. Runtime override is
possible only via paired CLI flags (--target-override AND
--i-understand-this-attacks-the-target) per docs/ARCHITECTURE.md
§ Human Approval Gates.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


# -----------------------------------------------------------------------------
# Target deployment
# -----------------------------------------------------------------------------

TARGET_BASE_URL = "https://openemr.136-118-242-198.sslip.io"
TARGET_SITE = "default"
TARGET_LOGIN_ENDPOINT = (
    f"{TARGET_BASE_URL}/interface/login/login.php?site={TARGET_SITE}"
)
TARGET_COPILOT_ENDPOINT = (
    f"{TARGET_BASE_URL}/interface/modules/custom_modules/oe-module-clinical-copilot"
    f"/public/index.php?site={TARGET_SITE}"
)
TARGET_VISION_ENDPOINT = (
    f"{TARGET_BASE_URL}/interface/modules/custom_modules/oe-module-clinical-copilot"
    f"/public/run-extraction.php"
)


def get_target_credentials() -> tuple[str, str]:
    """Read the dedicated ChartBreaker test-user credentials from env."""
    user = os.environ.get("CHARTBREAKER_TARGET_USER")
    password = os.environ.get("CHARTBREAKER_TARGET_PASSWORD")
    if not user or not password:
        raise RuntimeError(
            "CHARTBREAKER_TARGET_USER and CHARTBREAKER_TARGET_PASSWORD must be set. "
            "Copy .env.example to .env and fill in the dedicated test-user credentials."
        )
    return user, password


# Fixture patients the dedicated test user has explicit ACL access to.
# Cross-tenant pids (for Cat 2f authz-bypass attacks) belong to a different
# test user and must NOT appear in this list.
FIXTURE_PIDS: list[int] = [1, 2, 3]


# -----------------------------------------------------------------------------
# Model registry — per-role provider + model dispatch
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class ProviderConfig:
    """An OpenAI-compatible chat-completion endpoint."""

    name: str
    base_url: str
    api_key_env: str | None  # None when provider needs no auth (e.g. Ollama).


PROVIDERS: dict[str, ProviderConfig] = {
    "openai": ProviderConfig(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
    ),
    "openrouter": ProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
    ),
    "ollama": ProviderConfig(
        name="ollama",
        base_url="http://localhost:11434/v1",
        api_key_env=None,
    ),
    "anthropic": ProviderConfig(
        name="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_key_env="ANTHROPIC_API_KEY",
    ),
}


@dataclass(frozen=True)
class RoleConfig:
    """Which provider + model handles which agent role."""

    role: str
    provider: str
    model: str


MODEL_REGISTRY: dict[str, RoleConfig] = {
    # Control plane — OpenAI gpt-5.4-nano default; escalate per role
    # if the calibration set degrades.
    "orchestrator":    RoleConfig("orchestrator",    "openai", "gpt-5.4-nano"),
    "red_team_lead":   RoleConfig("red_team_lead",   "openai", "gpt-5.4-nano"),
    "judge_semantic":  RoleConfig("judge_semantic",  "openai", "gpt-5.4-nano"),
    "scribe":          RoleConfig("scribe",          "openai", "gpt-5.4-nano"),
    # Offensive specialists — OpenRouter uncensored fine-tune.
    # Default: nousresearch/hermes-3-llama-3.1-70b ($0.30/M, 131k ctx).
    # Hermes 3 is lightly aligned and rarely refuses red-team prompts.
    # Free tier (cognitivecomputations/dolphin-mistral-24b-venice-edition:free)
    # was the previous default but the shared rate limit makes it
    # unusable for sustained runs. Commercially-aligned frontier models
    # remain disallowed for this role per docs/ARCHITECTURE.md § Injector.
    "injector":          RoleConfig("injector",          "openrouter", "nousresearch/hermes-3-llama-3.1-70b"),
    "conversationalist": RoleConfig("conversationalist", "openrouter", "nousresearch/hermes-3-llama-3.1-70b"),
    "smuggler":          RoleConfig("smuggler",          "openrouter", "nousresearch/hermes-3-llama-3.1-70b"),
    "impersonator":      RoleConfig("impersonator",      "openrouter", "nousresearch/hermes-3-llama-3.1-70b"),
    # Deterministic specialists (Saboteur, Cracker, Glutton) have no model
    # and are absent from this registry by design.
}


def get_role_config(role: str) -> RoleConfig:
    """Return the {provider, model} pair for a role."""
    try:
        return MODEL_REGISTRY[role]
    except KeyError:
        known = ", ".join(sorted(MODEL_REGISTRY))
        raise KeyError(f"Role {role!r} not in MODEL_REGISTRY. Known: {known}")


def get_provider_api_key(provider_name: str) -> str | None:
    """Look up the API key for a provider. None if provider needs no auth."""
    if provider_name not in PROVIDERS:
        raise KeyError(f"Unknown provider {provider_name!r}")
    env_var = PROVIDERS[provider_name].api_key_env
    if env_var is None:
        return None
    key = os.environ.get(env_var)
    if not key:
        raise RuntimeError(
            f"Provider {provider_name!r} requires {env_var} to be set. "
            f"Copy .env.example to .env and fill it in."
        )
    return key


# -----------------------------------------------------------------------------
# Budgets — Orchestrator halts campaigns that exceed these without signal
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class Budgets:
    max_campaign_usd: float = 1.00
    max_run_usd: float = 5.00
    max_attempts_per_subcategory: int = 50


BUDGETS = Budgets()


# -----------------------------------------------------------------------------
# Paths (relative to repo root; resolved by callers)
# -----------------------------------------------------------------------------

OBSERVABILITY_DIR = "observability"
RUNS_SQLITE = f"{OBSERVABILITY_DIR}/runs.sqlite"
TRACES_JSONL = f"{OBSERVABILITY_DIR}/traces.jsonl"

EVALS_DIR = "evals"
SEED_CASES_YAML = f"{EVALS_DIR}/seed_cases.yaml"
REGRESSION_CASES_YAML = f"{EVALS_DIR}/regression_cases.yaml"
JUDGE_CALIBRATION_YAML = f"{EVALS_DIR}/judge_calibration.yaml"
