"""Typed Pydantic state objects passed between ChartBreaker agents.

These are the load-bearing inter-agent contracts named in
docs/ARCHITECTURE.md § Inter-Agent Communication. Each message type maps
to one edge in the LangGraph state graph; no agent sees fields that
aren't on its declared input type.

All models are frozen (immutable) to keep state transitions explicit —
agents return new state objects rather than mutating inputs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

# -----------------------------------------------------------------------------
# Verdict / severity / status enums (Literal for autocomplete + PHPStan-style
# exhaustive matching in match statements)
# -----------------------------------------------------------------------------

VerifierReplayVerdict = Literal["pass", "fail"]
SemanticVerdict = Literal["pass", "partial", "fail", "not_run"]
Severity = Literal["info", "low", "medium", "high", "critical"]
Exploitability = Literal["trivial", "easy", "moderate", "hard"]
RecommendedAction = Literal["regression", "mutate", "escalate", "discard"]
RegressionStatus = Literal[
    "fixed", "still_vulnerable", "new_regression", "drift_flagged"
]
HttpMethod = Literal["GET", "POST"]


def _utcnow() -> datetime:
    """Project-wide source of `now`. Inject a clock in tests, do not freeze here."""
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    """Generate a new opaque runtime ID."""
    return str(uuid4())


# -----------------------------------------------------------------------------
# Base config: frozen, strict, forbid extra fields
# -----------------------------------------------------------------------------


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


# -----------------------------------------------------------------------------
# CampaignBrief — Orchestrator → RedTeamLead
# -----------------------------------------------------------------------------


class CampaignBrief(_Frozen):
    """Orchestrator's decision: which subcategory to attack and how aggressively."""

    campaign_id: str = Field(default_factory=_new_id)
    subcategory_id: str  # e.g. "1b", "2f", "6a" — matches THREAT_MODEL IDs
    seed_case_id: str | None = None  # None means generate fresh, no seed
    mutation_budget: int = Field(ge=0, le=100)
    max_cost_usd: float = Field(ge=0.0)
    rationale: str  # human-readable explanation for the trace log
    created_at: datetime = Field(default_factory=_utcnow)


# -----------------------------------------------------------------------------
# AttackAttempt — Specialist → TargetClient
# -----------------------------------------------------------------------------


class MultipartFile(_Frozen):
    """One file part of a multipart/form-data request.

    Used by Saboteur Cat 4a (vision-pipeline upload probes against
    `/run-extraction.php`). `content_b64` is base64-encoded bytes so
    the value remains JSON-serializable when written to the
    observability store.
    """

    field_name: str
    filename: str
    content_b64: str
    content_type: str = "application/octet-stream"


class HttpRequestShape(_Frozen):
    """A deterministic-specialist's HTTP request envelope.

    Exactly one of `body`, `form_data`, `multipart_files` (or any
    combination of form_data + multipart_files for multipart bodies)
    should be set per request. `bypass_auth=True` issues the request
    from a fresh, unauthenticated httpx client — required for Cracker
    Cat 6d (session-fixation pre-seed) and 6e (login brute-force).
    """

    method: HttpMethod
    path: str
    body: dict[str, Any] | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    cookies: dict[str, str] | None = None
    form_data: dict[str, str] | None = None
    multipart_files: list[MultipartFile] | None = None
    bypass_auth: bool = False


class AttackAttempt(_Frozen):
    """One adversarial input the platform sends to the target.

    Exactly one of {prompt, chart_text_payload, multi_turn_sequence,
    http_request} is populated, matching the attack shape the specialist
    produces. The Target Client dispatches based on which is set.
    """

    attempt_id: str = Field(default_factory=_new_id)
    campaign_id: str
    subcategory_id: str
    specialist: Literal[
        "injector",
        "conversationalist",
        "smuggler",
        "impersonator",
        "saboteur",
        "cracker",
        "glutton",
    ]
    # LLM-shaped attacks
    prompt: str | None = None
    chart_text_payload: str | None = None
    multi_turn_sequence: list[str] | None = None
    # Deterministic-specialist-shaped attacks
    http_request: HttpRequestShape | None = None
    # Mutation lineage
    mutation_of: str | None = None  # parent attempt_id if this is a mutation
    created_at: datetime = Field(default_factory=_utcnow)


