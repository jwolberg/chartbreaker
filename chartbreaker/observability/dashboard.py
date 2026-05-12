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
import sys
from pathlib import Path

# Streamlit launches this script with `chartbreaker/observability/` on
# sys.path (the script's own directory), NOT the repo root. That makes
# `from chartbreaker.config import ...` fail with ModuleNotFoundError
# regardless of CWD. Inject the repo root so the import resolves
# whether you run `streamlit run chartbreaker/observability/dashboard.py`
# from the repo root or from anywhere else.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import pandas as pd
import streamlit as st

from chartbreaker.config import RUNS_SQLITE


# ---------------------------------------------------------------------------
# Glossaries — keep the dashboard readable for a fresh viewer.
# ---------------------------------------------------------------------------

# Subcategory ID → short human label. Drawn from docs/THREAT_MODEL.md
# (THREAT_MODEL.md is the source of truth; bump these when it changes).
SUBCATEGORY_LABEL: dict[str, str] = {
    # Category 1 — Prompt Injection
    "1a": "Direct prompt injection (USER_QUESTION override)",
    "1b": "Indirect injection via chart text",
    "1c": "System-prompt leakage",
    "1d": "Multi-turn role manipulation",
    "1e": "Structured-output coercion",
    "1f": "System-prompt extraction",
    # Category 2 — Data Exfiltration
    "2a": "Output-shape data exfiltration",
    "2b": "Source-ID forgery (marquee finding)",
    "2c": "Cross-patient data leakage",
    "2d": "Cross-turn cached-data leakage",
    "2e": "Audit-log side-channel (out of scope V1)",
    "2f": "Cross-tenant pid swap (authz bypass)",
    # Category 3 — State Corruption
    "3a": "Conversation-history poisoning",
    "3b": "Fabricated chart text as ground truth",
    "3c": "False clinical facts asserted as truth",
    "3d": "Uncalibrated absolute certainty",
    "3e": "Chart-text premise injection",
    # Category 4 — Tool Misuse
    "4a": "Vision-pipeline upload abuse",
    "4b": "Supervisor-graph routing-keyword bait",
    "4c": "Parameter tampering / oversized envelope",
    "4d": "Recursive tool-call placeholder",
    "4e": "RAG corpus ingestion (out of scope V1)",
    # Category 5 — DoS / Cost
    "5a": "Token exhaustion",
    "5b": "Rate-limit bypass via session rotation",
    "5c": "Self-referential / repetition loops",
    "5d": "Vision over-extraction",
    "5e": "Long-prompt amplification",
    # Category 6 — Identity / Role
    "6a": "CSRF token replay / suppression",
    "6b": "Persona hijacking",
    "6c": "BAA-gate / privilege escalation",
    "6d": "Session fixation + cookie audit",
    "6e": "Login brute-force / lockout-bypass",
}


def _sub_label(sub: str) -> str:
    """Format a subcategory id with its short label, e.g. '1b — Indirect injection…'."""
    name = SUBCATEGORY_LABEL.get(sub)
    return f"{sub} — {name}" if name else sub


# Agent role → one-line description for tooltips.
AGENT_DESCRIPTION: dict[str, str] = {
    "orchestrator": "Picks which attack subcategory to run next (priority math + budget).",
    "red_team_lead": "Routes each campaign to the right specialist (deterministic table).",
    "injector": "LLM specialist for prompt injection (Cat 1a, 1b).",
    "conversationalist": "LLM specialist for multi-turn manipulation (Cat 1d, 3a).",
    "smuggler": "LLM specialist for verifier-bypass exfiltration (Cat 2a, 2b, 2d).",
    "impersonator": "LLM specialist for persona hijacking (Cat 6b).",
    "saboteur": "Deterministic specialist for tool misuse / param tampering (Cat 4).",
    "cracker": "Deterministic specialist for HTTP/auth probes (Cat 2f, 6a-6e).",
    "glutton": "Deterministic specialist for DoS / cost amplification (Cat 5).",
    "judge": "Two-part verdict: deterministic verifier replay + semantic LLM check.",
    "judge_semantic": "OpenAI gpt-5.4-nano running the semantic half of the Judge.",
    "scribe": "LLM that drafts vulnerability reports from flagged attempts.",
    "target_client": "HTTP client that authenticates and dispatches attempts to the live Co-Pilot.",
    "system": "Run lifecycle events (run_started / run_ended).",
}


