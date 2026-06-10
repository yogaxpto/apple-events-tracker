"""Tests for the iCalendar feed generator (§6, CAL-1..CAL-6, OD-5)."""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from icalendar import Calendar

from apple_events_tracker.config import RuntimeConfig, SiteConfig
from apple_events_tracker.ics import build_calendar, validate_ics, write_feed
from apple_events_tracker.model import Event, EventStore


def _config() -> RuntimeConfig:
    return RuntimeConfig(site=SiteConfig(owner="acme", repo="apple-events"))


def _store() -> EventStore:
    upcoming = Event(
        key="wwdc-2026-06-08",
        title="WWDC26",
        kind="wwdc",
        status="upcoming",
        start="2026-06-08T10:00:00-07:00",
        end="2026-06-08T12:00:00-07:00",
        tzid="America/Los_Angeles",
        all_day=False,
        source_url="https://www.apple.com/apple-events/",
        watch_url="https://www.apple.com/apple-events/watch/",
        uid="wwdc26-official@apple.com",
        description="Watch online today at 10 a.m. PT.",
    )
    past_a = Event(
        key="special-event-2025-09-09",
        title="Apple Event (September 2025)",
        kind="special-event",
        status="past",
        start="2025-09-09",
        all_day=True,
        source_url="https://www.apple.com/apple-events/",
        description="iPhone reveal.",
    )
    past_b = Event(
        key="special-event-2024-10-28",
        title="Apple Event (October 2024)",
        kind="special-event",
        status="past",
        start="2024-10-28",
        all_day=True,
        source_url="https://www.apple.com/apple-events/",
        description="Mac announcements.",
        uid="",  # forces fallback UID
    )
    store = EventStore(events=[upcoming, past_a, past_b])
    store.generated_at = "2026-06-08T08:00:00Z"
    return store


def _vevent_by_uid(cal: Calendar, uid: str) -> Any:
    for ve in cal.walk("VEVENT"):
        if str(ve.get("UID")) == uid:
            return ve
    raise AssertionError(f"no VEVENT with UID {uid!r}")


def test_build_calendar_parses_and_validates() -> None:
    data = build_calendar(_store(), _config())
    validate_ics(data)  # must not raise
    cal = Calendar.from_ical(data)
    assert len(list(cal.walk("VEVENT"))) == 3


def test_calendar_metadata() -> None:
    cal = Calendar.from_ical(build_calendar(_store(), _config()))
    assert str(cal.get("PRODID")) == "-//Apple Events Tracker//EN"
    assert str(cal.get("METHOD")) == "PUBLISH"
    assert str(cal.get("VERSION")) == "2.0"
    assert str(cal.get("CALSCALE")) == "GREGORIAN"
    assert str(cal.get("X-WR-CALNAME")) == "Apple Events"
    assert cal.get("REFRESH-INTERVAL") is not None
    assert "REFRESH-INTERVAL;VALUE=DURATION:PT12H" in build_calendar(_store(), _config()).decode()
    assert cal.get("X-PUBLISHED-TTL") is not None
    assert len(list(cal.walk("VTIMEZONE"))) >= 1


def test_upcoming_is_timed_with_tzid_and_valarm() -> None:
    cal = Calendar.from_ical(build_calendar(_store(), _config()))
    ve = _vevent_by_uid(cal, "wwdc26-official@apple.com")

    dtstart = ve.get("DTSTART").dt
    assert isinstance(dtstart, datetime)
    assert dtstart.tzinfo is not None
    assert str(ve.get("DTSTART").params.get("TZID")) == "America/Los_Angeles"

    alarms = list(ve.walk("VALARM"))
    assert len(alarms) == 1
    assert alarms[0].get("TRIGGER").to_ical() == b"-PT30M"


def test_past_events_are_all_day_without_valarm() -> None:
    cal = Calendar.from_ical(build_calendar(_store(), _config()))
    for uid in (
        "special-event-2025-09-09@acme.github.io",
        "special-event-2024-10-28@acme.github.io",
    ):
        ve = _vevent_by_uid(cal, uid)
        dtstart = ve.get("DTSTART").dt
        assert isinstance(dtstart, date) and not isinstance(dtstart, datetime)
        assert list(ve.walk("VALARM")) == []
        # exclusive end = start + 1 day (DM-3)
        assert (ve.get("DTEND").dt - dtstart).days == 1


def test_uid_fallback_and_passthrough() -> None:
    cal = Calendar.from_ical(build_calendar(_store(), _config()))
    uids = {str(ve.get("UID")) for ve in cal.walk("VEVENT")}
    # passthrough when event.uid present
    assert "wwdc26-official@apple.com" in uids
    # fallback <key>@<uid_domain> when uid empty
    assert "special-event-2024-10-28@acme.github.io" in uids


def test_validate_rejects_empty_calendar() -> None:
    empty = Calendar()
    empty.add("VERSION", "2.0")
    empty.add("PRODID", "-//test//EN")
    with pytest.raises(ValueError):
        validate_ics(empty.to_ical())


def test_validate_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        validate_ics(b"not a calendar")


def test_output_is_deterministic() -> None:
    assert build_calendar(_store(), _config()) == build_calendar(_store(), _config())


def test_write_feed_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "feed.ics"
    data = write_feed(path, _store(), _config())
    assert path.read_bytes() == data
    validate_ics(data)
