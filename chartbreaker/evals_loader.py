"""Loader for evals/seed_cases.yaml.

The seed cases are the canonical starting points for adversarial campaigns;
specialists mutate from these. The loader returns plain dicts (no Pydantic
model yet — the seed YAML carries shape variation across specialists, and
flattening it prematurely costs more than it saves).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from chartbreaker.config import SEED_CASES_YAML


def _repo_root() -> Path:
    """Return the repo root assuming this file lives at chartbreaker/evals_loader.py."""
    return Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def _load_all() -> list[dict]:
    """Read and cache the full seed case list."""
    path = _repo_root() / SEED_CASES_YAML
    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    cases = data["cases"] if isinstance(data, dict) and "cases" in data else data
    if not isinstance(cases, list):
        # Malformed file content, not a caller type error.
        raise ValueError(f"{SEED_CASES_YAML} did not yield a list of cases")  # noqa: TRY004
    return cases


def all_cases() -> list[dict]:
    """Return every seed case."""
    return list(_load_all())


def by_id(case_id: str) -> dict:
    """Return one seed case by its ID (e.g. 'AF-SEED-002'). Raises if not found."""
    for case in _load_all():
        if case.get("id") == case_id:
            return case
    raise KeyError(f"seed case {case_id!r} not found in {SEED_CASES_YAML}")


def by_subcategory(subcategory_id: str) -> list[dict]:
    """Return every seed case targeting the given subcategory (e.g. '1b')."""
    return [c for c in _load_all() if c.get("subcategory") == subcategory_id]
