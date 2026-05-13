"""Isolated tests for the P5-T2 per-subcategory reporting additions.

These tests don't touch the live Judge — they construct CalibrationResult
rows directly to exercise the aggregation + threshold logic.
"""

from __future__ import annotations

from chartbreaker import calibration


def _result(record_id: str, subcategory: str, matched: bool) -> calibration.CalibrationResult:
    return calibration.CalibrationResult(
        record_id=record_id,
        subcategory_id=subcategory,
        expected_semantic="pass",
        actual_semantic="pass" if matched else "fail",
        matched=matched,
        rationale="test",
    )


def test_aggregate_per_subcategory_empty():
    assert calibration._aggregate_per_subcategory([]) == []


def test_aggregate_per_subcategory_groups_by_subcategory():
    results = [
        _result("A", "1a", True),
        _result("B", "1a", True),
        _result("C", "1a", False),
        _result("D", "2b", True),
    ]
    buckets = calibration._aggregate_per_subcategory(results)
    by_sub = {b.subcategory_id: b for b in buckets}
    assert by_sub["1a"].records == 3
    assert by_sub["1a"].correct == 2
    assert by_sub["1a"].accuracy == 2 / 3
    assert by_sub["2b"].records == 1
    assert by_sub["2b"].correct == 1
    assert by_sub["2b"].accuracy == 1.0


def test_aggregate_per_subcategory_sorted_by_id():
    results = [
        _result("X", "6b", True),
        _result("Y", "1a", True),
        _result("Z", "2c", True),
    ]
    buckets = calibration._aggregate_per_subcategory(results)
    assert [b.subcategory_id for b in buckets] == ["1a", "2c", "6b"]


def test_below_threshold_subcategories_flags_only_bad_buckets():
    summary = calibration.CalibrationSummary(
        total=10,
        matched=8,
        accuracy=0.80,
        threshold_action="warn",
        results=[],
        per_subcategory=[
            calibration.SubcategoryAccuracy("1a", records=4, correct=4),  # 100%
            calibration.SubcategoryAccuracy("2b", records=4, correct=2),  # 50%
            calibration.SubcategoryAccuracy("6b", records=2, correct=2),  # 100%
        ],
    )
    bad = summary.below_threshold_subcategories(threshold=0.70)
    assert [b.subcategory_id for b in bad] == ["2b"]


def test_below_threshold_default_is_halt_threshold():
    """Default threshold matches the platform halt threshold (70%)."""
    summary = calibration.CalibrationSummary(
        total=2,
        matched=1,
        accuracy=0.50,
        threshold_action="halt",
        results=[],
        per_subcategory=[
            calibration.SubcategoryAccuracy("1a", records=2, correct=1),  # 50%
        ],
    )
    assert summary.below_threshold_subcategories() == summary.per_subcategory
    assert summary.below_threshold_subcategories(0.40) == []


def test_subcategory_for_record_prefers_explicit_field():
    """Explicit `subcategory` on the record wins over the attack_id map."""
    record = {"attack_id": "AF-SEED-001", "subcategory": "5c"}
    assert calibration._subcategory_for(record) == "5c"


def test_subcategory_for_record_falls_back_to_attack_id_map():
    record = {"attack_id": "AF-SEED-002"}  # mapped to 1b
    assert calibration._subcategory_for(record) == "1b"


def test_subcategory_for_record_defaults_when_unknown():
    record = {"attack_id": "AF-SEED-9999"}  # not in the map
    assert calibration._subcategory_for(record) == "1a"