# Event types in agent_events → plain-English explanation.
EVENT_TYPE_DESCRIPTION: dict[str, str] = {
    "run_started": "ChartBreaker session began.",
    "run_ended": "ChartBreaker session finished cleanly.",
    "campaign_emitted": "Orchestrator decided to attack this subcategory next.",
    "attempt_generated": "A specialist produced one adversarial input (prompt or HTTP envelope).",
    "response_received": "Target Co-Pilot returned a response for this attempt.",
    "verdict_recorded": "Judge issued its two-part verdict on the response.",
    "finding_drafted": "Scribe wrote a vulnerability-report draft.",
    "cost_recorded": "An LLM call's token usage + USD was logged (trace mirror only).",
}


# Severity → tooltip explanation.
SEVERITY_DESCRIPTION: dict[str, str] = {
    "critical": "Direct unauthorized PHI access or authz bypass. Immediate fix required.",
    "high": "Material rule violation (DOB leak, persona break, session weakness).",
    "medium": "Defended in this deployment but reachable; or a cost/integrity gap.",
    "low": "Probe captured; no exploit reached. Useful as a baseline.",
    "info": "Background event; no security signal.",
}


# Verdict-field explainers (used in metric help= and rendered subheaders).
HELP_VERIFIER_REPLAY = (
    "Pass / fail from re-running the Co-Pilot's PHP verifier logic in Python "
    "against the raw response. `fail` means the response cited unsourced IDs, "
    "tried clinical recommendations, or matched another rule the deterministic "
    "verifier catches. Independent of any LLM judgment."
)
HELP_SEMANTIC = (
    "OpenAI gpt-5.4-nano's read of whether the response violated the chatbot's "
    "safety rules. `not_run` = semantic Judge disabled or skipped. `pass` / "
    "`partial` / `fail` = the LLM's verdict. Independent of the verifier replay; "
    "disagreement between the two is itself a finding."
)
HELP_SEVERITY = (
    "Threat-model severity rubric. Critical = authz bypass / cross-tenant PHI. "
    "High = DOB / system-prompt leak / persona break. Medium = defended-but-reachable. "
    "Low = probe captured with no exploit."
)
HELP_RECOMMENDED_ACTION = (
    "What the Judge wants done with this attempt. `regression` = pin to "
    "regression suite and re-test on every deploy. `mutate` = vary and retry. "
    "`escalate` = surface to operator immediately. `discard` = no follow-up."
)


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
    # Bump section-title and expander-summary font size by 50% so the
    # dashboard's hierarchy is easier to scan. Streamlit's default
    # subheader (h3) ≈ 1.25rem; expander summary ≈ 1rem. Targets are
    # scoped via data-testid attributes so we don't impact the
    # legend tables or other body content.
    st.markdown(
        """
<style>
section.main h2 { font-size: 2.25rem !important; }
section.main h3 { font-size: 1.875rem !important; }
[data-testid="stExpander"] details summary p { font-size: 1.5rem !important; font-weight: 600; }
</style>
""",
        unsafe_allow_html=True,
    )
    st.title("ChartBreaker — Observability Dashboard")
    st.caption(
        f"Local read-only view of `{db_path}`. "
        "ChartBreaker dashboard is operator-internal by design."
    )
    with st.expander("ℹ️ How to read this dashboard", expanded=False):
        st.markdown(
            """
**ChartBreaker** is a multi-agent red team that probes the deployed OpenEMR
Clinical Co-Pilot for AI-specific vulnerabilities (prompt injection,
PHI exfiltration, session weaknesses, etc.). Every attempt the platform
makes is captured in the observability store this dashboard reads from.

**Key concepts**

- **Run** — one CLI invocation of `chartbreaker run-mvp-loop` (or a CI sweep).
  Use the sidebar to pick a single run or "All runs".
- **Campaign** — the Orchestrator's decision to attack one *subcategory*.
- **Subcategory** — a specific attack vector from the threat model
  (e.g. `1b` = indirect prompt injection via chart text). Hover the
  legend below for the full mapping.
- **Attempt** — one adversarial input dispatched to the target.
- **Verdict** — the Judge's ruling. Has two parts:
  - **Verifier replay** (deterministic Python port of the Co-Pilot's PHP
    verifier) — `pass` or `fail`.
  - **Semantic** (LLM judgment, isolated from the attacker's reasoning) —
    `pass` / `partial` / `fail` / `not_run`.
  Disagreement between the two is itself a finding.

**Tabs**

- **📊 Dashboard** — aggregated view of one run (or all runs).
- **📡 Live activity** — auto-refreshing feed of the most recent agent
  events. Useful while a run is in progress.

**Tips**

- Click any **🔍 detail** link in the "Open vulnerabilities" table to
  jump into the per-attempt drill-down (full prompt + response + Judge
  rationale + costs on one screen).
- Use the sidebar **"Search rationales"** box to find every verdict
  whose explanation mentions a specific term (e.g. `BREACH-OK`,
  `medication:42`, `DISAGREEMENT`).
- See `docs/OBSERVABILITY.md` for the full four-layer guide (stdout,
  Python logs, SQLite + JSONL store, this dashboard).
"""
        )
    with st.expander("📖 Subcategory legend (what does '1b' mean?)", expanded=False):
        # Render the subcategory glossary as a dataframe so it's searchable.
        legend = pd.DataFrame(
            [{"subcategory": k, "description": v} for k, v in SUBCATEGORY_LABEL.items()]
        )
        st.dataframe(
            legend,
            hide_index=True,
            use_container_width=True,
            column_config={
                "subcategory": st.column_config.TextColumn(
                    "ID",
                    help="Subcategory id used in chart axes and tables.",
                    width="small",
                ),
                "description": st.column_config.TextColumn(
                    "Attack vector",
                    help="What the specialist is probing for in this subcategory.",
                ),
            },
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
        help=(
            "Each run is one `chartbreaker run-mvp-loop` invocation (or a CI "
            "sweep). Pick a single run to focus the dashboard on it, or "
            "`All runs` to see aggregate history."
        ),
    )
    return choice


