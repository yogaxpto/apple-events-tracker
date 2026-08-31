"""Tests for run notifications (notify.notify_new_events CI-note channel)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apple_events_tracker import config as config_module
from apple_events_tracker import notify
from apple_events_tracker.config import RuntimeConfig
from apple_events_tracker.model import Event


def _event() -> Event:
    return Event(
        key="special-event-2026-09-09",
        title="Apple Event",
        kind="special-event",
        status="upcoming",
        start="2026-09-09T10:00:00-07:00",
        all_day=False,
        source_url=config_module.SOURCE_URL,
    )


def test_new_event_noted_on_ci_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given a newly-detected event and an Actions job-summary file
    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    # When the notification runs
    notify.notify_new_events([_event()], RuntimeConfig())

    # Then the event is appended to the job summary and annotated on the run
    summary = summary_file.read_text(encoding="utf-8")
    assert "### New Apple event detected: Apple Event" in summary
    assert "- **Apple Event** — 2026-09-09T10:00:00-07:00 (special-event)" in summary
    out = capsys.readouterr().out
    assert out.startswith("::notice title=New Apple event detected: Apple Event::")
    assert "Apple Event on 2026-09-09T10:00:00-07:00" in out


def test_no_new_events_is_a_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given no newly-detected events
    summary_file = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_file))

    # When the notification runs
    notify.notify_new_events([], RuntimeConfig())

    # Then nothing is written or annotated
    assert not summary_file.exists()
    assert capsys.readouterr().out == ""


def test_outside_actions_only_logs(capsys: pytest.CaptureFixture[str]) -> None:
    # Given no Actions environment (conftest strips GITHUB_STEP_SUMMARY)
    # When the notification runs
    notify.notify_new_events([_event()], RuntimeConfig())

    # Then no workflow command is emitted (the log line is the whole announcement)
    assert capsys.readouterr().out == ""
