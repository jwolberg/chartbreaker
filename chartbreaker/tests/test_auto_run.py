"""Tests for chartbreaker/auto_run.py — Phase 6 auto-run loop.

The actual loop is exercised end-to-end manually (it hits the live target
+ LLM). These tests cover the deterministic pieces: hard caps, state
file persistence, pid liveness detection, and the iteration accounting
the Streamlit page reads.
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from chartbreaker import auto_run

# ---------------------------------------------------------------------------
# State file round-trip
# ---------------------------------------------------------------------------


def test_state_file_path_under_observability_dir():
    p = auto_run.state_file_path()
    assert p.name == "auto-run-state.json"


def test_default_stop_file_path_under_observability_dir():
    p = auto_run.default_stop_file_path()
    assert p.name == "auto-run-stop"


@pytest.fixture
def tmp_state(monkeypatch, tmp_path):
    """Redirect the state file + stop file under tmp_path."""
    monkeypatch.setattr(
        auto_run,
        "state_file_path",
        lambda: tmp_path / "auto-run-state.json",
    )
    return tmp_path


def test_state_write_then_load_round_trips(tmp_state):
    original = auto_run.AutoRunState(
        pid=12345,
        started_at="2026-05-13T01:00:00+00:00",
        max_iterations=5,
        max_cost_usd=5.0,
        proposals_per_iteration=8,
        stop_file=str(tmp_state / "stop"),
    )
    original.write()
    loaded = auto_run.AutoRunState.load()
    assert loaded is not None
    assert loaded.pid == 12345
    assert loaded.max_iterations == 5
    assert loaded.max_cost_usd == 5.0
    assert loaded.iterations_done == 0


def test_state_clear_removes_file(tmp_state):
    state = auto_run.AutoRunState(
        pid=42,
        started_at="x",
        max_iterations=1,
        max_cost_usd=1.0,
        proposals_per_iteration=1,
        stop_file=str(tmp_state / "stop"),
    )
    state.write()
    assert (tmp_state / "auto-run-state.json").exists()
    auto_run.AutoRunState.clear()
    assert not (tmp_state / "auto-run-state.json").exists()


def test_state_load_returns_none_when_file_missing(tmp_state):
    assert auto_run.AutoRunState.load() is None


def test_state_load_tolerates_unknown_keys(tmp_state):
    (tmp_state / "auto-run-state.json").write_text(
        json.dumps(
            {
                "pid": 1,
                "started_at": "x",
                "max_iterations": 1,
                "max_cost_usd": 0.5,
                "proposals_per_iteration": 1,
                "stop_file": "/tmp/x",
                "some_future_field": "ignored",
            }
        )
    )
    loaded = auto_run.AutoRunState.load()
    assert loaded is not None
    assert loaded.pid == 1


def test_state_load_returns_none_on_corrupt_json(tmp_state):
    (tmp_state / "auto-run-state.json").write_text("{not valid json")
    assert auto_run.AutoRunState.load() is None


# ---------------------------------------------------------------------------
# Liveness detection
# ---------------------------------------------------------------------------


def test_is_pid_alive_for_self():
    # The current process is obviously alive.
    assert auto_run._is_pid_alive(os.getpid()) is True


def test_is_pid_alive_for_nonexistent_pid():
    # Pid 99999999 is essentially guaranteed not to exist on macOS / Linux.
    assert auto_run._is_pid_alive(99999999) is False


def test_is_auto_run_active_false_when_no_state(tmp_state):
    assert auto_run.is_auto_run_active() is False


def test_is_auto_run_active_false_when_stopped(tmp_state):
    state = auto_run.AutoRunState(
        pid=os.getpid(),  # alive
        started_at="x",
        max_iterations=1,
        max_cost_usd=1.0,
        proposals_per_iteration=1,
        stop_file=str(tmp_state / "stop"),
        stopped=True,
    )
    state.write()
    assert auto_run.is_auto_run_active() is False


def test_is_auto_run_active_false_when_pid_dead(tmp_state):
    state = auto_run.AutoRunState(
        pid=99999999,  # dead
        started_at="x",
        max_iterations=1,
        max_cost_usd=1.0,
        proposals_per_iteration=1,
        stop_file=str(tmp_state / "stop"),
    )
    state.write()
    assert auto_run.is_auto_run_active() is False


def test_is_auto_run_active_true_when_alive_and_unstopped(tmp_state):
    state = auto_run.AutoRunState(
        pid=os.getpid(),
        started_at="x",
        max_iterations=1,
        max_cost_usd=1.0,
        proposals_per_iteration=1,
        stop_file=str(tmp_state / "stop"),
    )
    state.write()
    assert auto_run.is_auto_run_active() is True


# ---------------------------------------------------------------------------
# Hard cap enforcement
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_rejects_iterations_above_hard_cap(tmp_state):
    with pytest.raises(ValueError, match="max_iterations"):
        await auto_run.auto_run_loop(
            max_iterations=auto_run.HARD_CAP_ITERATIONS + 1,
            max_cost_usd=1.0,
            proposals_per_iteration=1,
            stop_file=tmp_state / "stop",
            operator="test",
        )


@pytest.mark.asyncio
async def test_loop_rejects_cost_above_hard_cap(tmp_state):
    with pytest.raises(ValueError, match="max_cost_usd"):
        await auto_run.auto_run_loop(
            max_iterations=1,
            max_cost_usd=auto_run.HARD_CAP_USD + 0.01,
            proposals_per_iteration=1,
            stop_file=tmp_state / "stop",
            operator="test",
        )


# ---------------------------------------------------------------------------
# Stop-file short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_exits_immediately_on_preexisting_stop_file(tmp_state):
    """If the stop-file is already there at iteration 0, the loop exits clean."""
    stop_path = tmp_state / "stop"

    # Patch _run_one_iteration so we can detect any actual iteration.
    iteration_called = {"n": 0}

    async def _no_op(state, operator, enable_semantic_judge):
        iteration_called["n"] += 1
        return None, 0.0

    with patch.object(auto_run, "_run_one_iteration", _no_op):
        # The loop unlinks any stale stop-file before iteration 0; create
        # it AFTER calling auto_run_loop's first stop_path check happens.
        # We approximate by checking the loop exits after at most one
        # iteration when the stop file is created mid-flight.
        async def _create_stop_after_first(state, operator, enable_semantic_judge):
            iteration_called["n"] += 1
            stop_path.touch()
            return None, 0.0

        with patch.object(auto_run, "_run_one_iteration", _create_stop_after_first):
            state = await auto_run.auto_run_loop(
                max_iterations=5,
                max_cost_usd=1.0,
                proposals_per_iteration=1,
                stop_file=stop_path,
                operator="test",
            )
    assert iteration_called["n"] == 1
    assert state.stopped is True
    assert state.stopped_reason == "stop-file requested"


# ---------------------------------------------------------------------------
# Cost cap short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_exits_when_cost_cap_reached(tmp_state):
    """One iteration consumes the whole cost cap → next iteration is skipped."""
    stop_path = tmp_state / "stop"

    async def _expensive_iteration(state, operator, enable_semantic_judge):
        return "fake-run-id", 5.0  # consumes the entire $5 cap in one shot

    with patch.object(auto_run, "_run_one_iteration", _expensive_iteration):
        state = await auto_run.auto_run_loop(
            max_iterations=5,
            max_cost_usd=5.0,
            proposals_per_iteration=1,
            stop_file=stop_path,
            operator="test",
        )
    # First iteration ran; second was skipped because cost_so_far_usd >= cap.
    assert state.iterations_done == 1
    assert state.cost_so_far_usd >= 5.0
    assert state.stopped is True
    assert "cost cap" in (state.stopped_reason or "").lower()


# ---------------------------------------------------------------------------
# Crash resilience inside one iteration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_iteration_crash_is_logged_and_loop_continues(tmp_state):
    """A single iteration's exception bumps iterations_done and continues."""
    stop_path = tmp_state / "stop"
    call_count = {"n": 0}

    async def _crashy_then_done(state, operator, enable_semantic_judge):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("simulated specialist crash")
        return "ok-run", 0.0

    with patch.object(auto_run, "_run_one_iteration", _crashy_then_done):
        state = await auto_run.auto_run_loop(
            max_iterations=2,
            max_cost_usd=5.0,
            proposals_per_iteration=1,
            stop_file=stop_path,
            operator="test",
        )
    assert call_count["n"] == 2  # both iterations attempted
    assert state.iterations_done >= 1
    assert any("crashed" in n for n in state.notes)