def _render_rationale_search() -> str:
    """Sidebar substring filter for judge_verdicts.rationale (P2.5-T5)."""
    return st.sidebar.text_input(
        "Search rationales",
        value="",
        help=(
            "Filter the verdict-mix, severity, and open-vulns panels by "
            "substring match against the Judge's rationale text. "
            "Case-insensitive. Try terms like:\n"
            "• `DISAGREEMENT` — find verdicts where the two Judge halves disagreed\n"
            "• `medication:42` — find responses citing a specific (potentially forged) source ID\n"
            "• `BREACH-OK` — find responses that complied with the demo-injection marker\n"
            "• `persona` — find responses where the model shifted role"
        ),
        key="rationale_search",
        placeholder="e.g. DISAGREEMENT",
    ).strip()


def _apply_rationale_filter(verdicts: pd.DataFrame, needle: str) -> pd.DataFrame:
    """Return verdicts whose rationale contains `needle` (case-insensitive).

    Empty or whitespace-only needle → no filter applied.
    """
    stripped = (needle or "").strip()
    if not stripped or verdicts.empty:
        return verdicts
    mask = verdicts["rationale"].fillna("").str.contains(stripped, case=False, regex=False)
    return verdicts[mask]


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
    cols[0].metric(
        "Attempts",
        len(attempts),
        help=(
            "Total adversarial inputs ChartBreaker dispatched to the live "
            "target in this run. One per row in the `attempts` table."
        ),
    )
    if not verdicts.empty:
        fail_rate = (
            (verdicts["verifier_replay"] == "fail").sum() / len(verdicts) * 100
        )
        cols[1].metric(
            "Verifier fail rate",
            f"{fail_rate:.0f}%",
            help=(
                "Percentage of attempts where the deterministic Python "
                "verifier (re-running the Co-Pilot's PHP verifier logic) "
                "said the response should have been rejected. High = the "
                "target is accepting responses our reference port would block."
            ),
        )
    else:
        cols[1].metric("Verifier fail rate", "—", help="No verdicts recorded yet.")
    distinct = (
        attempts["subcategory_id"].nunique() if not attempts.empty else 0
    )
    cols[2].metric(
        "Subcategories touched",
        distinct,
        help=(
            "Number of distinct attack vectors exercised (e.g. 1a direct "
            "injection, 2f cross-tenant pid swap, 6d session fixation). "
            "Broader coverage = stronger evidence."
        ),
    )
    cols[3].metric(
        "Total cost USD",
        f"${costs['usd'].sum():.4f}" if not costs.empty else "$0.00",
        help=(
            "Combined LLM spend across all specialists, the Judge, and the "
            "Orchestrator for this run. Deterministic specialists (Saboteur, "
            "Cracker) contribute $0. Does NOT include the target's own LLM "
            "cost — that bills to the OpenEMR deployment."
        ),
    )


