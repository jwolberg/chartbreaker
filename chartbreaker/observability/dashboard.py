"""ChartBreaker observability dashboard (Streamlit, local).

Read-only Streamlit app that answers the rubric's observability
questions directly from `observability/runs.sqlite`:

  - per-category coverage (how many subcategories attempted, how many
    attempts per subcategory)
  - pass / fail rate (verifier + semantic Judge)
  - resilience trend (verdicts over time)
  - open vulnerabilities (Judge action = regression that's still failing)
  - per-run cost
  - per-agent activity timeline
  - **per-attempt drill-down** (P2.5-T1) — click an attempt_id to see
    its full prompt + target response + Judge rationale + costs on
    one screen.
  - **live activity tab** (P2.5-T3) — auto-refreshes every 2s to show
    the most recent agent_events.

Per docs/PROJECT_STRATEGY.md § Operating Model § Hosting Topology and
docs/ARCHITECTURE.md § Observability Layer, this dashboard runs locally
on the operator's laptop (`streamlit run chartbreaker/observability/
dashboard.py`); it is NOT publicly hosted. The OpenEMR target remains
the only public surface in the system.

Run with:
    streamlit run chartbreaker/observability/dashboard.py
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

from chartbreaker.config import RUNS_SQLITE


@st.cache_data(ttl=10)
def _load_table(db_path: str, query: str) -> pd.DataFrame:
    """Run a SELECT and return the rows as a DataFrame. Cached briefly so
    repeated dashboard interactions don't re-query."""
    conn = sqlite3.connect(db_path)
    try:
        return pd.read_sql_query(query, conn)
    finally:
        conn.close()


def _empty_state_check(df: pd.DataFrame, label: str) -> bool:
    if df.empty:
        st.info(f"No {label} recorded yet. Run `chartbreaker run-mvp-loop`.")
        return True
    return False


def _render_header(db_path: str) -> None:
    st.set_page_config(
        page_title="ChartBreaker Dashboard",
        layout="wide",
        page_icon="🔍",
    )
    st.title("ChartBreaker — Observability Dashboard")
    st.caption(
        f"Local read-only view of `{db_path}`. "
        "ChartBreaker dashboard is operator-internal by design."
    )


def _render_run_picker(runs_df: pd.DataFrame) -> str | None:
    """Sidebar run picker. Returns the selected run_id or 'ALL'."""
    st.sidebar.header("Filters")
    if runs_df.empty:
        return None
    label_map: dict[str, str] = {"ALL": "All runs"}
    for _, row in runs_df.iterrows():
        label = f"{row['started_at'][:19]} — {row['operator']}"
        label_map[row["run_id"]] = label
    choice = st.sidebar.selectbox(
        "Run",
        options=list(label_map.keys()),
        format_func=lambda rid: label_map[rid],
    )
    return choice


def _filter_by_run(df: pd.DataFrame, run_col: str, run_id: str) -> pd.DataFrame:
    if run_id == "ALL":
        return df
    return df[df[run_col] == run_id]


def _render_summary_cards(
    attempts: pd.DataFrame,
    verdicts: pd.DataFrame,
    costs: pd.DataFrame,
) -> None:
    cols = st.columns(4)
    cols[0].metric("Attempts", len(attempts))
    if not verdicts.empty:
        fail_rate = (
            (verdicts["verifier_replay"] == "fail").sum() / len(verdicts) * 100
        )
        cols[1].metric("Verifier fail rate", f"{fail_rate:.0f}%")
    else:
        cols[1].metric("Verifier fail rate", "—")
    distinct = (
        attempts["subcategory_id"].nunique() if not attempts.empty else 0
    )
    cols[2].metric("Subcategories touched", distinct)
    cols[3].metric("Total cost USD", f"${costs['usd'].sum():.4f}" if not costs.empty else "$0.00")


