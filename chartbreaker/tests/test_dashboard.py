"""Smoke tests for the Streamlit dashboard module.

We don't render the Streamlit components (that would require a live
server); instead we verify the dashboard module imports and the
query layer works against a real SQLite DB built from the schema.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from chartbreaker.observability import dashboard
from chartbreaker.observability.store import ObservabilityStore


def _make_populated_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "runs.sqlite"
    trace_path = tmp_path / "traces.jsonl"

    from chartbreaker.state import (
        AttackAttempt,
        CampaignBrief,
        CostObservation,
        TargetResponse,
        Verdict,
    )

    brief = CampaignBrief(
        subcategory_id="1b",
        mutation_budget=1,
        max_cost_usd=0.10,
        rationale="dashboard smoke test",
    )
    attempt = AttackAttempt(
        campaign_id=brief.campaign_id,
        subcategory_id="1b",
        specialist="injector",
        prompt="hi",
    )
    response = TargetResponse(
        attempt_id=attempt.attempt_id,
        http_status=200,
        raw_model_output='{"sources": []}',
        post_verifier_output='{"sources": []}',
        latency_ms=42,
        target_version="gpt-5.4-mini",
    )
    verdict = Verdict(
        attempt_id=attempt.attempt_id,
        verifier_replay="pass",
        semantic="pass",
        severity="info",
        exploitability="moderate",
        rationale="ok",
        recommended_action="discard",
        judge_model="deterministic",
    )
    cost = CostObservation(
        campaign_id=brief.campaign_id,
        attempt_id=attempt.attempt_id,
        agent="injector",
        provider="openrouter",
        model="hermes",
        prompt_tokens=10,
        completion_tokens=5,
        usd=0.0001,
    )

    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as store:
        store.start_run("run-1", cli_command="test", operator="test")
        store.write_campaign("run-1", brief)
        store.write_attempt("run-1", attempt)
        store.write_target_response("run-1", response)
        store.write_verdict("run-1", verdict)
        store.write_cost("run-1", cost)
        store.end_run("run-1")
    return db_path


def test_load_table_returns_dataframe(tmp_path: Path) -> None:
    db_path = _make_populated_db(tmp_path)
    df = dashboard._load_table(str(db_path), "SELECT * FROM runs")
    assert isinstance(df, pd.DataFrame)
    assert len(df) == 1
    assert df.iloc[0]["operator"] == "test"


def test_load_table_handles_empty(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.sqlite"
    # Create a DB with just the schema, no rows.
    with ObservabilityStore(db_path=db_path, trace_path=tmp_path / "t.jsonl"):
        pass
    df = dashboard._load_table(str(db_path), "SELECT * FROM attempts")
    assert df.empty


def test_filter_by_run_all_returns_unfiltered(tmp_path: Path) -> None:
    db_path = _make_populated_db(tmp_path)
    df = dashboard._load_table(
        str(db_path),
        "SELECT a.*, c.run_id FROM attempts a JOIN campaigns c "
        "ON c.campaign_id = a.campaign_id",
    )
    filtered = dashboard._filter_by_run(df, "run_id", "ALL")
    assert len(filtered) == len(df)


def test_filter_by_run_specific_id(tmp_path: Path) -> None:
    db_path = _make_populated_db(tmp_path)
    df = dashboard._load_table(
        str(db_path),
        "SELECT a.*, c.run_id FROM attempts a JOIN campaigns c "
        "ON c.campaign_id = a.campaign_id",
    )
    filtered = dashboard._filter_by_run(df, "run_id", "run-1")
    assert len(filtered) == 1
    filtered_other = dashboard._filter_by_run(df, "run_id", "does-not-exist")
    assert filtered_other.empty
