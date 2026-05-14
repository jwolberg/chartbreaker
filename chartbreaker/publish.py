"""Publish pinned regression cases to an external tracker (GitLab).

This is step 1 of the closed-loop fix-verification flow:
ChartBreaker run → pinned ``AF-REG-NNN`` case → GitLab issue, with the
issue URL written back into ``regression_cases.yaml`` as ``tracker_ref``
so re-runs of this command are idempotent.

The OpenEMR fixer agent reads from the tracker; this module never reaches
into that side of the loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from chartbreaker import regression
from chartbreaker.trackers.gitlab import GitLabClient, Issue

# Threat-model subcategory labels — kept in sync with docs/THREAT_MODEL.md.
# Used for issue titles and the ``subcat:<id>`` scoped label body.
SUBCATEGORY_LABELS: dict[str, str] = {
    "1a": "Prompt injection — direct (USER_QUESTION)",
    "1b": "Prompt injection — indirect via chart text",
    "1c": "Prompt injection — vision-extracted PDF",
    "1d": "Prompt injection — multi-turn",
    "1e": "Prompt injection — structured-output coercion",
    "1f": "Prompt injection — system-prompt extraction",
    "2a": "Data exfiltration — PHI in summary/warning fields",
    "2b": "Data exfiltration — source-ID forgery",
    "2c": "Data exfiltration — cross-patient bleed",
    "2d": "Data exfiltration — vision-extraction PHI",
    "2f": "Data exfiltration — authorization bypass via pid",
    "3a": "State corruption — conversation history poisoning",
    "4a": "Tool misuse — unintended invocation",
    "4b": "Tool misuse — unintended invocation",
    "4c": "Tool misuse — parameter tampering",
    "4d": "Tool misuse — recursive tool calls",
    "5a": "DoS — token exhaustion / cost amplification",
    "6a": "Identity — CSRF / trust boundary",
    "6b": "Identity — persona hijacking",
    "6c": "Identity — privilege escalation (BAA gate)",
    "6d": "Identity — session fixation / cookie hardening",
    "6e": "Identity — login brute-force / auth surface",
}


@dataclass(frozen=True)
class PublishResult:
    case_id: str
    action: str  # "filed" | "skipped-already-filed" | "skipped-retired" | "dry-run"
    tracker_ref: str | None = None
    web_url: str | None = None


def labels_for_case(case: dict[str, Any]) -> list[str]:
    """GitLab labels for an issue. Scoped (``key::value``) for severity/subcat."""
    severity = (case.get("frozen_verdict") or {}).get("severity") or "unknown"
    subcat = case.get("subcategory") or "unknown"
    return [
        "chartbreaker",
        "state::open",
        f"severity::{severity}",
        f"subcat::{subcat}",
    ]


def issue_title(case: dict[str, Any]) -> str:
    cid = case["id"]
    subcat = case.get("subcategory", "?")
    specialist = case.get("specialist", "?")
    severity = (case.get("frozen_verdict") or {}).get("severity", "?")
    subcat_name = SUBCATEGORY_LABELS.get(subcat, "unmapped subcategory")
    return f"[{cid}] {subcat} — {subcat_name} ({specialist}, {severity})"


def issue_body(case: dict[str, Any]) -> str:
    cid = case["id"]
    subcat = case.get("subcategory", "?")
    subcat_name = SUBCATEGORY_LABELS.get(subcat, "unmapped subcategory")
    specialist = case.get("specialist", "?")
    pinned_at = case.get("pinned_at", "?")
    verdict = case.get("frozen_verdict") or {}
    target = case.get("frozen_target") or {}
    severity = verdict.get("severity", "?")
    exploitability = verdict.get("exploitability", "?")
    semantic = verdict.get("semantic", "?")
    verifier = verdict.get("verifier_replay", "?")
    judge_model = verdict.get("judge_model", "?")
    rationale = verdict.get("rationale", "")
    target_version = target.get("target_version", "?")
    http_status = target.get("http_status", "?")

    attempt = case.get("attempt") or {}
    prompt = attempt.get("prompt")
    chart_text = attempt.get("chart_text_payload")
    multi_turn = attempt.get("multi_turn_sequence")
    http_request = attempt.get("http_request")

    lines: list[str] = []
    lines.append(f"**Regression case:** `{cid}`")
    lines.append(f"**Subcategory:** {subcat} — {subcat_name}")
    lines.append(f"**Specialist:** {specialist}")
    lines.append(
        f"**Severity:** {severity} · **Exploitability:** {exploitability}"
    )
    lines.append(
        f"**Frozen verdict:** verifier={verifier} · semantic={semantic} · judge={judge_model}"
    )
    lines.append(f"**Pinned at:** {pinned_at}")
    lines.append(
        f"**Target at pin:** version={target_version} · http_status={http_status}"
    )
    lines.append("")
    lines.append("## Judge rationale")
    lines.append("")
    lines.append("> " + (rationale or "_(no rationale recorded)_").replace("\n", "\n> "))
    lines.append("")
    lines.append("## Reproducer")
    lines.append("")
    lines.append(f"Replay this case against the live target:")
    lines.append("")
    lines.append(f"```bash")
    lines.append(f"python -m chartbreaker.cli regress  # runs all pinned cases")
    lines.append(f"```")
    lines.append("")
    lines.append(
        "Full attempt payload is in "
        f"[`evals/regression_cases.yaml`](../blob/main/evals/regression_cases.yaml) "
        f"under `id: {cid}`."
    )
    if http_request:
        lines.append("")
        lines.append(
            "**HTTP shape:** "
            f"`{http_request.get('method', '?')} {http_request.get('path', '?')}`"
        )
    if prompt:
        lines.append("")
        lines.append("**Prompt excerpt:**")
        lines.append("")
        lines.append("```")
        lines.append(prompt[:600])
        lines.append("```")
    if chart_text:
        lines.append("")
        lines.append("**Chart-text payload excerpt:**")
        lines.append("")
        lines.append("```")
        lines.append(chart_text[:600])
        lines.append("```")
    if multi_turn:
        lines.append("")
        lines.append(f"**Multi-turn:** {len(multi_turn)} turns; see YAML for full sequence.")
    lines.append("")
    lines.append("## Verification")
    lines.append("")
    lines.append(
        "Close this issue by labeling it `state::awaiting-verification` after the "
        "fix MR merges. ChartBreaker will re-run the pinned case and post the "
        "verdict back here. The case is retired in `regression_cases.yaml` only "
        "when ChartBreaker reports `fixed`."
    )
    lines.append("")
    lines.append(
        f"_Filed by ChartBreaker `publish-findings` @ "
        f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}_"
    )
    return "\n".join(lines)


def _tracker_ref(host: str, project_id: int, iid: int) -> str:
    return f"gitlab:{host}:{project_id}:{iid}"


def publish_cases(
    *,
    project: str | int,
    client: GitLabClient | None = None,
    dry_run: bool = False,
    only_case_id: str | None = None,
) -> list[PublishResult]:
    """Walk regression cases and file the unfiled ones as GitLab issues.

    Idempotency: cases with a non-null ``tracker_ref`` are skipped.
    """
    cases = regression.load_cases(include_retired=True)
    results: list[PublishResult] = []
    changed = False

    for case in cases:
        cid = case["id"]
        if only_case_id and cid != only_case_id:
            continue
        if case.get("retired_at"):
            results.append(PublishResult(case_id=cid, action="skipped-retired"))
            continue
        if case.get("tracker_ref"):
            results.append(
                PublishResult(
                    case_id=cid,
                    action="skipped-already-filed",
                    tracker_ref=case["tracker_ref"],
                )
            )
            continue

        title = issue_title(case)
        body = issue_body(case)
        labels = labels_for_case(case)

        if dry_run or client is None:
            results.append(PublishResult(case_id=cid, action="dry-run"))
            continue

        issue: Issue = client.create_issue(project, title, body, labels=labels)
        ref = _tracker_ref(client._host, issue.project_id, issue.iid)  # noqa: SLF001
        case["tracker_ref"] = ref
        changed = True
        results.append(
            PublishResult(
                case_id=cid, action="filed", tracker_ref=ref, web_url=issue.web_url
            )
        )

    if changed and not dry_run:
        # Persist tracker_ref updates back to YAML. We re-load to avoid clobbering
        # writes that may have happened concurrently (cli-level concurrency is
        # the only realistic case; this is best-effort, not atomic).
        on_disk = regression.load_cases(include_retired=True)
        by_id = {c["id"]: c for c in cases}
        merged: list[dict[str, Any]] = []
        for c in on_disk:
            updated = by_id.get(c["id"])
            if updated and updated.get("tracker_ref") and not c.get("tracker_ref"):
                c["tracker_ref"] = updated["tracker_ref"]
            merged.append(c)
        regression._write_cases_raw(merged)  # noqa: SLF001

    return results
