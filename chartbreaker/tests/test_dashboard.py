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


def test_safe_json_pretty_returns_pretty_for_valid_json() -> None:
    out = dashboard._safe_json_pretty('{"b":2,"a":1}')
    # Pretty-printed → multi-line with indentation.
    assert "\n" in out
    assert "\"b\": 2" in out
    assert "\"a\": 1" in out


def test_safe_json_pretty_passes_through_non_json() -> None:
    assert dashboard._safe_json_pretty("not json at all") == "not json at all"


def test_safe_json_pretty_handles_none_and_empty() -> None:
    assert dashboard._safe_json_pretty(None) == ""
    assert dashboard._safe_json_pretty("") == ""


def test_attempt_detail_query_pulls_full_record(tmp_path: Path) -> None:
    """Smoke-test the SQL the drill-down page uses to load an attempt."""
    db_path = _make_populated_db(tmp_path)
    attempts_df = dashboard._load_table(
        str(db_path), "SELECT attempt_id FROM attempts LIMIT 1"
    )
    aid = attempts_df.iloc[0]["attempt_id"]

    detail = dashboard._load_table(
        str(db_path),
        f"""SELECT a.*, c.run_id, c.subcategory_id AS campaign_sub,
                   c.rationale AS campaign_rationale, c.seed_case_id
            FROM attempts a
            JOIN campaigns c ON c.campaign_id = a.campaign_id
            WHERE a.attempt_id = '{aid}'""",
    )
    assert len(detail) == 1
    assert detail.iloc[0]["specialist"] == "injector"
    assert detail.iloc[0]["campaign_rationale"] == "dashboard smoke test"


def test_rationale_filter_empty_needle_returns_unfiltered() -> None:
    df = pd.DataFrame(
        {
            "rationale": ["Persona shift detected", "Source ID forgery", "clean"],
        }
    )
    assert len(dashboard._apply_rationale_filter(df, "")) == 3
    assert len(dashboard._apply_rationale_filter(df, "  ")) == 3  # whitespace = no filter


def test_rationale_filter_case_insensitive_substring() -> None:
    df = pd.DataFrame(
        {
            "rationale": [
                "Persona shift detected",
                "Source ID forgery",
                "DISAGREEMENT semantic vs verifier_replay",
                None,
            ],
        }
    )
    matched = dashboard._apply_rationale_filter(df, "PERSONA")
    assert len(matched) == 1
    matched = dashboard._apply_rationale_filter(df, "disagreement")
    assert len(matched) == 1
    matched = dashboard._apply_rationale_filter(df, "nope")
    assert matched.empty


def test_rationale_filter_handles_empty_dataframe() -> None:
    df = pd.DataFrame({"rationale": []})
    assert dashboard._apply_rationale_filter(df, "anything").empty


def test_check_api_key_prereqs_lists_missing_vars(monkeypatch: "pytest.MonkeyPatch") -> None:
    # Wipe everything required so we can see the full failure list.
    for var in (
        "CHARTBREAKER_TARGET_USER",
        "CHARTBREAKER_TARGET_PASSWORD",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    missing = dashboard._check_api_key_prereqs(semantic_judge=True)
    assert "CHARTBREAKER_TARGET_USER" in missing
    assert "CHARTBREAKER_TARGET_PASSWORD" in missing
    assert "OPENROUTER_API_KEY" in missing
    assert "OPENAI_API_KEY" in missing


def test_check_api_key_prereqs_skips_openai_when_semantic_judge_off(
    monkeypatch: "pytest.MonkeyPatch",
) -> None:
    monkeypatch.setenv("CHARTBREAKER_TARGET_USER", "x")
    monkeypatch.setenv("CHARTBREAKER_TARGET_PASSWORD", "x")
    monkeypatch.setenv("OPENROUTER_API_KEY", "x")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Semantic Judge off → OpenAI key not required.
    assert dashboard._check_api_key_prereqs(semantic_judge=False) == []
    # Semantic Judge on → OpenAI key required.
    assert dashboard._check_api_key_prereqs(semantic_judge=True) == ["OPENAI_API_KEY"]


def test_detect_in_flight_run_returns_none_when_no_runs(tmp_path: Path) -> None:
    db_path = tmp_path / "empty.sqlite"
    # Initialize an empty store schema.
    with ObservabilityStore(db_path=db_path, trace_path=tmp_path / "t.jsonl"):
        pass
    assert dashboard._detect_in_flight_run(str(db_path)) is None


def test_detect_in_flight_run_returns_open_run(tmp_path: Path) -> None:
    db_path = _make_populated_db(tmp_path)
    # The populated DB ended_at-flagged its run; mark it open again.
    conn = sqlite3.connect(str(db_path))
    conn.execute("UPDATE runs SET ended_at = NULL")
    conn.commit()
    conn.close()

    row = dashboard._detect_in_flight_run(str(db_path))
    assert row is not None
    assert row["run_id"] == "run-1"
    assert row["ended_at"] is None if "ended_at" in row else True  # column omitted in select is fine


def test_detect_in_flight_run_handles_missing_db_file(tmp_path: Path) -> None:
    """If the DB file doesn't exist yet (fresh install), no in-flight run."""
    assert dashboard._detect_in_flight_run(str(tmp_path / "does-not-exist.sqlite")) is None


def test_live_activity_query_returns_recent_events(tmp_path: Path) -> None:
    """The live tab pulls the latest N agent_events ordered by event_id desc."""
    db_path = _make_populated_db(tmp_path)
    df = dashboard._load_table(
        str(db_path),
        "SELECT created_at, agent, event_type, attempt_id, payload "
        "FROM agent_events ORDER BY event_id DESC LIMIT 50",
    )
    # The populated DB writes events for run_started, campaign_emitted,
    # attempt_generated, response_received, verdict_recorded, run_ended.
    assert not df.empty
    events = set(df["event_type"].tolist())
    assert "verdict_recorded" in events
    assert "campaign_emitted" in events
