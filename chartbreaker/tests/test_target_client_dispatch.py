"""Tests for the Phase-2 target_client dispatch extensions.

Covers:
- HttpRequestShape with multipart_files (Saboteur Cat 4a)
- HttpRequestShape with form_data (login form posts)
- HttpRequestShape with bypass_auth (Cracker Cat 6d/6e)
- TargetResponse capturing response_cookies + set_cookie_headers
"""

from __future__ import annotations

import base64

import pytest

from chartbreaker.state import HttpRequestShape, MultipartFile, TargetResponse


def test_multipart_file_round_trips_through_pydantic():
    """MultipartFile holds base64 content; this is what the Saboteur emits."""
    payload = b"\x00\x01\xffhello"
    mf = MultipartFile(
        field_name="file",
        filename="probe.png",
        content_b64=base64.b64encode(payload).decode("ascii"),
        content_type="image/png",
    )
    assert base64.b64decode(mf.content_b64) == payload
    assert mf.field_name == "file"


def test_http_request_shape_accepts_multipart_and_form_data():
    """The new fields are optional and default to None / False."""
    plain = HttpRequestShape(method="POST", path="/x", body={"k": "v"})
    assert plain.form_data is None
    assert plain.multipart_files is None
    assert plain.bypass_auth is False

    multipart = HttpRequestShape(
        method="POST",
        path="/run-extraction.php",
        form_data={"pid": "1"},
        multipart_files=[
            MultipartFile(
                field_name="file",
                filename="probe.png",
                content_b64=base64.b64encode(b"x").decode("ascii"),
                content_type="image/png",
            )
        ],
    )
    assert multipart.form_data == {"pid": "1"}
    assert len(multipart.multipart_files) == 1


def test_http_request_shape_bypass_auth_flag():
    """bypass_auth=True is required for Cracker Cat 6d / 6e probes."""
    req = HttpRequestShape(
        method="POST",
        path="/interface/main/main_screen.php?auth=login",
        form_data={"authUser": "wrong", "clearPass": "wrong"},
        bypass_auth=True,
    )
    assert req.bypass_auth is True
    # Not all fields populated — this is fine, body is None.
    assert req.body is None


def test_target_response_holds_response_cookies_and_set_cookie_headers():
    """TargetResponse carries cookie data so Cracker can audit fixation."""
    resp = TargetResponse(
        attempt_id="a-1",
        http_status=302,
        raw_model_output=None,
        post_verifier_output=None,
        latency_ms=42,
        target_version="unknown",
        response_cookies={"PHPSESSID": "abc123"},
        set_cookie_headers=["PHPSESSID=abc123; HttpOnly; Secure"],
    )
    assert resp.response_cookies == {"PHPSESSID": "abc123"}
    assert resp.set_cookie_headers == ["PHPSESSID=abc123; HttpOnly; Secure"]


def test_target_response_defaults_cookies_to_none():
    """Cookie fields are optional — Co-Pilot briefing path doesn't set them."""
    resp = TargetResponse(
        attempt_id="a-2",
        http_status=200,
        raw_model_output="{}",
        post_verifier_output="{}",
        latency_ms=10,
        target_version="gpt-5.4-mini",
    )
    assert resp.response_cookies is None
    assert resp.set_cookie_headers is None


def test_target_response_immutable():
    """Pydantic frozen=True still holds after the field additions."""
    resp = TargetResponse(
        attempt_id="a-3",
        http_status=200,
        raw_model_output=None,
        post_verifier_output=None,
        latency_ms=1,
        target_version="x",
    )
    with pytest.raises(Exception):
        resp.attempt_id = "a-changed"  # type: ignore[misc]