def _render_coverage(attempts: pd.DataFrame) -> None:
    st.subheader(
        "Per-subcategory coverage",
        help=(
            "How many attempts the platform made per attack subcategory in "
            "this run. Each bar is one row in the threat model. Higher bars "
            "= the platform spent more dispatches on that attack vector."
        ),
    )
    st.caption(
        "Bars are labeled with subcategory IDs (1a, 1b, 2f, …). Open the "
        "**Subcategory legend** expander above for the full description "
        "of each ID."
    )
    if _empty_state_check(attempts, "attempts"):
        return
    by_sub = (
        attempts.groupby("subcategory_id")
        .size()
        .reset_index(name="attempts")
        .sort_values("attempts", ascending=False)
    )
    st.bar_chart(by_sub.set_index("subcategory_id"))

    # Also show a small table with full descriptions for each subcategory
    # actually touched in this run, since the bar chart can only fit short labels.
    touched = pd.DataFrame(
        [
            {
                "subcategory": row["subcategory_id"],
                "attack vector": SUBCATEGORY_LABEL.get(row["subcategory_id"], "(unknown)"),
                "attempts": int(row["attempts"]),
            }
            for _, row in by_sub.iterrows()
        ]
    )
    with st.expander("Show subcategory descriptions for this run", expanded=False):
        st.dataframe(touched, hide_index=True, use_container_width=True)


def _render_verdict_breakdown(verdicts: pd.DataFrame) -> None:
    st.subheader(
        "Judge verdict mix",
        help=(
            "Every attempt gets a two-part verdict from the Judge. The left "
            "chart shows the deterministic Python verifier port's calls; the "
            "right chart shows the semantic LLM Judge's calls. Disagreement "
            "between the two halves on the same attempt is itself a finding."
        ),
    )
    if _empty_state_check(verdicts, "judge verdicts"):
        return
    col1, col2 = st.columns(2)
    with col1:
        st.caption("**Verifier replay** (deterministic)")
        st.caption(HELP_VERIFIER_REPLAY)
        replay_counts = verdicts["verifier_replay"].value_counts().reset_index()
        replay_counts.columns = ["verdict", "count"]
        st.bar_chart(replay_counts.set_index("verdict"))
    with col2:
        st.caption("**Semantic LLM** (gpt-5.4-nano)")
        st.caption(HELP_SEMANTIC)
        sem_counts = verdicts["semantic"].value_counts().reset_index()
        sem_counts.columns = ["verdict", "count"]
        st.bar_chart(sem_counts.set_index("verdict"))


def _render_severity(verdicts: pd.DataFrame) -> None:
    st.subheader(
        "Severity distribution",
        help=HELP_SEVERITY,
    )
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
    st.caption(
        "Severity comes from the static rubric in `chartbreaker/agents/judge_agent.py` "
        "(per-subcategory mapping from `docs/THREAT_MODEL.md`). It reflects the "
        "*potential* impact of the attack vector, not whether this specific "
        "attempt succeeded."
    )


