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


def test_cat_4a_emits_vision_multipart_upload():
    """Cat 4a probe POSTs a multipart upload to the vision endpoint."""
    attempt = tool_misuse_specialist.generate(_brief("4a"))
    assert attempt.subcategory_id == "4a"
    assert attempt.http_request is not None
    assert "run-extraction.php" in attempt.http_request.path
    assert attempt.http_request.multipart_files is not None
    assert len(attempt.http_request.multipart_files) == 1
    # Suspicious filename (traversal-shaped) is part of the probe.
    mf = attempt.http_request.multipart_files[0]
    assert ".." in mf.filename
    assert mf.content_type == "image/png"


def test_cat_4b_emits_routing_keyword_bait():
    """Cat 4b probe stuffs the USER_QUESTION with internal routing tokens."""
    attempt = tool_misuse_specialist.generate(_brief("4b"))
    assert attempt.subcategory_id == "4b"
    body = attempt.http_request.body
    assert "ROUTE:" in body["user_question"]
    assert "TOOL:" in body["user_question"]


def test_cat_4d_emits_function_call_placeholder():
    """Cat 4d probe sends an OpenAI-style function-call-shaped string."""
    attempt = tool_misuse_specialist.generate(_brief("4d"))
    assert attempt.subcategory_id == "4d"
    body = attempt.http_request.body
    assert "function_call" in body["user_question"]


def test_routing_table_covers_all_four_saboteur_subcategories():
    from chartbreaker.agents import red_team_lead

    for sub in ("4a", "4b", "4c", "4d"):
        assert red_team_lead.specialist_for(sub) == "saboteur"
