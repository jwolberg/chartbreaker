"""Schema-migration tests for Phase 4 (proposed_campaigns table).

Covers spec AC-12 — migration creates the table when absent, re-runs are
no-ops, existing tables are untouched. Mirrors the v1→v2 pattern from
test_observability_migration.py.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chartbreaker.observability.store import ObservabilityStore


# Reuse the v1 schema fixture pattern: seed a pre-v3 DB (this is the
# CURRENT v2 schema, i.e. no proposed_campaigns) and confirm the
# migration adds the table while preserving the existing rows.
_V2_SCHEMA = """
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
INSERT INTO schema_version VALUES (2);

CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    cli_command TEXT NOT NULL,
    operator TEXT NOT NULL,
    target_version TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT
);
CREATE TABLE campaigns (
    campaign_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    subcategory_id TEXT NOT NULL,
    seed_case_id TEXT,
    mutation_budget INTEGER NOT NULL,
    max_cost_usd REAL NOT NULL,
    rationale TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE attempts (
    attempt_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    subcategory_id TEXT NOT NULL,
    specialist TEXT NOT NULL,
    prompt TEXT,
    chart_text_payload TEXT,
    multi_turn_sequence TEXT,
    http_request TEXT,
    mutation_of TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE target_responses (
    attempt_id TEXT PRIMARY KEY,
    http_status INTEGER NOT NULL,
    raw_model_output TEXT,
    post_verifier_output TEXT,
    latency_ms INTEGER NOT NULL,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    audit_log_id TEXT,
    target_version TEXT NOT NULL,
    response_cookies TEXT,
    set_cookie_headers TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE judge_verdicts (
    attempt_id TEXT PRIMARY KEY,
    verifier_replay TEXT NOT NULL,
    semantic TEXT NOT NULL,
    severity TEXT NOT NULL,
    exploitability TEXT NOT NULL,
    rationale TEXT NOT NULL,
    recommended_action TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE findings (
    finding_id TEXT PRIMARY KEY,
    attempt_id TEXT NOT NULL,
    title TEXT NOT NULL,
    severity TEXT NOT NULL,
    body_markdown TEXT NOT NULL,
    promoted_to_published INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    promoted_at TEXT
);
CREATE TABLE costs (
    cost_id INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    attempt_id TEXT,
    agent TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    usd REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE agent_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    campaign_id TEXT,
    attempt_id TEXT,
    agent TEXT NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL
);
"""


def _seed_v2_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(_V2_SCHEMA)
    conn.commit()
    conn.close()


def test_v2_db_gets_proposed_campaigns_table(tmp_path: Path) -> None:
    """Opening a v2 DB via ObservabilityStore creates the v3 table."""
    db_path = tmp_path / "runs.sqlite"
    trace_path = tmp_path / "traces.jsonl"
    _seed_v2_db(db_path)

    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as store:
        # Table is created.
        cols = {
            row[1]
            for row in store._conn.execute(  # noqa: SLF001
                "PRAGMA table_info(proposed_campaigns)"
            ).fetchall()
        }
        assert "proposal_id" in cols
        assert "status" in cols
        assert "mutation_budget" in cols
        assert "run_id" in cols
        # Schema version reports 3 after migration.
        version = store._conn.execute(  # noqa: SLF001
            "SELECT version FROM schema_version"
        ).fetchone()[0]
        assert version == 3


def test_migration_is_idempotent(tmp_path: Path) -> None:
    """Second open over the same DB does not error and reports the same shape."""
    db_path = tmp_path / "runs.sqlite"
    trace_path = tmp_path / "traces.jsonl"
    _seed_v2_db(db_path)

    with ObservabilityStore(db_path=db_path, trace_path=trace_path):
        pass
    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as store:
        cols = {
            row[1]
            for row in store._conn.execute(  # noqa: SLF001
                "PRAGMA table_info(proposed_campaigns)"
            ).fetchall()
        }
        assert "proposal_id" in cols
        version = store._conn.execute(  # noqa: SLF001
            "SELECT version FROM schema_version"
        ).fetchone()[0]
        assert version == 3


def test_fresh_db_creates_proposed_campaigns(tmp_path: Path) -> None:
    """A brand-new DB (no prior schema) starts at v3 with the table present."""
    db_path = tmp_path / "fresh.sqlite"
    trace_path = tmp_path / "traces.jsonl"
    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as store:
        # Table exists.
        rows = store._conn.execute(  # noqa: SLF001
            "SELECT name FROM sqlite_master WHERE type='table' AND name='proposed_campaigns'"
        ).fetchall()
        assert rows
        # Indexes exist.
        idx_names = {
            row[0]
            for row in store._conn.execute(  # noqa: SLF001
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='proposed_campaigns'"
            ).fetchall()
        }
        assert "idx_proposed_campaigns_status" in idx_names
        assert "idx_proposed_campaigns_triple" in idx_names


def test_status_check_constraint_rejects_unknown_value(tmp_path: Path) -> None:
    """The CHECK(status IN (...)) constraint blocks arbitrary strings."""
    db_path = tmp_path / "fresh.sqlite"
    trace_path = tmp_path / "traces.jsonl"
    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as store:
        try:
            store._conn.execute(  # noqa: SLF001
                "INSERT INTO proposed_campaigns "
                "(proposal_id, created_at, subcategory_id, specialist, "
                " mutation_budget, rationale, priority_score, est_cost_usd, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("p1", "2026-05-12T00:00:00", "1b", "injector", 1, "r", 1.0, 0.001, "weird"),
            )
        except sqlite3.IntegrityError:
            pass
        else:
            raise AssertionError("CHECK constraint failed to reject 'weird' status")