def _render_open_vulns(verdicts: pd.DataFrame, attempts: pd.DataFrame) -> None:
    st.subheader(
        "Open vulnerabilities",
        help=(
            "Every attempt whose Judge verdict has `recommended_action = "
            "regression` — meaning the Judge wants it pinned to the "
            "regression suite and re-tested on every deploy. These are "
            "the findings worth investigating first."
        ),
    )
    if verdicts.empty:
        st.info("No verdicts yet. Start a run with `chartbreaker run-mvp-loop`.")
        return
    flagged = verdicts[verdicts["recommended_action"] == "regression"]
    if flagged.empty:
        st.success("✅ No open regressions — every attempt was discarded by the Judge.")
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
    # Expand subcategory id with its short label so the row is readable
    # without consulting the legend.
    joined["attack vector"] = joined["subcategory_id"].apply(
        lambda s: SUBCATEGORY_LABEL.get(s, "(unknown)")
    )
    cols = [
        "detail",
        "subcategory_id",
        "attack vector",
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
                help="Click to open the per-attempt drill-down (full prompt + response + Judge rationale + costs).",
                width="small",
            ),
            "subcategory_id": st.column_config.TextColumn(
                "Subcat",
                help="Threat-model subcategory ID. See the Subcategory legend expander above for full names.",
                width="small",
            ),
            "attack vector": st.column_config.TextColumn(
                "Attack vector",
                help="One-line description of what this subcategory probes.",
            ),
            "specialist": st.column_config.TextColumn(
                "Specialist",
                help="Which agent generated this attempt. LLM specialists: injector, conversationalist, smuggler. Deterministic: saboteur, cracker, glutton.",
                width="small",
            ),
            "verifier_replay": st.column_config.TextColumn(
                "Verifier",
                help=HELP_VERIFIER_REPLAY,
                width="small",
            ),
            "semantic": st.column_config.TextColumn(
                "Semantic",
                help=HELP_SEMANTIC,
                width="small",
            ),
            "severity": st.column_config.TextColumn(
                "Severity",
                help=HELP_SEVERITY,
                width="small",
            ),
            "rationale": st.column_config.TextColumn(
                "Rationale",
                help="Concatenated explanation from both Judge halves. `DISAGREEMENT` tag = the two halves disagreed (a finding in itself).",
            ),
        },
    )


def _render_costs(costs: pd.DataFrame) -> None:
    st.subheader(
        "Cost by agent",
        help=(
            "LLM spend grouped by which agent made the call. Deterministic "
            "specialists (Saboteur, Cracker) appear with $0 cost. Pricing "
            "is configured in `chartbreaker/llm_client.py` and reflects "
            "per-million-token rates from each provider's billing page."
        ),
    )
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
    by_agent["role"] = by_agent["agent"].apply(
        lambda a: AGENT_DESCRIPTION.get(a, "—")
    )
    st.dataframe(
        by_agent[["agent", "role", "calls", "prompt_tokens", "completion_tokens", "usd"]],
        hide_index=True,
        use_container_width=True,
        column_config={
            "agent": st.column_config.TextColumn(
                "Agent",
                help="Role name used in the model registry (e.g. injector, judge_semantic).",
                width="small",
            ),
            "role": st.column_config.TextColumn(
                "What it does",
                help="One-line description of the agent's job.",
            ),
            "calls": st.column_config.NumberColumn(
                "Calls",
                help="Number of LLM dispatches this agent made.",
                width="small",
            ),
            "prompt_tokens": st.column_config.NumberColumn(
                "Prompt tokens",
                help="Tokens sent to the LLM in this agent's prompts.",
            ),
            "completion_tokens": st.column_config.NumberColumn(
                "Completion tokens",
                help="Tokens the LLM generated back.",
            ),
            "usd": st.column_config.NumberColumn(
                "Cost (USD)",
                help="Total billed cost for this agent's LLM calls. Sums prompt + completion at provider rates.",
                format="$%.4f",
            ),
        },
    )


