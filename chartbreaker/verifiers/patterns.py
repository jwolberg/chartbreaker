"""Directive-language regex set + cross-patient extractor.

Port of OpenEMR's `DirectiveLanguagePatterns` PHP class
(interface/modules/custom_modules/oe-module-clinical-copilot/
src/Verification/DirectiveLanguagePatterns.php).

Pattern bodies are mirrored character-for-character from the PHP source,
adjusted for Python regex syntax (PHP-style `/.../i` delimiters become
`re.IGNORECASE` on the compiled object).
"""

from __future__ import annotations

import re


# Prescribing — verbs of recommendation followed by med-change verbs;
# OR modal-verb forms like "should start", "must prescribe".
PRESCRIBING: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:recommend|recommends|recommended|recommending|suggest|suggests|suggested|suggesting"
        r"|advise|advises|advised|advising|consider|considers|considered|considering"
        r"|propose|proposes|proposed|proposing)\s+"
        r"(?:starting|adding|stopping|discontinuing|switching|increasing|decreasing"
        r"|titrating|tapering|prescribing|initiating|holding|resuming)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:should|must|need\s+to|needs\s+to)\s+"
        r"(?:start|started|initiate|prescribe|discontinue|stop|increase|decrease"
        r"|titrate|taper|switch)\b",
        re.IGNORECASE,
    ),
)


# Diagnosis — narrow directive forms only. Descriptive uses
# ("diagnosis of HTN", "diagnosed with diabetes") are intentionally
# allowed because chart summaries use them frequently.
DIAGNOSIS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:should|would|might|could|will|may)\s+diagnos(?:e|ing)\b", re.IGNORECASE),
    re.compile(r"\b(?:i|we|you)\s+diagnos(?:e|es|ing|ed)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:differential|working|leading|primary|likely|suspected|presumed)\s+"
        r"diagnos(?:is|es|e|ing|ed)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:appears|seems|appearing|seeming)\s+to\s+have\s+(?:a\s+)?"
        r"diagnos(?:is|es|e|ing|ed)\s+of\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:consistent\s+with|suggestive\s+of)\s+(?:a\s+)?"
        r"diagnos(?:is|es|e|ing|ed)\s+of\b",
        re.IGNORECASE,
    ),
)


TREATMENT: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:recommend|recommends|recommended|suggest|suggests|suggested"
        r"|advise|advises|advised|consider|considers|considered"
        r"|propose|proposes|proposed)\s+"
        r"(?:treating|treatment|treatments|therapy|therapies)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\btreatment\s+plan\s+(?:should|would|will|may|might|must|needs)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\btreat(?:s|ed|ing)?\s+(?:the\s+patient|him|her|them)\s+with\b",
        re.IGNORECASE,
    ),
)


LAB_ORDER: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:recommend|recommends|recommended|suggest|suggests|suggested"
        r"|advise|advises|advised|consider|considers|considered"
        r"|propose|proposes|proposed)\s+"
        r"(?:ordering|obtaining|drawing|rechecking|repeating|sending\s+for)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:should|must|need\s+to|needs\s+to)\s+"
        r"(?:order|obtain|draw|recheck|repeat|send\s+for)\s+"
        r"(?:a|an|the|another)?\s*(?:lab|labs|panel|test|cbc|cmp|bmp|hba1c|tsh|lfts?|ua)\b",
        re.IGNORECASE,
    ),
)


# Cross-patient: any "pid: 123" or "patient id #: 456" form.
CROSS_PATIENT: re.Pattern[str] = re.compile(
    r"\b(?:pid|patient[\s_-]*(?:id|#|number))\s*[#:]?\s*(\d{2,})\b",
    re.IGNORECASE,
)


def matches_any(text: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    """Return True iff any pattern in the tuple matches the text."""
    return any(p.search(text) for p in patterns)


def extract_mentioned_patient_ids(text: str) -> list[int]:
    """Return all `pid: N` / `patient id #: N` digits in the text, in order."""
    return [int(m) for m in CROSS_PATIENT.findall(text)]
