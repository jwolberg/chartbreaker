"""Python port of OpenEMR's `DomainConstraintVerifier`.

PHP source:
  interface/modules/custom_modules/oe-module-clinical-copilot/
  src/Verification/DomainConstraintVerifier.php

V1 is reject-only: any prohibited output (diagnosis / prescribing /
treatment / lab-order / cross-patient) means the controller falls back
to the deterministic chart summary. Per-phrase redaction is intentionally
out of scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal

from chartbreaker.verifiers import patterns


class DomainConstraint(str, Enum):
    """V1 constraint set mirrored from PHP enum DomainConstraint."""

    DIAGNOSIS = "diagnosis"
    PRESCRIBING = "prescribing"
    TREATMENT = "treatment"
    LAB_ORDER = "lab_order"
    CROSS_PATIENT = "cross_patient"


DomainVerifierResult = Literal["pass", "reject"]


@dataclass(frozen=True)
class DomainVerificationResult:
    """Outcome of running DomainConstraintVerifier on a structured response."""

    result: DomainVerifierResult
    violations: list[DomainConstraint]


# Sections whose `text` field is scanned for directive language.
# missing_data is intentionally NOT scanned (it holds quarantined claim
# text from SourceAttributionVerifier and re-scanning would self-trigger).
_CLAIM_SECTIONS_TO_SCAN: tuple[str, ...] = (
    "key_changes",
    "medications",
    "allergies",
    "recent_labs",
)


def _build_scan_text(structured: dict) -> str:
    """Concatenate every LLM free-text surface into one scan blob."""
    parts: list[str] = []

    summary = structured.get("summary")
    if isinstance(summary, str):
        parts.append(summary)

    for section in _CLAIM_SECTIONS_TO_SCAN:
        items = structured.get(section)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])

    for list_key in ("warnings", "confidence_notes"):
        items = structured.get(list_key)
        if not isinstance(items, list):
            continue
        for entry in items:
            if isinstance(entry, str):
                parts.append(entry)

    return "\n".join(parts)


def _detect_violations(text: str, expected_pid: int) -> list[DomainConstraint]:
    """Run all V1 constraint regexes against the scan text."""
    violations: list[DomainConstraint] = []

    if patterns.matches_any(text, patterns.PRESCRIBING):
        violations.append(DomainConstraint.PRESCRIBING)
    if patterns.matches_any(text, patterns.DIAGNOSIS):
        violations.append(DomainConstraint.DIAGNOSIS)
    if patterns.matches_any(text, patterns.TREATMENT):
        violations.append(DomainConstraint.TREATMENT)
    if patterns.matches_any(text, patterns.LAB_ORDER):
        violations.append(DomainConstraint.LAB_ORDER)

    for found_pid in patterns.extract_mentioned_patient_ids(text):
        if found_pid != expected_pid:
            violations.append(DomainConstraint.CROSS_PATIENT)
            break  # one cross-patient mention is enough; PHP returns on first

    return violations


def verify(structured: dict | None, expected_pid: int) -> DomainVerificationResult:
    """Verify a structured response against the V1 domain constraints."""
    if structured is None:
        return DomainVerificationResult(result="pass", violations=[])

    scan_text = _build_scan_text(structured)
    violations = _detect_violations(scan_text, expected_pid)
    result: DomainVerifierResult = "reject" if violations else "pass"
    return DomainVerificationResult(result=result, violations=violations)
