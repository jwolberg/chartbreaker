"""Tests for the schema_version 1 → 2 migration in ObservabilityStore.

Confirms that an existing v1 DB (with the old target_responses shape)
picks up the new response_cookies + set_cookie_headers columns when
ObservabilityStore initializes against it.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from chartbreaker.observability.store import ObservabilityStore

_V1_SCHEMA = """
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
INSERT INTO schema_version VALUES (1);

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


def _seed_v1_db(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    conn.executescript(_V1_SCHEMA)
    conn.commit()
    conn.close()


def test_v1_db_gets_response_cookie_columns(tmp_path: Path) -> None:
    """Opening a v1 DB with ObservabilityStore adds the v2 columns idempotently."""
    db_path = tmp_path / "runs.sqlite"
    trace_path = tmp_path / "traces.jsonl"
    _seed_v1_db(db_path)

    # First open: should ALTER TABLE and add new columns.
    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as store:
        cols = {
            row[1]
            for row in store._conn.execute("PRAGMA table_info(target_responses)").fetchall()
        }
        assert "response_cookies" in cols
        assert "set_cookie_headers" in cols
        version = store._conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]
        # P4 bumped the canonical schema_version to 3 (proposed_campaigns).
        assert version == 3

    # Second open: should be a no-op (no duplicate-column error).
    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as store:
        cols = {
            row[1]
            for row in store._conn.execute("PRAGMA table_info(target_responses)").fetchall()
        }
        assert "response_cookies" in cols
