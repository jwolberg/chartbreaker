"""Streamlit "Plan Next Run" tab — human-approval queue UI for the
Orchestrator Approval Harness (Phase 4).

Spec: ``docs/spec.md``. Build plan: ``docs/BUILD_PLAN.md`` § Phase 4.

Rendering lives in this dedicated module rather than inline in
``dashboard.py`` per the spec constraint — ``dashboard.py`` is already
~1800 lines, and the harness is a self-contained vertical slice.

All edits persist to SQLite immediately (not just ``st.session_state``)
so a Streamlit rerun, browser refresh, or full session restart shows the
same queue state. Checkbox selection is the one exception: it is
intentionally ephemeral — checkboxes drive WHICH proposals get
approved-then-launched on the next click, but unticking a checkbox is
not the same as rejecting a proposal.
"""

from __future__ import annotations

import getpass
import logging
import os
import subprocess
import sys
from pathlib import Path

import streamlit as st

from chartbreaker import auto_run as _auto_run
from chartbreaker.config import TRACES_JSONL
from chartbreaker.observability.store import ObservabilityStore
from chartbreaker.orchestrator import proposal_harness

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Store helper — open a short-lived ObservabilityStore per callback.
#
# We deliberately do NOT cache the store. Each interaction opens its own
# connection so the WAL journal stays small and writes from this tab
# don't race with reads from the rest of the dashboard.
# ----------------------------------------------------------------------


def _open_store(db_path: str) -> ObservabilityStore:
    """Return a context-manager store rooted at ``db_path``.

    Trace mirror path is derived from the db path's parent so that
    ``streamlit run`` against a non-default DB still writes its
    ``traces.jsonl`` next to the SQLite file rather than at the project
    default.
    """
    db = Path(db_path)
    trace_path = db.parent / Path(TRACES_JSONL).name
    return ObservabilityStore(db_path=db, trace_path=trace_path)


def _operator_label() -> str:
    """User-visible operator string written to decided_by / runs.operator."""
    try:
        return f"laptop:{getpass.getuser()}"
    except Exception:  # noqa: BLE001 — getuser can fail in odd env
        return "laptop:unknown"


# ----------------------------------------------------------------------
# Callback handlers — Streamlit fires these synchronously when the
# associated widget changes / is clicked. They write through to SQLite
# immediately so the next rerun reads the fresh state.
# ----------------------------------------------------------------------


def _on_budget_change(*, db_path: str, proposal_id: str) -> None:
    new_val = st.session_state[f"prop_budget_{proposal_id}"]
    try:
        with _open_store(db_path) as store:
            proposal_harness.set_mutation_budget(store, proposal_id, int(new_val))
    except Exception as exc:  # noqa: BLE001
        logger.warning("budget change failed for %s: %s", proposal_id, exc)
        st.session_state["_prop_error"] = (
            f"Could not update mutation budget for `{proposal_id[:8]}…`: {exc}"
        )


