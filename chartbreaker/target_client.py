"""HTTP wrapper for the OpenEMR Clinical Co-Pilot target.

Handles:
- Authentication as the dedicated ChartBreaker test user via the
  OpenEMR login form.
- CSRF token capture and reuse (body `csrf_token` + mirrored
  `X-CSRF-Token` header, per CopilotController.php:259).
- The single-target invariant: refuses dispatch to any host outside
  the configured base URL.
- Capture of the response envelope into a TargetResponse for the Judge.

Single-target enforcement is at the constructor level. Runtime override
requires explicit CLI flags handled in chartbreaker.cli, not here.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

import httpx

from chartbreaker.config import (
    TARGET_BASE_URL,
    TARGET_SITE,
    get_target_credentials,
)
from chartbreaker.state import AttackAttempt, HttpRequestShape, TargetResponse

logger = logging.getLogger(__name__)


_COPILOT_PATH = (
    "/interface/modules/custom_modules/oe-module-clinical-copilot"
    f"/public/index.php?site={TARGET_SITE}"
)
_LOGIN_PATH = f"/interface/login/login.php?site={TARGET_SITE}"


class TargetUnreachableError(RuntimeError):
    """Network failure, auth failure, or persistent 5xx from the target."""


class CrossTargetError(RuntimeError):
    """Caller tried to dispatch to a URL outside the configured target."""


def _extract_csrf_token(text: str) -> str | None:
    """Best-effort CSRF-token extraction from a response body.

    OpenEMR's stock pages put the token in a meta tag, a hidden form
    field, or — for module endpoints — return it in a JSON field.
    Returns None if no known pattern matches.
    """
    patterns = (
        r'name=["\']csrf_token["\']\s+content=["\']([^"\']+)["\']',
        r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']',
        r'"csrf_token"\s*:\s*"([^"]+)"',
    )
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


def _safe_parse_json(response: httpx.Response) -> dict[str, Any] | None:
    """Parse response as JSON, swallowing parse errors. Returns None on failure."""
    try:
        body = response.json()
    except (ValueError, httpx.DecodingError):
        return None
    return body if isinstance(body, dict) else None


class TargetClient:
    """Authenticated, single-target HTTP client for the Co-Pilot.

    Usage:
        async with TargetClient() as client:
            response = await client.dispatch(attempt)
    """

    def __init__(self, base_url: str = TARGET_BASE_URL) -> None:
        if base_url != TARGET_BASE_URL:
            raise CrossTargetError(
                f"base_url {base_url!r} is not the configured target "
                f"{TARGET_BASE_URL!r}. Use the chartbreaker CLI --target-override "
                f"flag if you genuinely need to redirect; this constructor refuses."
            )
        self._base_url = base_url
        self._client: httpx.AsyncClient | None = None
        self._csrf_token: str | None = None
        self._authenticated: bool = False

    async def __aenter__(self) -> "TargetClient":
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            follow_redirects=True,
            timeout=30.0,
        )
        await self._authenticate()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._client is not None:
            await self._client.aclose()
        self._client = None
        self._authenticated = False
        self._csrf_token = None

    async def _authenticate(self) -> None:
        """POST to the OpenEMR login form, capture session cookie + CSRF."""
        assert self._client is not None, "client not initialized; use async with"
        user, password = get_target_credentials()
        try:
            response = await self._client.post(
                _LOGIN_PATH,
                data={
                    "authUser": user,
                    "clearPass": password,
                    "authProvider": "Default",
                    "languageChoice": "1",
                },
            )
        except httpx.RequestError as exc:
            raise TargetUnreachableError(f"login request failed: {exc}") from exc

        if response.status_code >= 400:
            raise TargetUnreachableError(
                f"login returned {response.status_code}; first 200 chars: "
                f"{response.text[:200]!r}"
            )

        self._authenticated = True
        self._csrf_token = _extract_csrf_token(response.text)
        if self._csrf_token is None:
            logger.warning(
                "CSRF token not found in login response; first Co-Pilot POST may "
                "need to fetch it from a follow-up endpoint."
            )

    async def dispatch(self, attempt: AttackAttempt) -> TargetResponse:
        """Send one AttackAttempt to the target and capture the response."""
        if not self._authenticated or self._client is None:
            raise TargetUnreachableError(
                "TargetClient is not authenticated. Use it as an async context manager."
            )

        start = time.perf_counter()

        if attempt.http_request is not None:
            response = await self._dispatch_http_request(attempt.http_request)
        elif (
            attempt.prompt is not None
            or attempt.chart_text_payload is not None
            or attempt.multi_turn_sequence is not None
        ):
            response = await self._dispatch_copilot_briefing(attempt)
        else:
            raise ValueError(
                f"AttackAttempt {attempt.attempt_id} has no actionable payload"
            )

        latency_ms = int((time.perf_counter() - start) * 1000)
        body = _safe_parse_json(response)

        # Refresh CSRF token if the response carried a new one.
        if body is not None and isinstance(body.get("csrf_token"), str):
            self._csrf_token = body["csrf_token"]

        return TargetResponse(
            attempt_id=attempt.attempt_id,
            http_status=response.status_code,
            raw_model_output=(body.get("raw_model_output") if body else None),
            post_verifier_output=(
                body.get("summary")
                if body is not None
                else response.text[:2000] if response.text else None
            ),
            latency_ms=latency_ms,
            prompt_tokens=(
                body.get("usage", {}).get("prompt_tokens") if body else None
            ),
            completion_tokens=(
                body.get("usage", {}).get("completion_tokens") if body else None
            ),
            audit_log_id=(body.get("audit_log_id") if body else None),
            target_version=(body.get("model", "unknown") if body else "unknown"),
        )

    async def _dispatch_copilot_briefing(self, attempt: AttackAttempt) -> httpx.Response:
        """POST a briefing/followup request to the Co-Pilot endpoint."""
        assert self._client is not None

        # For MVP, only the last user turn of a multi_turn_sequence is dispatched;
        # full multi-turn arc handling lands in Phase 2 with Conversationalist.
        user_question = (
            attempt.prompt
            if attempt.prompt is not None
            else (
                attempt.multi_turn_sequence[-1]
                if attempt.multi_turn_sequence
                else None
            )
        )
        if user_question is None and attempt.chart_text_payload is None:
            raise ValueError("attempt has no prompt or chart_text_payload")

        # Default to the first fixture patient for MVP; specialists can override
        # by setting pid on the http_request branch.
        from chartbreaker.config import FIXTURE_PIDS

        pid = FIXTURE_PIDS[0]

        body: dict[str, Any] = {
            "action": "briefing",
            "pid": pid,
            "csrf_token": self._csrf_token or "",
        }
        if user_question is not None:
            body["user_question"] = user_question
        if attempt.chart_text_payload is not None:
            # Indirect-injection cases need this payload routed into PATIENT_CONTEXT,
            # which in V1 means it has to already be in the chart. For MVP the
            # specialist is responsible for arranging that via fixture data;
            # we only pass the payload as metadata for trace records.
            body["chart_text_payload"] = attempt.chart_text_payload

        headers = {"X-CSRF-Token": self._csrf_token or ""}
        return await self._client.post(_COPILOT_PATH, json=body, headers=headers)

    async def _dispatch_http_request(self, req: HttpRequestShape) -> httpx.Response:
        """Dispatch a deterministic specialist's prepared HTTP envelope."""
        assert self._client is not None

        # Single-target enforcement at request time: the path must be relative.
        # Absolute URLs are rejected to prevent specialists from accidentally
        # constructing a cross-host request.
        if req.path.startswith("http://") or req.path.startswith("https://"):
            raise CrossTargetError(
                f"http_request.path must be relative, got {req.path!r}. "
                f"The base URL is fixed in config."
            )

        headers = dict(req.headers)
        # Inject the current CSRF token if the specialist didn't set its own
        # (Cracker often wants to set or omit it on purpose).
        if "X-CSRF-Token" not in headers and self._csrf_token is not None:
            headers["X-CSRF-Token"] = self._csrf_token

        if req.method == "POST":
            return await self._client.post(
                req.path,
                json=req.body,
                headers=headers,
                cookies=req.cookies,
            )
        return await self._client.get(
            req.path,
            headers=headers,
            cookies=req.cookies,
        )