def _render_coverage(attempts: pd.DataFrame) -> None:
    st.subheader("Per-category coverage")
    if _empty_state_check(attempts, "attempts"):
        return
    by_sub = (
        attempts.groupby("subcategory_id")
        .size()
        .reset_index(name="attempts")
        .sort_values("attempts", ascending=False)
    )
    st.bar_chart(by_sub.set_index("subcategory_id"))


def _render_verdict_breakdown(verdicts: pd.DataFrame) -> None:
    st.subheader("Verdict mix")
    if _empty_state_check(verdicts, "judge verdicts"):
        return
    col1, col2 = st.columns(2)
    with col1:
        st.caption("Verifier replay")
        replay_counts = verdicts["verifier_replay"].value_counts().reset_index()
        replay_counts.columns = ["verdict", "count"]
        st.bar_chart(replay_counts.set_index("verdict"))
    with col2:
        st.caption("Semantic LLM")
        sem_counts = verdicts["semantic"].value_counts().reset_index()
        sem_counts.columns = ["verdict", "count"]
        st.bar_chart(sem_counts.set_index("verdict"))


def _render_severity(verdicts: pd.DataFrame) -> None:
    st.subheader("Severity distribution")
    if _empty_state_check(verdicts, "judge verdicts"):
        return
    sev_order = ["info", "low", "medium", "high", "critical"]
    sev_counts = (
        verdicts["severity"]
        .value_counts()
        .reindex(sev_order, fill_value=0)
        .reset_index()
    )
    sev_counts.columns = ["severity", "count"]
    st.bar_chart(sev_counts.set_index("severity"))


def _render_open_vulns(verdicts: pd.DataFrame, attempts: pd.DataFrame) -> None:
    st.subheader("Open vulnerabilities (action = regression)")
    if verdicts.empty:
        st.info("No verdicts yet.")
        return
    flagged = verdicts[verdicts["recommended_action"] == "regression"]
    if flagged.empty:
        st.success("No open regressions.")
        return
    joined = flagged.merge(
        attempts[["attempt_id", "subcategory_id", "specialist"]],
        on="attempt_id",
        how="left",
        suffixes=("_v", ""),
    )
    # Build a "?attempt_id=..." URL so each row is clickable into the drill-down.
    joined = joined.copy()
    joined["detail"] = joined["attempt_id"].apply(lambda aid: f"?attempt_id={aid}")
    cols = [
        "detail",
        "attempt_id",
        "subcategory_id",
        "specialist",
        "verifier_replay",
        "semantic",
        "severity",
        "rationale",
    ]
    st.dataframe(
        joined[cols],
        hide_index=True,
        use_container_width=True,
        column_config={
            "detail": st.column_config.LinkColumn(
                "Open",
                display_text="🔍 detail",
                help="Open the per-attempt drill-down page.",
            ),
        },
    )


def _render_costs(costs: pd.DataFrame) -> None:
    st.subheader("Cost by agent")
    if _empty_state_check(costs, "cost rows"):
        return
    by_agent = (
        costs.groupby("agent")
        .agg(
            calls=("usd", "size"),
            prompt_tokens=("prompt_tokens", "sum"),
            completion_tokens=("completion_tokens", "sum"),
            usd=("usd", "sum"),
        )
        .reset_index()
        .sort_values("usd", ascending=False)
    )
    st.dataframe(by_agent, hide_index=True, use_container_width=True)


def _render_agent_timeline(events: pd.DataFrame, run_id: str) -> None:
    st.subheader("Agent activity timeline")
    if _empty_state_check(events, "agent events"):
        return
    filtered = _filter_by_run(events, "run_id", run_id)
    if filtered.empty:
        st.info("No events for this filter.")
        return
    pivot = (
        filtered.groupby(["agent", "event_type"])
        .size()
        .reset_index(name="n")
        .pivot(index="event_type", columns="agent", values="n")
        .fillna(0)
    )
    st.bar_chart(pivot)


# ---------------------------------------------------------------------------
# P2.5-T1 — Per-attempt drill-down page
# ---------------------------------------------------------------------------

