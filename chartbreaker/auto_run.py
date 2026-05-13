"""Auto-run loop — Phase 6 dashboard feature.

Continuously generates proposals via the Phase-4 harness, auto-approves
every row, executes the batch, and feeds the resulting telemetry back
into the next iteration's Orchestrator scoring. Bounded by two hard
caps (max iterations + max cost) and an out-of-band stop file the
operator's Streamlit page touches via the "Stop auto-run" button.

Run as a subprocess from the dashboard:

    python -m chartbreaker.cli auto-run-loop \\
        --max-iterations 5 \\
        --max-cost-usd 5.00 \\
        --stop-file observability/auto-run-stop

State is persisted to ``observability/auto-run-state.json`` after every
iteration so the dashboard can render an "iteration N/5, last run_id …,
cost $X.YY of $Z.ZZ" status banner without polling the process.

Safety posture: auto-run literally bypasses the Phase-4 human-approval
gate. The hard caps + stop-file + state-visibility are the trade for
that. Removing any of the three is a deliberate change with security
implications — see docs/ARCHITECTURE.md § Human Approval Gates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from chartbreaker.config import OBSERVABILITY_DIR
from chartbreaker.observability.store import ObservabilityStore
from chartbreaker.orchestrator import proposal_harness

logger = logging.getLogger(__name__)


# Hard ceiling on operator-supplied caps. Defends against a fat-fingered
# CLI flag (e.g. --max-cost-usd 5000) that the operator didn't intend.
# Override only by editing this constant; intentional design.
HARD_CAP_USD = 50.0
HARD_CAP_ITERATIONS = 25

# Default per-iteration cap on how many proposals to generate. The auto
# run shouldn't iterate against an unbounded slate — narrower is safer.
DEFAULT_PROPOSALS_PER_ITERATION = 8

# How often (seconds) to poll the stop-file between iterations. The
# Streamlit Stop button creates the file; the loop notices on the next
# poll.
STOP_POLL_SECONDS = 1.0


# ---------------------------------------------------------------------------
# State file (read by the Streamlit page; written by this module).
# ---------------------------------------------------------------------------


def state_file_path() -> Path:
    return Path(OBSERVABILITY_DIR) / "auto-run-state.json"


def default_stop_file_path() -> Path:
    return Path(OBSERVABILITY_DIR) / "auto-run-stop"


@dataclass
class AutoRunState:
    """Snapshot of an in-progress auto-run. Persisted to JSON every iteration."""

    pid: int
    started_at: str
    max_iterations: int
    max_cost_usd: float
    proposals_per_iteration: int
    stop_file: str
    iterations_done: int = 0
    cost_so_far_usd: float = 0.0
    last_run_id: str | None = None
    last_iteration_finished_at: str | None = None
    stopped: bool = False
    stopped_reason: str | None = None
    # Optional bookkeeping the dashboard can render.
    notes: list[str] = field(default_factory=list)

    def write(self) -> None:
        path = state_file_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls) -> "AutoRunState | None":
        path = state_file_path()
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(data, dict):
            return None
        # Tolerate unknown keys for forward-compat by filtering.
        valid_keys = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in valid_keys})

    @classmethod
    def clear(cls) -> None:
        path = state_file_path()
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


def _is_pid_alive(pid: int) -> bool:
    """True if ``pid`` is still running. POSIX-only (works on macOS + Linux)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by someone else
    return True


def is_auto_run_active() -> bool:
    """True iff a state file exists AND the recorded pid is still alive."""
    state = AutoRunState.load()
    if state is None:
        return False
    if state.stopped:
        return False
    return _is_pid_alive(state.pid)


async def _run_one_iteration(
    state: AutoRunState,
    operator: str,
    enable_semantic_judge: bool,
) -> tuple[str | None, float]:
    """Propose → auto-approve → execute one batch. Returns (run_id, run_cost_usd)."""
    with ObservabilityStore() as store:
        # 1. Generate fresh proposals (priority math re-scores against
        #    the run telemetry written by previous iterations).
        props = proposal_harness.propose(
            store, n=state.proposals_per_iteration
        )
        if not props:
            return None, 0.0

        # 2. Auto-approve every newly-generated proposal. The
        #    decided_by stamp marks them as auto-run-authored so
        #    they're auditable after the fact.
        for p in props:
            proposal_harness.approve(
                store,
                p.proposal_id,
                decided_by=f"auto-run:{operator}",
            )

        # 3. Execute the approved batch via the Phase-4 harness.
        run_id = await proposal_harness.execute_approved_batch(
            store, operator=f"auto-run:{operator}"
        )

        # 4. Measure cost for this iteration so the cap is enforced.
        run_cost = store.cost_total_for_run(run_id) if run_id else 0.0
        return run_id or None, float(run_cost)


