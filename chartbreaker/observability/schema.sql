-- ChartBreaker observability schema (SQLite, schema_version = 1).
-- Matches docs/PROJECT_STRATEGY.md § Logging and State Store Requirement.
-- Column shapes follow the Pydantic state objects in chartbreaker/state.py.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ---------------------------------------------------------------------------
-- schema_version: single-row table that lets future migrations refuse to
-- run against a DB they don't understand.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
INSERT OR IGNORE INTO schema_version (version) VALUES (1);

-- ---------------------------------------------------------------------------
-- runs: one row per CLI invocation or scheduled CI sweep.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT    PRIMARY KEY,
    cli_command     TEXT    NOT NULL,
    operator        TEXT    NOT NULL,    -- "laptop:<user>" or "ci"
    target_version  TEXT,                -- recorded once the first response lands
    started_at      TEXT    NOT NULL,
    ended_at        TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);

-- ---------------------------------------------------------------------------
-- campaigns: Orchestrator-emitted CampaignBrief rows.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS campaigns (
    campaign_id      TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL REFERENCES runs(run_id),
    subcategory_id   TEXT NOT NULL,
    seed_case_id     TEXT,
    mutation_budget  INTEGER NOT NULL,
    max_cost_usd     REAL    NOT NULL,
    rationale        TEXT    NOT NULL,
    created_at       TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_campaigns_subcategory ON campaigns(subcategory_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_run ON campaigns(run_id);

-- ---------------------------------------------------------------------------
-- attempts: every adversarial AttackAttempt the specialists generate.
-- The shape-specific payload columns are JSON-encoded where they're complex;
-- exactly one of (prompt, chart_text_payload, multi_turn_sequence,
-- http_request) is populated per row.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id          TEXT PRIMARY KEY,
    campaign_id         TEXT NOT NULL REFERENCES campaigns(campaign_id),
    subcategory_id      TEXT NOT NULL,
    specialist          TEXT NOT NULL,
    prompt              TEXT,
    chart_text_payload  TEXT,
    multi_turn_sequence TEXT,    -- JSON array of strings
    http_request        TEXT,    -- JSON HttpRequestShape
    mutation_of         TEXT,    -- parent attempt_id if mutation
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_attempts_campaign ON attempts(campaign_id);
CREATE INDEX IF NOT EXISTS idx_attempts_subcategory ON attempts(subcategory_id);
CREATE INDEX IF NOT EXISTS idx_attempts_specialist ON attempts(specialist);

-- ---------------------------------------------------------------------------
-- target_responses: one row per attempt, captured by target_client.dispatch.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS target_responses (
    attempt_id            TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
    http_status           INTEGER NOT NULL,
    raw_model_output      TEXT,
    post_verifier_output  TEXT,
    latency_ms            INTEGER NOT NULL,
    prompt_tokens         INTEGER,
    completion_tokens     INTEGER,
    audit_log_id          TEXT,
    target_version        TEXT NOT NULL,
    created_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_target_responses_status ON target_responses(http_status);

-- ---------------------------------------------------------------------------
-- judge_verdicts: Judge's two-part verdict per attempt.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS judge_verdicts (
    attempt_id           TEXT PRIMARY KEY REFERENCES attempts(attempt_id),
    verifier_replay      TEXT NOT NULL CHECK (verifier_replay IN ('pass', 'fail')),
    semantic             TEXT NOT NULL CHECK (semantic IN ('pass', 'partial', 'fail', 'not_run')),
    severity             TEXT NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    exploitability       TEXT NOT NULL CHECK (exploitability IN ('trivial', 'easy', 'moderate', 'hard')),
    rationale            TEXT NOT NULL,
    recommended_action   TEXT NOT NULL CHECK (recommended_action IN ('regression', 'mutate', 'escalate', 'discard')),
    judge_model          TEXT NOT NULL,
    created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verdicts_severity ON judge_verdicts(severity);
CREATE INDEX IF NOT EXISTS idx_verdicts_semantic ON judge_verdicts(semantic);

-- ---------------------------------------------------------------------------
-- findings: Scribe-drafted vulnerability reports.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS findings (
    finding_id              TEXT PRIMARY KEY,    -- e.g. "AF-001"
    attempt_id              TEXT NOT NULL REFERENCES attempts(attempt_id),
    title                   TEXT NOT NULL,
    severity                TEXT NOT NULL CHECK (severity IN ('info', 'low', 'medium', 'high', 'critical')),
    body_markdown           TEXT NOT NULL,
    promoted_to_published   INTEGER NOT NULL DEFAULT 0,   -- 0/1 for SQLite boolean
    created_at              TEXT NOT NULL,
    promoted_at             TEXT
);
CREATE INDEX IF NOT EXISTS idx_findings_severity ON findings(severity);
CREATE INDEX IF NOT EXISTS idx_findings_promoted ON findings(promoted_to_published);

-- ---------------------------------------------------------------------------
-- costs: per-LLM-call telemetry. Aggregated by agent / campaign for budgets.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS costs (
    cost_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id       TEXT NOT NULL REFERENCES campaigns(campaign_id),
    attempt_id        TEXT,
    agent             TEXT NOT NULL,
    provider          TEXT NOT NULL,
    model             TEXT NOT NULL,
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    usd               REAL NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_costs_campaign ON costs(campaign_id);
CREATE INDEX IF NOT EXISTS idx_costs_agent ON costs(agent);

-- ---------------------------------------------------------------------------
-- agent_events: append-only timeline of state transitions. Single source of
-- truth for "what did the agents just do?" queries from the dashboard.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_events (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES runs(run_id),
    campaign_id    TEXT,
    attempt_id     TEXT,
    agent          TEXT NOT NULL,
    event_type     TEXT NOT NULL,    -- "campaign_emitted", "attempt_dispatched", "verdict_recorded", etc.
    payload        TEXT,             -- optional JSON detail
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_events_run ON agent_events(run_id);
CREATE INDEX IF NOT EXISTS idx_agent_events_attempt ON agent_events(attempt_id);
CREATE INDEX IF NOT EXISTS idx_agent_events_agent ON agent_events(agent);
CREATE INDEX IF NOT EXISTS idx_agent_events_created ON agent_events(created_at);