def _safe_json_pretty(text: str | None) -> str:
    """Pretty-print a JSON string; fall back to raw on parse failure."""
    if text is None or text == "":
        return ""
    try:
        return json.dumps(json.loads(text), indent=2, sort_keys=False)
    except (ValueError, TypeError):
        return text


def _render_attempt_detail(db_path: str, attempt_id: str) -> None:
    """Render the full record of one attempt on a single screen.

    Pulls attempt + target_response + judge_verdict + cost rows +
    per-attempt agent_events. Renders each section with a back link
    to clear the query param and return to the main dashboard.
    """
    st.markdown("[← back to dashboard](?)")
    st.subheader(f"Attempt detail — `{attempt_id}`")

    # Attempt row (with run + campaign context).
    attempt_df = _load_table(
        db_path,
        f"""SELECT a.*, c.run_id, c.subcategory_id AS campaign_sub,
                   c.rationale AS campaign_rationale, c.seed_case_id
            FROM attempts a
            JOIN campaigns c ON c.campaign_id = a.campaign_id
            WHERE a.attempt_id = '{attempt_id.replace("'", "''")}'""",
    )
    if attempt_df.empty:
        st.error(f"No attempt found with id `{attempt_id}`.")
        return
    row = attempt_df.iloc[0]

    # Header strip with the key identifiers.
    cols = st.columns(4)
    cols[0].metric("Subcategory", row["subcategory_id"])
    cols[1].metric("Specialist", row["specialist"])
    cols[2].metric("Created", str(row["created_at"])[:19])
    cols[3].metric("Run id", str(row["run_id"])[:8] + "…")
    st.caption(f"Orchestrator rationale: {row['campaign_rationale']}")

    # ───── Attack input ─────
    st.markdown("### Attack input (what got sent)")
    if row.get("prompt"):
        st.markdown("**Prompt (USER_QUESTION):**")
        st.code(row["prompt"], language="text")
    if row.get("chart_text_payload"):
        st.markdown("**Chart-text payload (indirect injection):**")
        st.code(row["chart_text_payload"], language="text")
    if row.get("multi_turn_sequence"):
        st.markdown("**Multi-turn sequence:**")
        try:
            turns = json.loads(row["multi_turn_sequence"])
        except (ValueError, TypeError):
            turns = []
        if isinstance(turns, list):
            for i, t in enumerate(turns, 1):
                action = "briefing" if i == 1 else "followup"
                st.markdown(f"**Turn {i}** (`action={action}`):")
                st.code(str(t), language="text")
    if row.get("http_request"):
        st.markdown("**HTTP request envelope:**")
        st.code(_safe_json_pretty(row["http_request"]), language="json")

    # ───── Target response ─────
    response_df = _load_table(
        db_path,
        f"""SELECT * FROM target_responses
            WHERE attempt_id = '{attempt_id.replace("'", "''")}'""",
    )
    st.markdown("### Target response")
    if response_df.empty:
        st.info("No target response recorded for this attempt.")
    else:
        resp = response_df.iloc[0]
        m = st.columns(4)
        m[0].metric("HTTP status", int(resp["http_status"]))
        m[1].metric("Latency (ms)", int(resp["latency_ms"]))
        m[2].metric("Prompt tokens", resp.get("prompt_tokens") or 0)
        m[3].metric("Completion tokens", resp.get("completion_tokens") or 0)
        st.caption(f"Target version: `{resp['target_version']}`")

        st.markdown("**Raw model output (pre-PHP-verifier):**")
        st.code(_safe_json_pretty(resp.get("raw_model_output")), language="json")
        if resp.get("post_verifier_output") and resp.get("post_verifier_output") != resp.get("raw_model_output"):
            st.markdown("**Post-verifier output (what the user saw):**")
            st.code(_safe_json_pretty(resp.get("post_verifier_output")), language="json")

        # PHP verifier verdicts (target_client packs them into audit_log_id JSON).
        if resp.get("audit_log_id"):
            try:
                audit = json.loads(resp["audit_log_id"])
            except (ValueError, TypeError):
                audit = None
            if isinstance(audit, dict):
                st.markdown("**PHP verifier verdicts:**")
                php_cols = st.columns(3)
                php_cols[0].metric("Source verifier", audit.get("source_verification") or "—")
                php_cols[1].metric("Domain verifier", audit.get("domain_verification") or "—")
                php_cols[2].metric("LLM status", audit.get("llm_status") or "—")
                if audit.get("failure_reason"):
                    st.warning(f"failure_reason: {audit['failure_reason']}")
                allowed = audit.get("allowed_source_ids") or []
                if allowed:
                    st.caption(f"allowed_source_ids: {', '.join(allowed)}")

        # Response cookies + Set-Cookie headers (Cracker 6d/6e signals).
        if resp.get("response_cookies") or resp.get("set_cookie_headers"):
            st.markdown("**Response cookies / Set-Cookie headers:**")
            if resp.get("response_cookies"):
                st.code(_safe_json_pretty(resp["response_cookies"]), language="json")
            if resp.get("set_cookie_headers"):
                st.code(_safe_json_pretty(resp["set_cookie_headers"]), language="json")

    # ───── Judge verdict ─────
    verdict_df = _load_table(
        db_path,
        f"""SELECT * FROM judge_verdicts
            WHERE attempt_id = '{attempt_id.replace("'", "''")}'""",
    )
    st.markdown("### Judge verdict")
    if verdict_df.empty:
        st.info("No Judge verdict recorded yet.")
    else:
        v = verdict_df.iloc[0]
        c = st.columns(4)
        c[0].metric("Verifier replay", v["verifier_replay"])
        c[1].metric("Semantic", v["semantic"])
        c[2].metric("Severity", v["severity"])
        c[3].metric("Action", v["recommended_action"])
        st.caption(f"Judge model: `{v['judge_model']}`")
        st.markdown("**Rationale:**")
        st.write(v["rationale"])

    # ───── Cost rows ─────
    cost_df = _load_table(
        db_path,
        f"""SELECT agent, provider, model, prompt_tokens, completion_tokens, usd, created_at
            FROM costs
            WHERE attempt_id = '{attempt_id.replace("'", "''")}'
            ORDER BY created_at""",
    )
    st.markdown("### Cost rows for this attempt")
    if cost_df.empty:
        st.caption("(no cost rows — deterministic specialist or trace-only attempt)")
    else:
        st.dataframe(cost_df, hide_index=True, use_container_width=True)

    # ───── agent_events timeline for this attempt ─────
    events_df = _load_table(
        db_path,
        f"""SELECT created_at, agent, event_type, payload
            FROM agent_events
            WHERE attempt_id = '{attempt_id.replace("'", "''")}'
            ORDER BY created_at""",
    )
    st.markdown("### Event timeline (this attempt only)")
    if events_df.empty:
        st.caption("(no events recorded for this attempt)")
    else:
        for _, ev in events_df.iterrows():
            label = f"`{ev['created_at'][11:19]}` · **{ev['agent']}** · {ev['event_type']}"
            payload = ev.get("payload")
            if payload:
                with st.expander(label):
                    st.code(_safe_json_pretty(payload), language="json")
            else:
                st.markdown(label)