async def auto_run_loop(
    *,
    max_iterations: int,
    max_cost_usd: float,
    proposals_per_iteration: int,
    stop_file: str | Path,
    operator: str,
    enable_semantic_judge: bool = False,
) -> AutoRunState:
    """Drive the iterative loop. Returns the final state for inspection."""
    if max_iterations > HARD_CAP_ITERATIONS:
        raise ValueError(
            f"max_iterations {max_iterations} exceeds hard cap "
            f"{HARD_CAP_ITERATIONS}. Lower the flag or edit auto_run.py."
        )
    if max_cost_usd > HARD_CAP_USD:
        raise ValueError(
            f"max_cost_usd ${max_cost_usd:.2f} exceeds hard cap "
            f"${HARD_CAP_USD:.2f}. Lower the flag or edit auto_run.py."
        )

    from datetime import datetime, timezone

    stop_path = Path(stop_file)
    stop_path.unlink(missing_ok=True)  # clear stale stop-file from prior session

    state = AutoRunState(
        pid=os.getpid(),
        started_at=datetime.now(tz=timezone.utc).isoformat(),
        max_iterations=max_iterations,
        max_cost_usd=max_cost_usd,
        proposals_per_iteration=proposals_per_iteration,
        stop_file=str(stop_path),
    )
    state.write()
    logger.info(
        "auto-run started: pid=%d max_iter=%d max_cost=$%.2f",
        state.pid, max_iterations, max_cost_usd,
    )

    try:
        for i in range(max_iterations):
            # Stop file check — fast path before doing anything expensive.
            if stop_path.exists():
                state.stopped = True
                state.stopped_reason = "stop-file requested"
                state.notes.append(
                    f"iteration {i+1}/{max_iterations} skipped: stop requested"
                )
                state.write()
                break

            if state.cost_so_far_usd >= max_cost_usd:
                state.stopped = True
                state.stopped_reason = (
                    f"cost cap reached (${state.cost_so_far_usd:.4f} ≥ "
                    f"${max_cost_usd:.2f})"
                )
                state.write()
                break

            logger.info("auto-run iteration %d/%d", i + 1, max_iterations)
            try:
                run_id, run_cost = await _run_one_iteration(
                    state, operator, enable_semantic_judge
                )
            except Exception as exc:  # noqa: BLE001 — bound to iteration
                logger.exception("auto-run iteration %d crashed", i + 1)
                state.notes.append(
                    f"iteration {i+1} crashed: {type(exc).__name__}: {exc}"
                )
                state.iterations_done = i + 1
                state.write()
                # One crashed iteration shouldn't bring the loop down —
                # the user asked for a "until stopped" loop. Continue.
                continue

            state.iterations_done = i + 1
            state.cost_so_far_usd += run_cost
            state.last_run_id = run_id
            state.last_iteration_finished_at = datetime.now(
                tz=timezone.utc
            ).isoformat()
            if run_id is None:
                state.notes.append(
                    f"iteration {i+1} produced no new proposals — "
                    "Orchestrator may be at coverage saturation"
                )
            state.write()

            # Light pause so a fast loop doesn't hammer the target's
            # rate limits + gives the stop-file a chance between ticks.
            time.sleep(STOP_POLL_SECONDS)
        else:
            state.stopped = True
            state.stopped_reason = "max iterations reached"
            state.write()
    finally:
        if not state.stopped:
            state.stopped = True
            state.stopped_reason = state.stopped_reason or "loop exited"
        state.write()
        stop_path.unlink(missing_ok=True)
        logger.info(
            "auto-run finished: iterations=%d cost=$%.4f reason=%s",
            state.iterations_done,
            state.cost_so_far_usd,
            state.stopped_reason,
        )
    return state


def auto_run_loop_sync(**kwargs: Any) -> AutoRunState:
    """Synchronous wrapper for CLI invocation."""
    return asyncio.run(auto_run_loop(**kwargs))
