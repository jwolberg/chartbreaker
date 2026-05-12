"""Tests for the Saboteur (tool-misuse / param tampering) specialist."""

from __future__ import annotations

import pytest

from chartbreaker.agents.specialists import tool_misuse_specialist
from chartbreaker.state import CampaignBrief


def _brief(subcategory: str) -> CampaignBrief:
    return CampaignBrief(
        subcategory_id=subcategory,
        mutation_budget=1,
        max_cost_usd=0.10,
        rationale="test",
    )


def test_cat_4c_emits_oversized_user_question():
    attempt = tool_misuse_specialist.generate(_brief("4c"))
    assert attempt.specialist == "saboteur"
    assert attempt.subcategory_id == "4c"
    assert attempt.http_request is not None
    user_question = attempt.http_request.body["user_question"]
    assert len(user_question) > 1000  # over the documented cap


def test_unknown_subcategory_raises():
    with pytest.raises(ValueError, match="Saboteur does not cover"):
        tool_misuse_specialist.generate(_brief("9z"))