def _render_agent_timeline(events: pd.DataFrame, run_id: str) -> None:
    """Inter-agent communication detail (P2.5-T4).

    Renders the run's `agent_events` rows in chronological order as a
    foldable feed. Each row's `payload` JSON is folded inside an
    expander so the operator can see *what* an agent communicated to
    the next (e.g. Orchestrator → RedTeamLead "dispatch subcategory
    1d") without opening a SQLite shell. A small histogram of
    event_type counts at the top keeps the at-a-glance shape from the
    old bar chart.
    """
    st.subheader(
        "Agent activity timeline",
        help=(
            "Every state transition during a run is captured here. "
            "Use this to answer 'what did the Orchestrator decide?', "
            "'when did the Judge fire?', 'in what order did the agents "
            "communicate?'. Expand any event to see its JSON payload."
        ),
    )
    if _empty_state_check(events, "agent events"):
        return
    filtered = _filter_by_run(events, "run_id", run_id)
    if filtered.empty:
        st.info("No events for this filter.")
        return

    # Top-line histogram — kept so the operator sees aggregate shape at a glance.
    with st.expander(
        f"Aggregate event counts ({len(filtered)} events) — open for the agent × event-type heatmap",
        expanded=False,
    ):
        counts = (
            filtered.groupby(["agent", "event_type"])
            .size()
            .reset_index(name="n")
            .pivot(index="event_type", columns="agent", values="n")
            .fillna(0)
        )
        st.bar_chart(counts)

    # Event-type legend so the feed is readable without context.
    with st.expander("📖 Event-type legend (what does `verdict_recorded` mean?)", expanded=False):
        legend_rows = [
            {"event_type": k, "what it means": v}
            for k, v in EVENT_TYPE_DESCRIPTION.items()
        ]
        st.dataframe(
            pd.DataFrame(legend_rows),
            hide_index=True,
            use_container_width=True,
        )

    # Chronological feed with payload expansion.
    st.caption(
        "Each row is one event. Click to expand and see the JSON payload "
        "(Orchestrator decisions, RedTeamLead dispatches, target response "
        "metadata, etc.). `🔍 attempt …` links jump to the per-attempt "
        "drill-down page."
    )
    # Filter sidebar: by agent.
    agents_present = sorted(filtered["agent"].dropna().unique().tolist())
    selected_agents = st.multiselect(
        "Filter by agent",
        options=agents_present,
        default=agents_present,
        key="timeline_agent_filter",
        help="Hide event rows from agents you don't care about right now.",
    )
    feed = filtered[filtered["agent"].isin(selected_agents)].sort_values("created_at")
    if feed.empty:
        st.info("No events match the filter.")
        return

    # Most-recent N events to avoid blowing up the page on huge runs.
    show_last = st.slider(
        "Show last N events", min_value=10, max_value=200, value=50, step=10,
        key="timeline_limit",
        help="Cap on how many events to render. Big runs can produce hundreds; rendering all of them slows the page.",
    )
    feed = feed.tail(show_last).reset_index(drop=True)

    for _, ev in feed.iterrows():
        ts = str(ev["created_at"])[11:23]
        agent = ev["agent"]
        event_type = ev["event_type"]
        agent_role = AGENT_DESCRIPTION.get(agent, "")
        event_meaning = EVENT_TYPE_DESCRIPTION.get(event_type, "")
        aid = ev.get("attempt_id")
        attempt_link = (
            f" · [🔍 attempt {str(aid)[:8]}…](?attempt_id={aid})" if aid else ""
        )
        # Header includes a plain-English suffix so the row is meaningful
        # without expanding.
        header_bits = [f"`{ts}`", f"**{agent}**", f"`{event_type}`"]
        header = " · ".join(header_bits) + attempt_link
        if event_meaning:
            header += f" — _{event_meaning}_"
        payload = ev.get("payload")
        if payload:
            with st.expander(header):
                if agent_role:
                    st.caption(f"**{agent}** — {agent_role}")
                st.code(_safe_json_pretty(payload), language="json")
        else:
            st.markdown(header)


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

# Default live-tab refresh cadence. 30 seconds is calm enough to read
# without scrolling-state thrash, while still being responsive enough
# during an active run. Operator can adjust via the in-tab slider.
LIVE_REFRESH_DEFAULT_SECONDS = 30
LIVE_REFRESH_MIN_SECONDS = 5
LIVE_REFRESH_MAX_SECONDS = 120


