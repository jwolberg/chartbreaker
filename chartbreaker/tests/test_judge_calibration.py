"""Calibration accuracy test for the semantic Judge.

Skipped by default — opt in by setting `CHARTBREAKER_RUN_CALIBRATION=1`.
Requires `OPENAI_API_KEY` to be set (or whatever provider key the
`judge_semantic` role is configured for in MODEL_REGISTRY).

Behavior:
- ≥85% match accuracy → PASS
- 70%–85%             → WARN (test xfails with a clear message)
- <70%                → FAIL (Judge is unfit)
"""

from __future__ import annotations

import asyncio
import os

import pytest

from chartbreaker import calibration


def _calibration_opted_in() -> bool:
    return os.environ.get("CHARTBREAKER_RUN_CALIBRATION") == "1"


@pytest.mark.skipif(
    not _calibration_opted_in(),
    reason="Set CHARTBREAKER_RUN_CALIBRATION=1 to run live Judge calibration.",
)
def test_judge_semantic_calibration_accuracy() -> None:
    summary = asyncio.run(calibration.run_calibration())

    print(
        f"\nJudge calibration: {summary.matched}/{summary.total} = {summary.accuracy:.2%}"
    )
    for r in summary.results:
        mark = "OK " if r.matched else "MISS"
        print(
            f"  {mark} {r.record_id} expected={r.expected_semantic} got={r.actual_semantic}"
        )

    assert summary.accuracy >= calibration.HALT_THRESHOLD, (
        f"Judge calibration accuracy {summary.accuracy:.0%} is below halt threshold "
        f"{calibration.HALT_THRESHOLD:.0%}. The Judge model is unfit — bump the model "
        f"or re-tune the prompt before continuing."
    )
    if summary.accuracy < calibration.WARN_THRESHOLD:
        pytest.xfail(
            f"Judge calibration {summary.accuracy:.0%} is below warn threshold "
            f"{calibration.WARN_THRESHOLD:.0%}; investigate before next run."
        )