# ---------------------------------------------------------------------------
# P2.5-T3 — Live auto-refresh tab
# ---------------------------------------------------------------------------

def _render_live_activity(db_path: str, refresh_seconds: int = 2) -> None:
    """Most-recent agent_events feed with HTML meta-refresh.

    No Streamlit cache here — every reload re-queries the latest rows so
    the panel reflects an in-progress run. The meta-refresh tag drives
    the periodic reload without needing the streamlit-autorefresh extra.
    """
    # Inject meta-refresh. Scoped to the page; takes effect on the next
    # full reload (Streamlit re-renders the markdown on each rerun).
    st.markdown(
        f"<meta http-equiv='refresh' content='{refresh_seconds}'>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"Auto-refreshing every {refresh_seconds}s · pulls the latest 50 events. "
        "Close this tab or navigate away to stop polling."
    )

    conn = sqlite3.connect(db_path)
    try:
        events = pd.read_sql_query(
            "SELECT created_at, agent, event_type, attempt_id, payload "
            "FROM agent_events "
            "ORDER BY event_id DESC LIMIT 50",
            conn,
        )
    finally:
        conn.close()

    if events.empty:
        st.info("No agent events yet. Start a run with `chartbreaker run-mvp-loop`.")
        return

    # Reverse so the oldest of this window is at the top, newest at the bottom
    # (matches a console log tail).
    events = events.iloc[::-1].reset_index(drop=True)

    # Compact event-feed rendering. Last event highlighted.
    for idx, ev in events.iterrows():
        ts = str(ev["created_at"])[11:23]
        agent = ev["agent"]
        event_type = ev["event_type"]
        attempt = ev.get("attempt_id")
        attempt_link = (
            f" · [attempt {str(attempt)[:8]}…](?attempt_id={attempt})"
            if attempt
            else ""
        )
        header = f"`{ts}` · **{agent}** · {event_type}{attempt_link}"
        if ev.get("payload"):
            with st.expander(header, expanded=(idx == len(events) - 1)):
                st.code(_safe_json_pretty(ev["payload"]), language="json")
        else:
            st.markdown(header)


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main() -> None:
    db_path = RUNS_SQLITE
    _render_header(db_path)

    if not Path(db_path).exists():
        st.warning(
            f"`{db_path}` does not exist yet. Run `chartbreaker run-mvp-loop` "
            "to populate the observability store."
        )
        st.stop()

    # P2.5-T1: drill-down mode is triggered by an ?attempt_id=... query param.
    # When present, render the attempt-detail page instead of the aggregates.
    attempt_id_param = st.query_params.get("attempt_id")
    if isinstance(attempt_id_param, list):
        # Streamlit returns a list when the same param appears multiple times.
        attempt_id_param = attempt_id_param[0] if attempt_id_param else None
    if attempt_id_param:
        _render_attempt_detail(db_path, attempt_id_param)
        return

    runs = _load_table(db_path, "SELECT * FROM runs ORDER BY started_at DESC")
    attempts_all = _load_table(
        db_path,
        "SELECT a.*, c.run_id FROM attempts a "
        "JOIN campaigns c ON c.campaign_id = a.campaign_id",
    )
    verdicts_all = _load_table(
        db_path,
        "SELECT v.*, c.run_id, a.subcategory_id "
        "FROM judge_verdicts v "
        "JOIN attempts a ON a.attempt_id = v.attempt_id "
        "JOIN campaigns c ON c.campaign_id = a.campaign_id",
    )
    costs_all = _load_table(
        db_path,
        "SELECT co.*, c.run_id FROM costs co "
        "JOIN campaigns c ON c.campaign_id = co.campaign_id",
    )
    events_all = _load_table(db_path, "SELECT * FROM agent_events ORDER BY created_at")

    run_id = _render_run_picker(runs)
    if run_id is None:
        st.warning("No runs recorded yet.")
        st.stop()

    attempts = _filter_by_run(attempts_all, "run_id", run_id)
    verdicts = _filter_by_run(verdicts_all, "run_id", run_id)
    costs = _filter_by_run(costs_all, "run_id", run_id)

    tab_dashboard, tab_live = st.tabs(["📊 Dashboard", "📡 Live activity"])

    with tab_dashboard:
        _render_summary_cards(attempts, verdicts, costs)
        _render_coverage(attempts)
        _render_verdict_breakdown(verdicts)
        _render_severity(verdicts)
        _render_open_vulns(verdicts, attempts)
        _render_costs(costs)
        _render_agent_timeline(events_all, run_id)

        with st.expander("Raw runs table"):
            st.dataframe(runs, hide_index=True, use_container_width=True)

    with tab_live:
        _render_live_activity(db_path)


if __name__ == "__main__":
    main()
