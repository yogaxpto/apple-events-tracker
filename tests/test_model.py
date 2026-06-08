"""Tests for the canonical data model + validation (§5)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from apple_events_tracker.model import (
    Event,
    EventStore,
    ValidationError,
    derive_key,
    load_store,
    save_store,
)


def _timed() -> Event:
    return Event(
        key="wwdc-2026-06-08",
        title="WWDC26",
        kind="wwdc",
        status="upcoming",
        start="2026-06-08T10:00:00-07:00",
        end="2026-06-08T12:00:00-07:00",
        tzid="America/Los_Angeles",
        all_day=False,
        source_url="https://www.apple.com/apple-events/",
        uid="x@apple.com",
    )


def _allday(day: str = "2024-06-10", kind: str = "wwdc") -> Event:
    return Event(
        key=derive_key(kind, date.fromisoformat(day)),
        title="WWDC 2024",
        kind=kind,
        status="past",
        start=day,
        all_day=True,
        source_url="https://www.apple.com/apple-events/",
    )


def test_derive_key_is_stable() -> None:
    assert derive_key("wwdc", date(2026, 6, 8)) == "wwdc-2026-06-08"


def test_timed_event_parsing_helpers() -> None:
    e = _timed()
    assert e.start_date() == date(2026, 6, 8)
    assert e.start_datetime().hour == 10
    assert e.end_datetime() is not None


def test_allday_event_has_no_start_datetime() -> None:
    with pytest.raises(ValueError):
        _allday().start_datetime()


def test_validation_rejects_bad_kind_and_status() -> None:
    e = _timed()
    e.kind = "bogus"
    with pytest.raises(ValidationError):
        e.validate()
    e2 = _timed()
    e2.status = "later"
    with pytest.raises(ValidationError):
        e2.validate()


def test_validation_rejects_implausible_year() -> None:
    e = _allday("1800-01-01")
    with pytest.raises(ValidationError):
        e.validate()


def test_store_rejects_duplicate_keys() -> None:
    store = EventStore(events=[_allday(), _allday()])
    with pytest.raises(ValidationError):
        store.validate()


def test_store_sorting_is_newest_first() -> None:
    store = EventStore(events=[_allday("2020-06-22"), _timed(), _allday("2024-06-10")])
    keys = [e.key for e in store.sorted_events()]
    assert keys[0] == "wwdc-2026-06-08"  # newest
    assert keys[-1] == "wwdc-2020-06-22"  # oldest


def test_roundtrip_load_save(tmp_path: Path) -> None:
    store = EventStore(events=[_timed(), _allday()], generated_at="2026-06-08T08:00:00Z")
    path = tmp_path / "events.json"
    save_store(path, store)
    loaded = load_store(path)
    assert loaded.schema_version == 1
    assert {e.key for e in loaded.events} == {"wwdc-2026-06-08", "wwdc-2024-06-10"}
    # to_dict key order is stable for minimal diffs
    assert list(loaded.events[0].to_dict().keys())[0] == "key"


def test_load_missing_file_returns_empty_store(tmp_path: Path) -> None:
    assert load_store(tmp_path / "nope.json").events == []
