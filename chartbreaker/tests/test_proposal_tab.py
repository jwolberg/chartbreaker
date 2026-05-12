"""Smoke tests for the Streamlit Plan Next Run tab (P4-T3).

Following the same pattern as test_dashboard.py: we do not stand up a
live Streamlit server. Instead we verify the module imports, exposes
the expected entry points, and that the underlying persistence layer
the tab consumes still works after a fresh dashboard "session" (a new
ObservabilityStore against the same SQLite path).

Skips gracefully when streamlit is not installed (e.g. an `.venv`
environment that mirrors the minimal runtime requirements.txt).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

# Streamlit is the dashboard's optional dependency — see requirements.txt.
# When the test environment lacks it, skip the entire module rather than
# erroring at collection time.
pytest.importorskip("streamlit")

from chartbreaker.observability import proposal_tab  # noqa: E402
from chartbreaker.observability.store import ObservabilityStore  # noqa: E402
from chartbreaker.orchestrator import proposal_harness  # noqa: E402


async def _stub_rationale(**_kwargs) -> str:
    return "stub rationale"


def test_tab_module_imports_and_exposes_render() -> None:
    """AC-1 prerequisite: the tab module loads cleanly."""
    assert hasattr(proposal_tab, "render")
    assert callable(proposal_tab.render)


def test_check_api_key_prereqs_lists_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tab's prereq helper surfaces exactly the missing env vars."""
    for var in (
        "CHARTBREAKER_TARGET_USER",
        "CHARTBREAKER_TARGET_PASSWORD",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    missing = proposal_tab._check_api_key_prereqs()  # noqa: SLF001
    assert set(missing) >= {
        "CHARTBREAKER_TARGET_USER",
        "CHARTBREAKER_TARGET_PASSWORD",
        "OPENROUTER_API_KEY",
        "OPENAI_API_KEY",
    }


def test_open_store_opens_against_given_path(tmp_path: Path) -> None:
    """The store-opening helper is parameterized on db_path so the tab can
    target any DB the dashboard is rooted at."""
    db_path = tmp_path / "runs.sqlite"
    trace_path = tmp_path / "traces.jsonl"
    # First open creates the schema, including proposed_campaigns.
    with proposal_tab._open_store(str(db_path)) as store:  # noqa: SLF001
        # Persist one proposal so the next session can read it back.
        proposal_harness.propose(store, n=1, render_rationale=_stub_rationale)

    # Second "session" — fresh store, same path.
    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as fresh:
        pending = proposal_harness.list_pending(fresh)
        assert len(pending) == 1
        assert pending[0].status == "proposed"


def test_proposals_persist_across_dashboard_restart(tmp_path: Path) -> None:
    """AC-10 — pending proposals survive a streamlit run restart.

    Modelled as: write proposals via one store context, then open a
    fresh ObservabilityStore against the same DB path and read them
    back. This is exactly the data path the Streamlit tab takes since
    each callback opens its own short-lived store.
    """
    db_path = tmp_path / "runs.sqlite"
    trace_path = tmp_path / "traces.jsonl"

    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as store:
        props = proposal_harness.propose(
            store, n=2, render_rationale=_stub_rationale
        )
        ids = {p.proposal_id for p in props}

    # New "session" — completely fresh store.
    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as store2:
        rehydrated = proposal_harness.list_pending(store2)
        assert {p.proposal_id for p in rehydrated} == ids


def test_cost_visible_via_get_proposal(tmp_path: Path) -> None:
    """AC-9 prerequisite: the est_cost_usd field is queryable per row so
    the tab footer can sum it. (Footer rendering itself uses Streamlit
    primitives we cannot drive headlessly.)"""
    db_path = tmp_path / "runs.sqlite"
    trace_path = tmp_path / "traces.jsonl"

    with ObservabilityStore(db_path=db_path, trace_path=trace_path) as store:
        [p] = proposal_harness.propose(
            store, n=1, render_rationale=_stub_rationale
        )
        assert p.est_cost_usd >= 0.0
        roundtrip = proposal_harness.get_proposal(store, p.proposal_id)
        assert roundtrip is not None
        assert roundtrip.est_cost_usd == p.est_cost_usd
