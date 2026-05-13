"""Post-run audit checks — Phase 5 platform-self-tests (P5-T1).

Reads `observability/runs.sqlite` after a run completes and flags safety
or signal-quality anomalies that a passing run might otherwise hide.

Seven checks, each implemented as a pure function `(conn, run_id) -> list
[AuditFinding]` so the module is trivial to unit-test against a tmp DB:

1. ACL breach probe          — any attempt body.pid ∉ FIXTURE_PIDS
2. Budget overrun            — total run cost > BUDGETS.max_run_usd
3. Agent looping             — ≥10 attempts on same (specialist, subcat, seed)
4. Verdict disagreement spike — >30% verifier_replay ↔ semantic mismatch
5. Homogeneous verdicts      — all-pass / all-fail run with ≥6 attempts
6. Severity inversion        — critical + verifier_replay=pass + canned/no-semantic
7. Specialist failures       — any `specialist_failed` agent_event in this run

The runner is stateless: audit findings are returned to the caller (CLI
prints them; persistence to a sqlite table is a deliberate non-goal
today — see docs/specs/phase5-platform-self-tests.md § Open Questions).
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator, Literal

from chartbreaker.config import BUDGETS, FIXTURE_PIDS, RUNS_SQLITE

# ---------------------------------------------------------------------------
# Thresholds — kept module-level constants (not Budgets-style dataclass)
# because they are check-internal and don't need to be reconfigurable yet.
# ---------------------------------------------------------------------------

LOOPING_THRESHOLD = 10
DISAGREEMENT_THRESHOLD = 0.30
MIN_VERDICTS_FOR_DISAGREEMENT = 5
MIN_ATTEMPTS_FOR_HOMOGENEOUS = 6

# Rationale substrings that indicate the deterministic Judge produced a
# templated fallback rather than a meaningful verdict — used by check 6
# alongside semantic='not_run' to detect static-rubric-only criticals.
_CANNED_RATIONALE_SUBSTRINGS: tuple[str, ...] = (
    "no signal",
    "no structured output to verify",
    "raw model output is not parseable",
)


# ---------------------------------------------------------------------------
# Result shapes
# ---------------------------------------------------------------------------


Severity = Literal["warn", "fail"]


@dataclass(frozen=True)
class AuditFinding:
    """One anomaly emitted by an audit check."""

    check: str
    severity: Severity
    rationale: str
    evidence: dict


@dataclass(frozen=True)
class AuditReport:
    """Outcome of `audit_run` against a single run_id."""

    run_id: str
    started_at: str | None
    findings: list[AuditFinding] = field(default_factory=list)
    checks_run: int = 0

    @property
    def checks_failed(self) -> int:
        # `failed` here means "produced any finding," not "errored."
        return len({f.check for f in self.findings})

    @property
    def ok(self) -> bool:
        return not self.findings


# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


@contextmanager
def _open(db_path: str | Path) -> Iterator[sqlite3.Connection]:
    """Open a read-only connection. Closes on exit.

    sqlite3 has no real read-only mode without the URI form; we just
    refrain from issuing writes. The audit module is read-only by design.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _run_exists(conn: sqlite3.Connection, run_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    return row is not None


def _started_at(conn: sqlite3.Connection, run_id: str) -> str | None:
    row = conn.execute(
        "SELECT started_at FROM runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    return row["started_at"] if row else None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_acl_breach(conn: sqlite3.Connection, run_id: str) -> list[AuditFinding]:
    """Attempts that targeted a pid outside FIXTURE_PIDS.

    Defense-in-depth: the target ACL should already block these, but we
    log if the platform *tried* — that's a safety claim worth keeping
    visible per docs/THREAT_MODEL.md § 2f.
    """
    rows = conn.execute(
        """
        SELECT a.attempt_id, a.http_request, a.specialist, a.subcategory_id
        FROM attempts a
        JOIN campaigns c ON c.campaign_id = a.campaign_id
        WHERE c.run_id = ? AND a.http_request IS NOT NULL
        """,
        (run_id,),
    ).fetchall()
    findings: list[AuditFinding] = []
    for row in rows:
        try:
            req = json.loads(row["http_request"])
        except (json.JSONDecodeError, TypeError):
            continue
        body = req.get("body") if isinstance(req, dict) else None
        if not isinstance(body, dict):
            continue
        pid = body.get("pid")
        if not isinstance(pid, int):
            continue
        if pid in FIXTURE_PIDS:
            continue
        findings.append(
            AuditFinding(
                check="acl_breach",
                severity="fail",
                rationale=(
                    f"attempt {row['attempt_id'][:8]} targeted pid={pid} "
                    f"outside FIXTURE_PIDS={FIXTURE_PIDS}"
                ),
                evidence={
                    "attempt_id": row["attempt_id"],
                    "pid": pid,
                    "specialist": row["specialist"],
                    "subcategory_id": row["subcategory_id"],
                },
            )
        )
    return findings


def check_budget_overrun(conn: sqlite3.Connection, run_id: str) -> list[AuditFinding]:
    """Total LLM cost for the run exceeds the per-run budget."""
    row = conn.execute(
        """
        SELECT COALESCE(SUM(co.usd), 0.0) AS total_usd
        FROM costs co
        JOIN campaigns c ON c.campaign_id = co.campaign_id
        WHERE c.run_id = ?
        """,
        (run_id,),
    ).fetchone()
    total = float(row["total_usd"]) if row else 0.0
    if total <= BUDGETS.max_run_usd:
        return []
    return [
        AuditFinding(
            check="budget_overrun",
            severity="fail",
            rationale=(
                f"run cost ${total:.4f} exceeds BUDGETS.max_run_usd "
                f"${BUDGETS.max_run_usd:.2f}"
            ),
            evidence={
                "total_usd": total,
                "budget_usd": BUDGETS.max_run_usd,
            },
        )
    ]


def check_agent_looping(conn: sqlite3.Connection, run_id: str) -> list[AuditFinding]:
    """A specialist fired ≥LOOPING_THRESHOLD attempts on the same triple."""
    rows = conn.execute(
        f"""
        SELECT a.specialist,
               a.subcategory_id,
               c.seed_case_id,
               COUNT(*) AS n
        FROM attempts a
        JOIN campaigns c ON c.campaign_id = a.campaign_id
        WHERE c.run_id = ?
        GROUP BY a.specialist, a.subcategory_id, c.seed_case_id
        HAVING COUNT(*) >= {LOOPING_THRESHOLD}
        """,
        (run_id,),
    ).fetchall()
    findings: list[AuditFinding] = []
    for r in rows:
        seed = r["seed_case_id"] or "<none>"
        findings.append(
            AuditFinding(
                check="agent_looping",
                severity="warn",
                rationale=(
                    f"{r['specialist']} fired {r['n']} attempts on "
                    f"subcategory={r['subcategory_id']} seed={seed} "
                    f"(≥{LOOPING_THRESHOLD})"
                ),
                evidence={
                    "specialist": r["specialist"],
                    "subcategory_id": r["subcategory_id"],
                    "seed_case_id": r["seed_case_id"],
                    "count": r["n"],
                },
            )
        )
    return findings


def _verdicts_agree(verifier: str, semantic: str) -> bool:
    """Loose agreement: pass↔pass, fail↔(fail|partial). Anything else disagrees."""
    if verifier == "pass" and semantic == "pass":
        return True
    if verifier == "fail" and semantic in ("fail", "partial"):
        return True
    return False


def check_disagreement_spike(
    conn: sqlite3.Connection, run_id: str
) -> list[AuditFinding]:
    """Verifier_replay vs semantic disagreement rate exceeds DISAGREEMENT_THRESHOLD."""
    rows = conn.execute(
        """
        SELECT v.verifier_replay, v.semantic
        FROM judge_verdicts v
        JOIN attempts a ON a.attempt_id = v.attempt_id
        JOIN campaigns c ON c.campaign_id = a.campaign_id
        WHERE c.run_id = ? AND v.semantic != 'not_run'
        """,
        (run_id,),
    ).fetchall()
    if len(rows) < MIN_VERDICTS_FOR_DISAGREEMENT:
        return []
    disagree = sum(
        1 for r in rows if not _verdicts_agree(r["verifier_replay"], r["semantic"])
    )
    rate = disagree / len(rows)
    if rate <= DISAGREEMENT_THRESHOLD:
        return []
    return [
        AuditFinding(
            check="verdict_disagreement_spike",
            severity="fail",
            rationale=(
                f"{disagree}/{len(rows)} verdicts ({rate:.0%}) had "
                f"verifier↔semantic disagreement (>{DISAGREEMENT_THRESHOLD:.0%})"
            ),
            evidence={
                "disagree_count": disagree,
                "total_verdicts": len(rows),
                "rate": rate,
            },
        )
    ]


def check_homogeneous_verdicts(
    conn: sqlite3.Connection, run_id: str
) -> list[AuditFinding]:
    """All verdicts in the run share the same verifier_replay value."""
    rows = conn.execute(
        """
        SELECT v.verifier_replay, COUNT(*) AS n
        FROM judge_verdicts v
        JOIN attempts a ON a.attempt_id = v.attempt_id
        JOIN campaigns c ON c.campaign_id = a.campaign_id
        WHERE c.run_id = ?
        GROUP BY v.verifier_replay
        """,
        (run_id,),
    ).fetchall()
    total = sum(int(r["n"]) for r in rows)
    if total < MIN_ATTEMPTS_FOR_HOMOGENEOUS or len(rows) != 1:
        return []
    value = rows[0]["verifier_replay"]
    return [
        AuditFinding(
            check="homogeneous_verdicts",
            severity="warn",
            rationale=(
                f"all {total} verdicts have verifier_replay={value!r} — "
                "target down, judge broken, or trivial run?"
            ),
            evidence={"verdict_value": value, "total": total},
        )
    ]


def check_specialist_failures(
    conn: sqlite3.Connection, run_id: str
) -> list[AuditFinding]:
    """A specialist raised mid-loop and the brief produced no attempt.

    cli.py wraps `_run_one_brief` in try/except so one specialist failure
    no longer aborts the whole run — but the failed brief still produces
    zero attempts, which silently shrinks coverage. This check reads the
    `specialist_failed` agent_events the wrapper emits and surfaces them
    so the operator notices instead of just seeing a smaller verdict
    table at the end.
    """
    rows = conn.execute(
        """
        SELECT campaign_id, payload
        FROM agent_events
        WHERE run_id = ? AND event_type = 'specialist_failed'
        ORDER BY created_at
        """,
        (run_id,),
    ).fetchall()
    findings: list[AuditFinding] = []
    for r in rows:
        try:
            payload = json.loads(r["payload"]) if r["payload"] else {}
        except (json.JSONDecodeError, TypeError):
            payload = {}
        sub = payload.get("subcategory_id", "?")
        etype = payload.get("error_type", "?")
        emsg = payload.get("error_message", "")
        findings.append(
            AuditFinding(
                check="specialist_failure",
                severity="warn",
                rationale=(
                    f"Cat {sub} specialist raised {etype} mid-run "
                    f"(brief executed → no attempt). Message: {emsg[:120]}"
                ),
                evidence={
                    "campaign_id": r["campaign_id"],
                    "subcategory_id": sub,
                    "error_type": etype,
                    "error_message": emsg,
                },
            )
        )
    return findings


def check_severity_inversion(
    conn: sqlite3.Connection, run_id: str
) -> list[AuditFinding]:
    """Critical severity stamped despite verifier_replay=pass and no real signal.

    Flags the case where the static per-subcategory severity rubric (in
    chartbreaker/agents/judge_agent.py) hands out a `critical` label even
    though neither the deterministic verifier nor a real semantic Judge
    flagged the response. That's a static-rubric artifact, not a finding.
    """
    rows = conn.execute(
        """
        SELECT v.attempt_id, v.severity, v.verifier_replay,
               v.semantic, v.rationale
        FROM judge_verdicts v
        JOIN attempts a ON a.attempt_id = v.attempt_id
        JOIN campaigns c ON c.campaign_id = a.campaign_id
        WHERE c.run_id = ?
          AND v.severity = 'critical'
          AND v.verifier_replay = 'pass'
        """,
        (run_id,),
    ).fetchall()
    findings: list[AuditFinding] = []
    for r in rows:
        rationale_low = (r["rationale"] or "").lower()
        canned = any(s in rationale_low for s in _CANNED_RATIONALE_SUBSTRINGS)
        no_semantic = r["semantic"] == "not_run"
        if not (canned or no_semantic):
            continue
        findings.append(
            AuditFinding(
                check="severity_inversion",
                severity="warn",
                rationale=(
                    f"attempt {r['attempt_id'][:8]} marked critical but "
                    "verifier_replay=pass with no semantic signal — "
                    "likely static-rubric artifact"
                ),
                evidence={
                    "attempt_id": r["attempt_id"],
                    "verifier_replay": r["verifier_replay"],
                    "semantic": r["semantic"],
                    "rationale_snippet": (r["rationale"] or "")[:200],
                },
            )
        )
    return findings


_CHECK_FUNCTIONS: tuple = (
    check_acl_breach,
    check_budget_overrun,
    check_agent_looping,
    check_disagreement_spike,
    check_homogeneous_verdicts,
    check_severity_inversion,
    check_specialist_failures,
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def audit_run(
    run_id: str, db_path: str | Path | None = None
) -> AuditReport | None:
    """Run every check against ``run_id``.

    Returns None when ``run_id`` does not exist in the store — the CLI
    layer converts that to exit code 2 (usage error).

    ``db_path`` defaults to the module-level ``RUNS_SQLITE`` at call time
    (not at function-definition time) so tests can monkeypatch the
    constant without rebuilding the function.
    """
    if db_path is None:
        db_path = RUNS_SQLITE
    with _open(db_path) as conn:
        if not _run_exists(conn, run_id):
            return None
        findings: list[AuditFinding] = []
        for fn in _CHECK_FUNCTIONS:
            findings.extend(fn(conn, run_id))
        return AuditReport(
            run_id=run_id,
            started_at=_started_at(conn, run_id),
            findings=findings,
            checks_run=len(_CHECK_FUNCTIONS),
        )


def audit_all(db_path: str | Path | None = None) -> list[AuditReport]:
    """Run audits against every run in the store, most recent first."""
    if db_path is None:
        db_path = RUNS_SQLITE
    with _open(db_path) as conn:
        run_ids = [
            r["run_id"]
            for r in conn.execute(
                "SELECT run_id FROM runs ORDER BY started_at DESC"
            ).fetchall()
        ]
    reports: list[AuditReport] = []
    for rid in run_ids:
        report = audit_run(rid, db_path)
        if report is not None:
            reports.append(report)
    return reports