def _on_reject(*, db_path: str, proposal_id: str) -> None:
    try:
        with _open_store(db_path) as store:
            proposal_harness.reject(
                store,
                proposal_id,
                reason="rejected via dashboard",
                decided_by=_operator_label(),
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reject failed for %s: %s", proposal_id, exc)
        st.session_state["_prop_error"] = (
            f"Could not reject `{proposal_id[:8]}…`: {exc}"
        )


def _on_generate(*, db_path: str, n: int) -> None:
    try:
        with _open_store(db_path) as store:
            new_props = proposal_harness.propose(store, n=n)
        st.session_state["_prop_info"] = (
            f"Generated {len(new_props)} proposal(s). "
            "Already-pending triples were skipped (duplicate guard)."
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("propose() failed: %s", exc)
        st.session_state["_prop_error"] = f"propose() failed: {exc}"


def _on_launch(*, db_path: str, selected_ids: list[str]) -> None:
    """Approve every checked row, then execute the resulting batch."""
    operator = _operator_label()
    try:
        with _open_store(db_path) as store:
            for pid in selected_ids:
                proposal_harness.approve(store, pid, decided_by=operator)
            run_id = proposal_harness.execute_approved_batch_sync(
                store, operator=operator
            )
        st.session_state["_prop_info"] = (
            f"Launched batch of {len(selected_ids)} proposal(s) as run "
            f"`{run_id[:8]}…`. Each row will move to `executed` once the "
            "run loop finishes recording verdicts."
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("execute_approved_batch failed")
        st.session_state["_prop_error"] = (
            f"Launch failed: {exc} — proposals that were approved before the "
            "failure are still in `approved` state and will be picked up by "
            "the next launch."
        )


# ----------------------------------------------------------------------
# Subviews
# ----------------------------------------------------------------------


def _render_header(db_path: str) -> None:
    st.subheader("📋 Plan Next Run — Orchestrator Approval Queue")
    st.caption(
        "Human-in-the-loop gate on the Orchestrator's next-run slate. "
        "Generate proposals, tweak per-row mutation budget, check the rows "
        "you want to run, then click **Launch approved batch**. Persists "
        "across `streamlit run` restarts."
    )

    flash_info = st.session_state.pop("_prop_info", None)
    flash_err = st.session_state.pop("_prop_error", None)
    if flash_info:
        st.info(flash_info)
    if flash_err:
        st.error(flash_err)

    col1, col2 = st.columns([3, 1])
    with col1:
        st.number_input(
            "How many proposals to generate",
            min_value=1,
            max_value=16,
            value=int(st.session_state.get("prop_generate_n", 8)),
            step=1,
            key="prop_generate_n",
            help="Upper bound — duplicate triples already pending are skipped.",
        )
    with col2:
        st.write("")  # vertical alignment with number_input label
        st.button(
            "✨ Generate proposals",
            on_click=_on_generate,
            kwargs={
                "db_path": db_path,
                "n": int(st.session_state.get("prop_generate_n", 8)),
            },
            use_container_width=True,
        )


def _on_check_all(*, pending_ids: list[str]) -> None:
    """Mirror the master 'Check all' state into every per-row checkbox.

    Reads the new master state from ``st.session_state[_check_all_key]``
    and writes it to each row's ``prop_check_<id>`` key so Streamlit's
    next render reflects the bulk change. Unchecks all when the master
    is toggled off — matches the "select all / deselect all" idiom.
    """
    new_value = bool(st.session_state.get(_CHECK_ALL_KEY, False))
    for pid in pending_ids:
        st.session_state[f"prop_check_{pid}"] = new_value


_CHECK_ALL_KEY = "prop_check_all"


def _render_pending(db_path: str) -> list[str]:
    """Render the pending queue. Returns the list of checked proposal_ids."""
    with _open_store(db_path) as store:
        pending = proposal_harness.list_pending(store)

    if not pending:
        st.info(
            "No pending proposals. Click **✨ Generate proposals** above to "
            "ask the Orchestrator for a fresh slate."
        )
        return []

    pending_ids = [p.proposal_id for p in pending]

    # Master "Check all" — toggles every per-row checkbox in one click.
    # Visually anchored to the count line so the operator sees the bulk
    # action sitting next to the proposal list it controls.
    header_cols = st.columns([3, 1])
    with header_cols[0]:
        st.write(f"**{len(pending)} pending proposal(s)** — review and select:")
    with header_cols[1]:
        # No `value=` — session_state under `key=` already provides the
        # value, AND the on_change callback writes to per-row session
        # state. Passing `value=` with `key=` triggers Streamlit's
        # "set twice" warning the moment a callback mutates state.
        st.checkbox(
            "Check all",
            key=_CHECK_ALL_KEY,
            on_change=_on_check_all,
            kwargs={"pending_ids": pending_ids},
            help=(
                "Toggle every proposal in the queue. Untick to clear all. "
                "Individual rows can still be unchecked after."
            ),
        )

    selected_ids: list[str] = []
    for p in pending:
        with st.container(border=True):
            top_left, top_right = st.columns([4, 1])
            with top_left:
                st.markdown(
                    f"**Cat {p.subcategory_id}** · `{p.specialist}` "
                    f"· seed `{p.seed_case_id or '—'}` · "
                    f"priority **{p.priority_score:.2f}**"
                )
                st.write(p.rationale)
            with top_right:
                st.metric(
                    label="Est. cost (USD)",
                    value=f"${p.est_cost_usd:.4f}",
                    help=(
                        "Deterministic estimate: mutation_budget × per-attempt "
                        "(specialist + Judge) token rate from the model "
                        "registry. No live LLM probing."
                    ),
                )

            bot_left, bot_mid, bot_right = st.columns([1, 1, 1])
            with bot_left:
                st.number_input(
                    "Mutation budget",
                    min_value=1,
                    max_value=20,
                    value=int(p.mutation_budget),
                    step=1,
                    key=f"prop_budget_{p.proposal_id}",
                    on_change=_on_budget_change,
                    kwargs={"db_path": db_path, "proposal_id": p.proposal_id},
                    help="Edits persist immediately to SQLite.",
                )
            with bot_mid:
                # No `value=` here on purpose. When a widget has both
                # `key=` AND `value=` AND something else (the master
                # "Check all" callback) writes to that session_state
                # key, Streamlit raises "widget value set via Session
                # State API + default value." Session_state under the
                # key already drives initial render — let it.
                checked = st.checkbox(
                    "Include in next launch",
                    key=f"prop_check_{p.proposal_id}",
                    help=(
                        "Ephemeral selection — does NOT persist across restarts. "
                        "Checking + clicking Launch is what transitions the row "
                        "to approved → executed."
                    ),
                )
                if checked:
                    selected_ids.append(p.proposal_id)
            with bot_right:
                st.button(
                    "🚫 Reject",
                    key=f"prop_reject_{p.proposal_id}",
                    on_click=_on_reject,
                    kwargs={"db_path": db_path, "proposal_id": p.proposal_id},
                    help="Terminal. The proposal will move to the History panel.",
                )
    return selected_ids


def _render_launch_footer(db_path: str, selected_ids: list[str]) -> None:
    """Sticky-ish footer with total cost + launch button."""
    if not selected_ids:
        total_cost = 0.0
    else:
        with _open_store(db_path) as store:
            props = [
                proposal_harness.get_proposal(store, pid) for pid in selected_ids
            ]
        total_cost = sum(p.est_cost_usd for p in props if p is not None)

    st.divider()
    cols = st.columns([3, 2])
    with cols[0]:
        if selected_ids:
            st.markdown(
                f"**{len(selected_ids)} selected — est. total ${total_cost:.4f}**"
            )
        else:
            st.markdown("_Select at least one proposal to launch._")
    with cols[1]:
        missing = _check_api_key_prereqs()
        disabled = not selected_ids or bool(missing)
        if missing:
            st.error(
                "Missing env vars: " + ", ".join(f"`{m}`" for m in missing)
            )
        st.button(
            "🚀 Launch approved batch",
            disabled=disabled,
            use_container_width=True,
            on_click=_on_launch,
            kwargs={"db_path": db_path, "selected_ids": selected_ids},
            help=(
                "Marks each selected row as approved, then runs each through "
                "the same _run_one_brief() path the autonomous CLI uses. "
                "Rows move to 'executed' once the run loop completes."
            ),
        )


def _check_api_key_prereqs() -> list[str]:
    """Same prereq set as the existing run-test launch button."""
    missing: list[str] = []
    for var in (
        "CHARTBREAKER_TARGET_USER",
        "CHARTBREAKER_TARGET_PASSWORD",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ):
        if not os.environ.get(var):
            missing.append(var)
    return missing


def _render_history(db_path: str) -> None:
    with _open_store(db_path) as store:
        all_props = proposal_harness.list_all(store)
    historical = [p for p in all_props if p.status in ("rejected", "executed")]
    if not historical:
        return

    with st.expander(f"History — {len(historical)} decided proposal(s)"):
        rows = [
            {
                "status": p.status,
                "cat": p.subcategory_id,
                "specialist": p.specialist,
                "budget": p.mutation_budget,
                "decided_at": (p.decided_at or "")[:19],
                "decided_by": p.decided_by or "",
                "run_id": (p.run_id or "")[:8],
                "rejection_reason": p.rejection_reason or "",
                "proposal_id": p.proposal_id[:8],
            }
            for p in historical
        ]
        st.dataframe(rows, hide_index=True, use_container_width=True)


# ----------------------------------------------------------------------
# Auto-run mode (Phase 6)
#
# Subprocess-based — same pattern as the "Run test" sidebar button. The
# Streamlit page only operates the lifecycle (start / observe / stop);
# the actual loop lives in chartbreaker/auto_run.py and is driven by
# `python -m chartbreaker.cli auto-run-loop`. State persists to
# observability/auto-run-state.json so the page can render a status
# banner without polling the process.
#
# Auto-run bypasses the Phase-4 human-approval gate by design. Hard
# caps are mandatory (HARD_CAP_USD + HARD_CAP_ITERATIONS in
# chartbreaker/auto_run.py) so a runaway loop can't drain the LLM
# budget. Stop is decoupled via a touch-file the CLI polls between
# iterations.
# ----------------------------------------------------------------------


_AUTO_RUN_LOG_DIR = Path(__file__).resolve().parents[2] / "observability"


def _spawn_auto_run(
    *,
    max_iterations: int,
    max_cost_usd: float,
    proposals_per_iteration: int,
    semantic_judge: bool,
) -> subprocess.Popen:
    """Shell out to `python -m chartbreaker.cli auto-run-loop ...`.

    Detached so a Streamlit page reload (or a dashboard process restart)
    doesn't kill the loop. The CLI writes incrementally to runs.sqlite
    and to auto-run-state.json; this page tails both.
    """
    cmd: list[str] = [
        sys.executable,
        "-m",
        "chartbreaker.cli",
        "auto-run-loop",
        "--max-iterations",
        str(max_iterations),
        "--max-cost-usd",
        f"{max_cost_usd:.2f}",
        "--proposals-per-iteration",
        str(proposals_per_iteration),
    ]
    if semantic_judge:
        cmd.append("--semantic-judge")

    log_path = _AUTO_RUN_LOG_DIR / "auto-run-launches.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    fh = log_path.open("ab")
    fh.write(b"\n=== auto-run launched from dashboard ===\n")
    fh.write(" ".join(cmd).encode() + b"\n")
    fh.flush()
    return subprocess.Popen(
        cmd,
        cwd=str(_AUTO_RUN_LOG_DIR.parent),
        stdout=fh,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def _on_start_auto_run(*, db_path: str) -> None:
    if _auto_run.is_auto_run_active():
        st.session_state["_prop_error"] = (
            "Auto-run is already active. Stop it before starting a new one."
        )
        return
    missing = _check_api_key_prereqs()
    if missing:
        st.session_state["_prop_error"] = (
            "Cannot start auto-run — missing env vars: "
            + ", ".join(f"`{m}`" for m in missing)
        )
        return
    try:
        proc = _spawn_auto_run(
            max_iterations=int(st.session_state.get("auto_run_iterations", 5)),
            max_cost_usd=float(st.session_state.get("auto_run_max_cost", 5.0)),
            proposals_per_iteration=int(
                st.session_state.get("auto_run_proposals", 8)
            ),
            semantic_judge=bool(
                st.session_state.get("auto_run_semantic", False)
            ),
        )
    except Exception as exc:
        logger.exception("auto-run spawn failed")
        st.session_state["_prop_error"] = f"Failed to start auto-run: {exc}"
        return
    st.session_state["_prop_info"] = (
        f"Auto-run started — pid {proc.pid}. Status banner will appear above "
        "after the next refresh."
    )


def _on_stop_auto_run() -> None:
    state = _auto_run.AutoRunState.load()
    if state is None:
        st.session_state["_prop_error"] = "No auto-run state file to stop."
        return
    Path(state.stop_file).touch()
    st.session_state["_prop_info"] = (
        "Stop requested. The loop will exit after the current iteration "
        "finishes (≤ a few seconds)."
    )


@st.fragment(run_every="30s")
def _render_auto_run_panel(db_path: str) -> None:
    """Auto-run controls + status banner. Always rendered at the top.

    Decorated with ``st.fragment(run_every="30s")`` so the panel
    self-refreshes on a 30-second cadence WITHOUT triggering a full page
    reload. The old approach (a `<meta http-equiv="refresh">` tag) worked
    but reloaded the entire dashboard, which yanks st.tabs() back to its
    first tab and kicks the operator off Plan Next Run every 30 seconds
    while auto-run is active. Fragment-level reruns leave the tab state
    alone.

    Polling at 30s when no auto-run is active is essentially free — just
    a missing-file check on AutoRunState.load() — so the timer doesn't
    need to be conditional.
    """
    state = _auto_run.AutoRunState.load()
    active = state is not None and not state.stopped and _auto_run._is_pid_alive(state.pid)

    if active and state is not None:
        st.markdown(
            f"### 🔁 Auto-run iteration "
            f"**{state.iterations_done}/{state.max_iterations}** · "
            f"last run `{(state.last_run_id or '—')[:8]}` · "
            f"cost **${state.cost_so_far_usd:.4f}** / ${state.max_cost_usd:.2f}"
        )
        st.progress(
            min(1.0, state.iterations_done / max(1, state.max_iterations)),
            text="iteration progress",
        )
        if state.notes:
            with st.expander("Iteration notes"):
                for n in state.notes[-10:]:
                    st.markdown(f"- {n}")
        cols = st.columns([1, 3])
        with cols[0]:
            st.button(
                "🛑 Stop auto-run",
                on_click=_on_stop_auto_run,
                use_container_width=True,
                help="Touches the stop-file. The loop exits between iterations.",
            )
        with cols[1]:
            st.caption(
                "Banner auto-refreshes every 30s (fragment-scoped — won't "
                "lose your tab). Stop is honored at the next iteration "
                "boundary, not mid-batch."
            )
    else:
        # Show the most recent finished state if there is one, so the
        # operator can confirm a run finished + see why it stopped.
        if state is not None and state.stopped:
            st.info(
                f"Last auto-run finished — iterations="
                f"{state.iterations_done}/{state.max_iterations} · "
                f"cost ${state.cost_so_far_usd:.4f} · "
                f"reason: {state.stopped_reason}"
            )

        with st.expander("🔁 Auto-run mode (advanced)", expanded=False):
            st.warning(
                "Auto-run bypasses the human-approval gate. Hard caps below "
                "are enforced; the loop will not exceed them. Inspect the "
                "runs after — `chartbreaker audit-run` flags anomalies."
            )
            row = st.columns(3)
            with row[0]:
                st.number_input(
                    "Max iterations",
                    min_value=1,
                    max_value=_auto_run.HARD_CAP_ITERATIONS,
                    value=int(st.session_state.get("auto_run_iterations", 5)),
                    key="auto_run_iterations",
                    step=1,
                    help=(
                        f"Hard cap: {_auto_run.HARD_CAP_ITERATIONS}. "
                        "Each iteration generates proposals, auto-approves "
                        "every row, and launches the batch."
                    ),
                )
            with row[1]:
                st.number_input(
                    "Max cost (USD)",
                    min_value=0.50,
                    max_value=_auto_run.HARD_CAP_USD,
                    value=float(st.session_state.get("auto_run_max_cost", 5.0)),
                    step=0.50,
                    key="auto_run_max_cost",
                    help=(
                        f"Hard cap: ${_auto_run.HARD_CAP_USD:.0f}. Loop "
                        "exits before launching an iteration that would "
                        "exceed this total."
                    ),
                )
            with row[2]:
                st.number_input(
                    "Proposals / iteration",
                    min_value=1,
                    max_value=16,
                    value=int(st.session_state.get("auto_run_proposals", 8)),
                    step=1,
                    key="auto_run_proposals",
                    help="How many proposals to auto-approve each cycle.",
                )
            st.checkbox(
                "Use semantic Judge during auto-run",
                value=bool(st.session_state.get("auto_run_semantic", False)),
                key="auto_run_semantic",
                help=(
                    "Costs more (extra LLM call per attempt) but produces "
                    "the verifier↔semantic disagreement signal."
                ),
            )
            st.button(
                "▶️ Start auto-run",
                on_click=_on_start_auto_run,
                kwargs={"db_path": db_path},
                use_container_width=True,
                type="primary",
            )


# ----------------------------------------------------------------------
# Public entry point — wired from dashboard.py
# ----------------------------------------------------------------------


@st.fragment
def render(db_path: str) -> None:
    """Render the Plan Next Run tab against the given SQLite path.

    Wrapped in ``@st.fragment`` so widget interactions (Check-all,
    per-row checkboxes, Generate / Launch / Reject / Start auto-run
    buttons) rerun ONLY this fragment instead of the full dashboard
    script. Without it, every click triggers a top-level script rerun
    and ``st.tabs()`` defaults back to its first tab (Dashboard) —
    yanking the operator out of the Plan Next Run tab on every action.

    Fragment was added in Streamlit 1.33; project pin is 1.47.1.
    """
    _render_auto_run_panel(db_path)
    _render_header(db_path)
    selected_ids = _render_pending(db_path)
    _render_launch_footer(db_path, selected_ids)
    _render_history(db_path)