def _render_live_activity(db_path: str) -> None:
    """Most-recent agent_events feed with HTML meta-refresh.

    No Streamlit cache here — every reload re-queries the latest rows so
    the panel reflects an in-progress run. The meta-refresh tag drives
    the periodic reload without needing the streamlit-autorefresh extra.
    The refresh cadence is operator-tunable via the slider below.
    """
    st.markdown(
        "### 📡 Live activity feed\n"
        "**What you're looking at:** the most recent events from the agent "
        "timeline, refreshed on a fixed cadence. Use this while a run is "
        "in progress — the SQLite store is written incrementally, so each "
        "refresh picks up new events as they land."
    )

    # Slider lets the operator tune cadence in-page so the panel doesn't
    # flash too often. 30s default per operator feedback.
    refresh_seconds = st.slider(
        "Refresh every (seconds)",
        min_value=LIVE_REFRESH_MIN_SECONDS,
        max_value=LIVE_REFRESH_MAX_SECONDS,
        value=LIVE_REFRESH_DEFAULT_SECONDS,
        step=5,
        key="live_refresh_seconds",
        help=(
            "How often the page reloads. Higher = less visual flashing, "
            "but you'll see new events later. Default 30 s. Set to the "
            "maximum (120 s) for a near-static view while you read."
        ),
    )

    # Inject meta-refresh. Scoped to the page; takes effect on the next
    # full reload (Streamlit re-renders the markdown on each rerun).
    st.markdown(
        f"<meta http-equiv='refresh' content='{refresh_seconds}'>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"⏱ Auto-refreshing every {refresh_seconds}s · showing the 50 most "
        "recent events. Each row is one agent action; click the expander "
        "to see its JSON payload. `🔍 attempt …` links jump to the "
        "per-attempt drill-down."
    )
    with st.expander("📖 Event-type legend (what does each row mean?)", expanded=False):
        legend_rows = [
            {"event_type": k, "what it means": v}
            for k, v in EVENT_TYPE_DESCRIPTION.items()
        ]
        st.dataframe(
            pd.DataFrame(legend_rows),
            hide_index=True,
            use_container_width=True,
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
        st.info(
            "No agent events yet. Start a run with `chartbreaker run-mvp-loop` "
            "in another terminal — events will appear here on the next refresh."
        )
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
            f" · [🔍 attempt {str(attempt)[:8]}…](?attempt_id={attempt})"
            if attempt
            else ""
        )
        meaning = EVENT_TYPE_DESCRIPTION.get(event_type, "")
        agent_role = AGENT_DESCRIPTION.get(agent, "")
        header = f"`{ts}` · **{agent}** · `{event_type}`{attempt_link}"
        if meaning:
            header += f" — _{meaning}_"
        if ev.get("payload"):
            with st.expander(header, expanded=(idx == len(events) - 1)):
                if agent_role:
                    st.caption(f"**{agent}** — {agent_role}")
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

    rationale_needle = _render_rationale_search()

    attempts = _filter_by_run(attempts_all, "run_id", run_id)
    verdicts = _filter_by_run(verdicts_all, "run_id", run_id)
    costs = _filter_by_run(costs_all, "run_id", run_id)

    # Apply rationale search to the verdict-driven panels only. Coverage,
    # severity, costs, and timeline stay unfiltered because they answer
    # different questions than "what verdicts match this text?".
    verdicts_searched = _apply_rationale_filter(verdicts, rationale_needle)

    tab_dashboard, tab_live = st.tabs(["📊 Dashboard", "📡 Live activity"])

    with tab_dashboard:
        if rationale_needle:
            st.info(
                f"Rationale filter active: `{rationale_needle}` — "
                f"{len(verdicts_searched)}/{len(verdicts)} verdicts shown in the "
                "verdict-mix and open-vulns panels."
            )
        _render_summary_cards(attempts, verdicts, costs)

        # Top of the page: the answers the operator is here for —
        # "what's broken?" (open vulns) and "what did it cost?" (costs).
        _render_open_vulns(verdicts_searched, attempts)
        _render_costs(costs)

        # Followed by the aggregate "how the run shaped up" panels.
        _render_coverage(attempts)
        _render_verdict_breakdown(verdicts_searched)
        _render_severity(verdicts_searched)
        _render_agent_timeline(events_all, run_id)

        with st.expander("Raw runs table"):
            st.dataframe(runs, hide_index=True, use_container_width=True)

    with tab_live:
        _render_live_activity(db_path)


if __name__ == "__main__":
    main()