# -----------------------------------------------------------------------------
# TargetResponse — TargetClient → Judge
# -----------------------------------------------------------------------------


class TargetResponse(_Frozen):
    """What the Co-Pilot returned for one attempt.

    Both raw_model_output (pre-PHP-verifier) and post_verifier_output
    (what the clinician would see) are captured so the Judge can run
    its own verifier replay and decide whether the soft defense held.
    """

    attempt_id: str
    http_status: int
    raw_model_output: str | None  # unscrubbed model output; None if not returned
    post_verifier_output: str | None  # post-PHP-verifier output; what user saw
    latency_ms: int = Field(ge=0)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    audit_log_id: str | None = None
    target_version: str  # e.g. "gpt-5.4-mini" — pinned for regression replay
    # Response-side metadata for protocol probes (Cat 6d session-fixation
    # cookie-flag audit, Cat 6e login-response inspection). Optional because
    # the Co-Pilot briefing path doesn't need it.
    response_cookies: dict[str, str] | None = None
    set_cookie_headers: list[str] | None = None
    created_at: datetime = Field(default_factory=_utcnow)


# -----------------------------------------------------------------------------
# Verdict — Judge → Regression, Scribe, Observability
# -----------------------------------------------------------------------------


class Verdict(_Frozen):
    """The Judge's two-part verdict per ARCHITECTURE § Judge Agent.

    `verifier_replay` is deterministic (Python ports of SourceAttribution +
    DomainConstraint verifiers re-run on raw_model_output).
    `semantic` is the LLM judge's read of whether the model violated the
    DATA-ONLY rule regardless of post-scrub output. Disagreement between
    the two is itself a finding.
    """

    attempt_id: str
    verifier_replay: VerifierReplayVerdict
    semantic: SemanticVerdict
    severity: Severity
    exploitability: Exploitability
    rationale: str
    recommended_action: RecommendedAction
    judge_model: str  # e.g. "openai:gpt-5.4-nano" — pinned for replayability
    created_at: datetime = Field(default_factory=_utcnow)


# -----------------------------------------------------------------------------
# RegressionReport — Regression → Observability
# -----------------------------------------------------------------------------


class RegressionReport(_Frozen):
    """One regression-replay row: previously-pinned exploit vs current verdict."""

    run_id: str  # which regression sweep produced this
    case_id: str  # which pinned regression case (e.g. "AF-REG-001")
    previous_verdict: Verdict
    current_verdict: Verdict
    status: RegressionStatus
    created_at: datetime = Field(default_factory=_utcnow)


# -----------------------------------------------------------------------------
# ReportDraft — Scribe → Observability + filesystem
# -----------------------------------------------------------------------------


class ReportDraft(_Frozen):
    """A vulnerability report draft. Critical/high stays in reports/draft/
    until a human promotes it with `git mv`."""

    finding_id: str  # e.g. "AF-001"
    title: str
    severity: Severity
    body_markdown: str
    attempt_id: str  # back-reference to the exploit attempt
    promoted_to_published: bool = False  # set True when human moves file out of draft/
    created_at: datetime = Field(default_factory=_utcnow)


# -----------------------------------------------------------------------------
# CostObservation — Glutton (and every LLM call) → Observability
# -----------------------------------------------------------------------------


class CostObservation(_Frozen):
    """One LLM call's cost telemetry. Written on every dispatch by llm_client."""

    campaign_id: str
    attempt_id: str | None = None  # Orchestrator narration calls have no attempt
    agent: str  # role name e.g. "judge_semantic", "injector"
    provider: str  # "openai" / "openrouter" / "ollama" / "anthropic"
    model: str
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    usd: float = Field(ge=0.0)
    created_at: datetime = Field(default_factory=_utcnow)
