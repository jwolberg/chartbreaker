"""Tests for the P2.5-T6 run-log auto-capture helpers."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from chartbreaker import cli


def test_attach_auto_path_uses_run_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`auto` resolves to <observability_dir>/run-<run_id>.log."""
    monkeypatch.setattr(cli.config, "OBSERVABILITY_DIR", str(tmp_path))
    handler = cli._attach_run_log("abc-123", "auto")
    try:
        assert Path(handler.baseFilename) == tmp_path / "run-abc-123.log"
        assert Path(handler.baseFilename).exists()
    finally:
        cli._detach_run_log(handler)


def test_attach_writes_log_records_to_file(tmp_path: Path) -> None:
    log_path = tmp_path / "explicit.log"
    handler = cli._attach_run_log("run-1", str(log_path))
    try:
        logging.getLogger("chartbreaker.test").error("hello from test")
    finally:
        cli._detach_run_log(handler)
    contents = log_path.read_text()
    assert "hello from test" in contents
    assert "ERROR" in contents
    assert "chartbreaker.test" in contents


def test_detach_removes_handler_from_root_logger(tmp_path: Path) -> None:
    log_path = tmp_path / "x.log"
    root = logging.getLogger()
    before = list(root.handlers)
    handler = cli._attach_run_log("run-1", str(log_path))
    assert handler in root.handlers
    cli._detach_run_log(handler)
    assert handler not in root.handlers
    # Root logger state matches the pre-attach snapshot.
    assert list(root.handlers) == before


def test_attach_creates_parent_directories(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "nested" / "run.log"
    assert not nested.parent.exists()
    handler = cli._attach_run_log("run-1", str(nested))
    try:
        assert nested.exists()
    finally:
        cli._detach_run_log(handler)
