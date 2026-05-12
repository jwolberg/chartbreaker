"""SQLite + JSONL observability store for ChartBreaker.

Every agent state-transition writes to:
- the primary table for that object (campaigns / attempts / target_responses /
  judge_verdicts / findings / costs),
- the `agent_events` timeline,
- and the append-only `observability/traces.jsonl` mirror.

The schema is loaded from schema.sql on first init. Schema migrations are
version-pinned (`schema_version` table) but no migrations exist yet — bump
this when v2 ships.

Single-threaded by design; the async loop is the only writer. For Phase 2
multi-writer scenarios, swap to Postgres per the deferred decision in
docs/PROJECT_STRATEGY.md § Deliberately Deferred.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from chartbreaker.config import RUNS_SQLITE, TRACES_JSONL
from chartbreaker.state import (
    AttackAttempt,
    CampaignBrief,
    CostObservation,
    RegressionReport,
    ReportDraft,
    TargetResponse,
    Verdict,
)

logger = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def _iso(dt: datetime) -> str:
    """Render a datetime as ISO-8601 with UTC timezone for SQLite TEXT storage."""
    return dt.astimezone(timezone.utc).isoformat()


def _utcnow_iso() -> str:
    return _iso(datetime.now(tz=timezone.utc))


def _json_dump(obj: object) -> str | None:
    """JSON-dump or None-pass-through. Used for nullable JSON columns."""
    return None if obj is None else json.dumps(obj, default=str, separators=(",", ":"))


class ObservabilityStore:
    """SQLite + JSONL store. Use as a context manager.

    Usage:
        with ObservabilityStore() as store:
            run_id = store.start_run(cli_command="chartbreaker run --campaign 1b",
                                     operator="laptop:jmwolberg")
            store.write_campaign(brief, run_id)
            store.write_attempt(attempt)
            store.write_target_response(response)
            store.write_verdict(verdict)
            store.end_run(run_id)
    """

    def __init__(
        self,
        db_path: str | Path = RUNS_SQLITE,
        trace_path: str | Path = TRACES_JSONL,
    ) -> None:
        self._db_path = Path(db_path)
        self._trace_path = Path(trace_path)
        self._conn: sqlite3.Connection | None = None

    def __enter__(self) -> "ObservabilityStore":
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._trace_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._db_path),
            isolation_level=None,  # autocommit
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._conn is not None:
            self._conn.close()
        self._conn = None

    def _ensure_schema(self) -> None:
        assert self._conn is not None
        schema_sql = _SCHEMA_PATH.read_text()
        self._conn.executescript(schema_sql)
        self._migrate_to_v2()
        self._migrate_to_v3()

    def _migrate_to_v2(self) -> None:
        """Add v2 columns to pre-existing DBs created under schema v1.

        SQLite's CREATE TABLE IF NOT EXISTS does not add columns to an
        existing table, so we issue conditional ALTER TABLE statements
        when columns are missing. Idempotent.
        """
        assert self._conn is not None
        existing_cols = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(target_responses)").fetchall()
        }
        if "response_cookies" not in existing_cols:
            self._conn.execute(
                "ALTER TABLE target_responses ADD COLUMN response_cookies TEXT"
            )
        if "set_cookie_headers" not in existing_cols:
            self._conn.execute(
                "ALTER TABLE target_responses ADD COLUMN set_cookie_headers TEXT"
            )
        # Retire any pre-v2 schema_version rows. schema.sql's
        # INSERT OR IGNORE already added the (2) row, so this just removes
        # the stale (1) row when migrating an existing v1 DB.
        self._conn.execute("DELETE FROM schema_version WHERE version < 2")

    def _migrate_to_v3(self) -> None:
        """Bring v2 DBs up to v3 (Phase-4: proposed_campaigns table).

        The table itself is created by schema.sql's CREATE TABLE IF NOT
        EXISTS — purely additive, no ALTER on existing tables. The only
        per-migration work here is retiring stale schema_version rows so
        a v2 DB ends up reporting version=3 after this runs. Idempotent.
        """
        assert self._conn is not None
        self._conn.execute("DELETE FROM schema_version WHERE version < 3")

    # ------------------------------------------------------------------
    # Trace mirror
    # ------------------------------------------------------------------

    def _append_trace(self, event: dict) -> None:
        """Append one JSON line to traces.jsonl. Best-effort; logs but never raises."""
        try:
            with self._trace_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, default=str, separators=(",", ":")))
                f.write("\n")
        except OSError as exc:
            logger.warning("could not append to traces.jsonl: %s", exc)

    def _emit_event(
        self,
        run_id: str,
        agent: str,
        event_type: str,
        *,
        campaign_id: str | None = None,
        attempt_id: str | None = None,
        payload: dict | None = None,
    ) -> None:
        """Insert an agent_events row AND append to traces.jsonl."""
        assert self._conn is not None
        ts = _utcnow_iso()
        self._conn.execute(
            "INSERT INTO agent_events "
            "(run_id, campaign_id, attempt_id, agent, event_type, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, campaign_id, attempt_id, agent, event_type, _json_dump(payload), ts),
        )
        self._append_trace(
            {
                "ts": ts,
                "run_id": run_id,
                "campaign_id": campaign_id,
                "attempt_id": attempt_id,
                "agent": agent,
                "event": event_type,
                **({"payload": payload} if payload else {}),
            }
        )

    # ------------------------------------------------------------------
    # Run lifecycle
    # ------------------------------------------------------------------

    def start_run(self, run_id: str, cli_command: str, operator: str) -> None:
        assert self._conn is not None
        ts = _utcnow_iso()
        self._conn.execute(
            "INSERT INTO runs (run_id, cli_command, operator, started_at) "
            "VALUES (?, ?, ?, ?)",
            (run_id, cli_command, operator, ts),
        )
        self._emit_event(run_id, "system", "run_started", payload={"cli": cli_command})

    def end_run(self, run_id: str) -> None:
        assert self._conn is not None
        ts = _utcnow_iso()
        self._conn.execute("UPDATE runs SET ended_at = ? WHERE run_id = ?", (ts, run_id))
        self._emit_event(run_id, "system", "run_ended")

    # ------------------------------------------------------------------
    # State-object writers
    # ------------------------------------------------------------------

    def write_campaign(self, run_id: str, brief: CampaignBrief) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO campaigns "
            "(campaign_id, run_id, subcategory_id, seed_case_id, mutation_budget, "
            " max_cost_usd, rationale, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                brief.campaign_id,
                run_id,
                brief.subcategory_id,
                brief.seed_case_id,
                brief.mutation_budget,
                brief.max_cost_usd,
                brief.rationale,
                _iso(brief.created_at),
            ),
        )
        self._emit_event(
            run_id,
            "orchestrator",
            "campaign_emitted",
            campaign_id=brief.campaign_id,
            payload={
                "subcategory_id": brief.subcategory_id,
                "mutation_budget": brief.mutation_budget,
            },
        )

    def write_attempt(self, run_id: str, attempt: AttackAttempt) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO attempts "
            "(attempt_id, campaign_id, subcategory_id, specialist, prompt, "
            " chart_text_payload, multi_turn_sequence, http_request, mutation_of, "
            " created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                attempt.attempt_id,
                attempt.campaign_id,
                attempt.subcategory_id,
                attempt.specialist,
                attempt.prompt,
                attempt.chart_text_payload,
                _json_dump(attempt.multi_turn_sequence),
                _json_dump(
                    attempt.http_request.model_dump() if attempt.http_request else None
                ),
                attempt.mutation_of,
                _iso(attempt.created_at),
            ),
        )
        self._emit_event(
            run_id,
            attempt.specialist,
            "attempt_generated",
            campaign_id=attempt.campaign_id,
            attempt_id=attempt.attempt_id,
        )

    def write_target_response(self, run_id: str, response: TargetResponse) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO target_responses "
            "(attempt_id, http_status, raw_model_output, post_verifier_output, "
            " latency_ms, prompt_tokens, completion_tokens, audit_log_id, "
            " target_version, response_cookies, set_cookie_headers, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                response.attempt_id,
                response.http_status,
                response.raw_model_output,
                response.post_verifier_output,
                response.latency_ms,
                response.prompt_tokens,
                response.completion_tokens,
                response.audit_log_id,
                response.target_version,
                _json_dump(response.response_cookies),
                _json_dump(response.set_cookie_headers),
                _iso(response.created_at),
            ),
        )
        # Update the run's recorded target_version once we have one.
        self._conn.execute(
            "UPDATE runs SET target_version = ? "
            "WHERE run_id = ? AND target_version IS NULL",
            (response.target_version, run_id),
        )
        self._emit_event(
            run_id,
            "target_client",
            "response_received",
            attempt_id=response.attempt_id,
            payload={"http_status": response.http_status, "latency_ms": response.latency_ms},
        )

    def write_verdict(self, run_id: str, verdict: Verdict) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO judge_verdicts "
            "(attempt_id, verifier_replay, semantic, severity, exploitability, "
            " rationale, recommended_action, judge_model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                verdict.attempt_id,
                verdict.verifier_replay,
                verdict.semantic,
                verdict.severity,
                verdict.exploitability,
                verdict.rationale,
                verdict.recommended_action,
                verdict.judge_model,
                _iso(verdict.created_at),
            ),
        )
        self._emit_event(
            run_id,
            "judge",
            "verdict_recorded",
            attempt_id=verdict.attempt_id,
            payload={
                "verifier_replay": verdict.verifier_replay,
                "semantic": verdict.semantic,
                "severity": verdict.severity,
            },
        )

    def write_finding(self, run_id: str, draft: ReportDraft) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO findings "
            "(finding_id, attempt_id, title, severity, body_markdown, "
            " promoted_to_published, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                draft.finding_id,
                draft.attempt_id,
                draft.title,
                draft.severity,
                draft.body_markdown,
                1 if draft.promoted_to_published else 0,
                _iso(draft.created_at),
            ),
        )
        self._emit_event(
            run_id,
            "scribe",
            "finding_drafted",
            attempt_id=draft.attempt_id,
            payload={"finding_id": draft.finding_id, "severity": draft.severity},
        )

    def write_cost(self, run_id: str, cost: CostObservation) -> None:
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO costs "
            "(campaign_id, attempt_id, agent, provider, model, "
            " prompt_tokens, completion_tokens, usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                cost.campaign_id,
                cost.attempt_id,
                cost.agent,
                cost.provider,
                cost.model,
                cost.prompt_tokens,
                cost.completion_tokens,
                cost.usd,
                _iso(cost.created_at),
            ),
        )
        # No agent_event row for cost — it's a side-channel, would flood the timeline.
        self._append_trace(
            {
                "ts": _utcnow_iso(),
                "run_id": run_id,
                "campaign_id": cost.campaign_id,
                "attempt_id": cost.attempt_id,
                "agent": cost.agent,
                "event": "cost_recorded",
                "provider": cost.provider,
                "model": cost.model,
                "usd": cost.usd,
            }
        )

    # ------------------------------------------------------------------
    # Read helpers (used by the dashboard and the Orchestrator)
    # ------------------------------------------------------------------

    def attempts_per_subcategory(self) -> dict[str, int]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT subcategory_id, COUNT(*) AS n FROM attempts GROUP BY subcategory_id"
        ).fetchall()
        return {row["subcategory_id"]: row["n"] for row in rows}

    def cost_per_agent(self, run_id: str | None = None) -> dict[str, float]:
        assert self._conn is not None
        if run_id is None:
            rows = self._conn.execute(
                "SELECT agent, SUM(usd) AS total FROM costs GROUP BY agent"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT c.agent, SUM(c.usd) AS total "
                "FROM costs c "
                "JOIN campaigns cm ON cm.campaign_id = c.campaign_id "
                "WHERE cm.run_id = ? GROUP BY c.agent",
                (run_id,),
            ).fetchall()
        return {row["agent"]: row["total"] for row in rows}

    # ------------------------------------------------------------------
    # Orchestrator telemetry helpers — wire the priority formula's inputs.
    # See docs/ARCHITECTURE.md § Orchestration Strategy.
    # ------------------------------------------------------------------

    def cost_total_for_run(self, run_id: str) -> float:
        """Sum every LLM-call USD recorded for a run. 0.0 if none yet."""
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT COALESCE(SUM(c.usd), 0.0) AS total "
            "FROM costs c JOIN campaigns cm ON cm.campaign_id = c.campaign_id "
            "WHERE cm.run_id = ?",
            (run_id,),
        ).fetchone()
        return float(row["total"]) if row else 0.0

    def attempts_in_subcategory_for_run(self, run_id: str, subcategory_id: str) -> int:
        """Count attempts already dispatched against `subcategory_id` in this run.

        Used by the Orchestrator's `coverage_ratio` term to deprioritize
        subcategories the platform has already exercised in the current
        run — the more attempts we've already made on a vector, the less
        urgent the next attempt on the same vector becomes.
        """
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT COUNT(*) AS n "
            "FROM attempts a JOIN campaigns cm ON cm.campaign_id = a.campaign_id "
            "WHERE cm.run_id = ? AND a.subcategory_id = ?",
            (run_id, subcategory_id),
        ).fetchone()
        return int(row["n"]) if row else 0

    def recent_target_versions(self, limit: int = 2) -> list[str]:
        """Return the N most-recent distinct `target_version` values across runs.

        The Orchestrator's `recent_target_change_signal` lights up when
        len(versions) >= 2 and they differ — i.e. the target deployed a
        new model since the last sweep, so the Orchestrator should
        re-attack with elevated priority across the board.
        """
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT DISTINCT target_version FROM runs "
            "WHERE target_version IS NOT NULL "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [row["target_version"] for row in rows if row["target_version"]]

    # ------------------------------------------------------------------
    # proposed_campaigns CRUD (Phase 4 — Orchestrator Approval Harness).
    # The harness module (chartbreaker.orchestrator.proposal_harness) is
    # the only producer of these rows. The Streamlit "Plan Next Run" tab
    # is the only consumer of pending-state reads. Approved rows are
    # executed by reusing _run_one_brief() from chartbreaker.cli — these
    # CRUD helpers carry only the queue lifecycle, not the run itself.
    # See docs/spec.md and BUILD_PLAN § P4-T1.
    # ------------------------------------------------------------------

    def insert_proposed_campaign(
        self,
        *,
        proposal_id: str,
        created_at: str,
        subcategory_id: str,
        specialist: str,
        seed_case_id: str | None,
        mutation_budget: int,
        rationale: str,
        priority_score: float,
        est_cost_usd: float,
        parent_finding_id: str | None,
        status: str,
        decided_at: str | None = None,
        decided_by: str | None = None,
        rejection_reason: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Insert one proposed_campaigns row. Caller supplies all fields."""
        assert self._conn is not None
        self._conn.execute(
            "INSERT INTO proposed_campaigns "
            "(proposal_id, created_at, subcategory_id, specialist, seed_case_id, "
            " mutation_budget, rationale, priority_score, est_cost_usd, "
            " parent_finding_id, status, decided_at, decided_by, "
            " rejection_reason, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                proposal_id,
                created_at,
                subcategory_id,
                specialist,
                seed_case_id,
                mutation_budget,
                rationale,
                priority_score,
                est_cost_usd,
                parent_finding_id,
                status,
                decided_at,
                decided_by,
                rejection_reason,
                run_id,
            ),
        )

    def update_proposed_campaign_status(
        self,
        proposal_id: str,
        *,
        status: str,
        decided_at: str | None = None,
        decided_by: str | None = None,
        rejection_reason: str | None = None,
        run_id: str | None = None,
    ) -> None:
        """Transition a proposal's status and stamp the decision fields.

        Only the fields the caller actually sets are written; None leaves
        the column unchanged so a later approve→execute transition does
        not blow away the decided_at/by recorded at approval time.
        """
        assert self._conn is not None
        sets = ["status = ?"]
        args: list[object] = [status]
        if decided_at is not None:
            sets.append("decided_at = ?")
            args.append(decided_at)
        if decided_by is not None:
            sets.append("decided_by = ?")
            args.append(decided_by)
        if rejection_reason is not None:
            sets.append("rejection_reason = ?")
            args.append(rejection_reason)
        if run_id is not None:
            sets.append("run_id = ?")
            args.append(run_id)
        args.append(proposal_id)
        self._conn.execute(
            f"UPDATE proposed_campaigns SET {', '.join(sets)} WHERE proposal_id = ?",
            args,
        )

    def set_proposed_campaign_mutation_budget(
        self, proposal_id: str, mutation_budget: int
    ) -> None:
        """Persist a user-edited mutation budget. Independent of status."""
        assert self._conn is not None
        self._conn.execute(
            "UPDATE proposed_campaigns SET mutation_budget = ? "
            "WHERE proposal_id = ?",
            (mutation_budget, proposal_id),
        )

    def list_proposed_campaigns(
        self, status: str | None = None
    ) -> list[dict]:
        """Return proposed_campaigns rows as a list of dicts. Most-recent first."""
        assert self._conn is not None
        if status is None:
            rows = self._conn.execute(
                "SELECT * FROM proposed_campaigns ORDER BY created_at DESC"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM proposed_campaigns WHERE status = ? "
                "ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_proposed_campaign(self, proposal_id: str) -> dict | None:
        assert self._conn is not None
        row = self._conn.execute(
            "SELECT * FROM proposed_campaigns WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        return dict(row) if row else None
