"""Tests for change detection / classification (FR-6, FR-7, FR-8, DM-4)."""

from __future__ import annotations

from datetime import UTC, datetime

from apple_events_tracker.diff import classify, iso_utc
from apple_events_tracker.model import Event, EventStore

NOW = datetime(2026, 6, 8, 17, 0, tzinfo=UTC)


def _ev(key: str, title: str, start: str, *, all_day: bool = True, status: str = "past") -> Event:
    return Event(
        key=key,
        title=title,
        kind="special-event",
        status=status,
        start=start,
        all_day=all_day,
        source_url="https://www.apple.com/apple-events/",
    )


def test_new_event_is_classified_new_with_timestamps() -> None:
    fresh = _ev("special-event-2025-09-09", "Apple Event", "2025-09-09")
    result = classify(EventStore(), [fresh], NOW)
    assert [e.key for e in result.new] == ["special-event-2025-09-09"]
    assert result.has_changes
    assert result.new[0].first_seen == iso_utc(NOW)
    assert result.new[0].sequence == 0


def test_unchanged_event_keeps_sequence_and_timestamps() -> None:
    stored = _ev("special-event-2025-09-09", "Apple Event", "2025-09-09")
    stored.sequence = 3
    stored.first_seen = "2025-01-01T00:00:00Z"
    stored.last_changed = "2025-09-09T00:00:00Z"
    fresh = _ev("special-event-2025-09-09", "Apple Event", "2025-09-09")
    result = classify(EventStore(events=[stored]), [fresh], NOW)
    assert not result.has_changes
    assert result.unchanged[0].sequence == 3
    assert result.unchanged[0].last_changed == "2025-09-09T00:00:00Z"


def test_changed_event_bumps_sequence_and_preserves_first_seen() -> None:
    stored = _ev("special-event-2025-09-09", "Apple Event", "2025-09-09")
    stored.sequence = 1
    stored.first_seen = "2025-01-01T00:00:00Z"
    fresh = _ev("special-event-2025-09-09", "Apple Event — Awe dropping", "2025-09-09")
    result = classify(EventStore(events=[stored]), [fresh], NOW)
    assert [e.key for e in result.changed] == ["special-event-2025-09-09"]
    assert result.changed[0].sequence == 2  # DM-4
    assert result.changed[0].first_seen == "2025-01-01T00:00:00Z"
    assert result.changed[0].last_changed == iso_utc(NOW)


def test_generic_scrape_does_not_downgrade_richer_known_title() -> None:
    # Apple's redesigned gallery prints only "WWDC" / "Apple Event"; the curated archive
    # holds "WWDC 2024". A re-scrape must keep the richer title and stay unchanged (no
    # phantom CHANGED, no SEQUENCE bump, no feed degradation).
    stored = _ev("wwdc-2024-06-10", "WWDC 2024", "2024-06-10")
    stored.sequence = 2
    stored.first_seen = "2024-01-01T00:00:00Z"
    fresh = _ev("wwdc-2024-06-10", "WWDC", "2024-06-10")
    result = classify(EventStore(events=[stored]), [fresh], NOW)
    assert not result.has_changes
    merged = result.merged.by_key()["wwdc-2024-06-10"]
    assert merged.title == "WWDC 2024"
    assert merged.sequence == 2


def test_unenumerated_less_specific_scrape_is_blocked_by_specificity_fallback() -> None:
    # A bare label that isn't in GENERIC_EVENT_TITLES is still caught by the substring
    # specificity check, so an unforeseen generic label can't downgrade a richer title.
    stored = _ev("special-event-2014-09-09", "Apple Developer Conference 2014", "2014-09-09")
    fresh = _ev("special-event-2014-09-09", "Apple Developer Conference", "2014-09-09")
    result = classify(EventStore(events=[stored]), [fresh], NOW)
    assert not result.has_changes
    title = result.merged.by_key()["special-event-2014-09-09"].title
    assert title == "Apple Developer Conference 2014"


def test_specific_rename_still_wins_over_known_title() -> None:
    # A genuine rename to another *specific* title is a real change and must be applied.
    stored = _ev("special-event-2025-09-09", "Apple Event", "2025-09-09")
    fresh = _ev("special-event-2025-09-09", "Apple Event — Awe Dropping", "2025-09-09")
    result = classify(EventStore(events=[stored]), [fresh], NOW)
    assert [e.key for e in result.changed] == ["special-event-2025-09-09"]
    assert result.merged.by_key()["special-event-2025-09-09"].title == "Apple Event — Awe Dropping"


def test_known_events_are_never_deleted() -> None:
    stored = _ev("special-event-2020-01-01", "Old Event", "2020-01-01")
    # fresh scrape doesn't include it; it must survive in the merge.
    result = classify(EventStore(events=[stored]), [], NOW)
    assert "special-event-2020-01-01" in result.merged.by_key()


def test_uid_preserved_when_scrape_loses_it() -> None:
    stored = _ev(
        "wwdc-2026-06-08", "WWDC26", "2026-06-08T10:00:00-07:00", all_day=False, status="upcoming"
    )
    stored.uid = "apple-uid@apple.com"
    fresh = _ev(
        "wwdc-2026-06-08", "WWDC26", "2026-06-08T10:00:00-07:00", all_day=False, status="upcoming"
    )  # no uid
    result = classify(EventStore(events=[stored]), [fresh], NOW)
    assert result.merged.by_key()["wwdc-2026-06-08"].uid == "apple-uid@apple.com"


def test_status_flips_to_past_when_event_has_happened() -> None:
    stored = _ev("wwdc-2025-06-09", "WWDC25", "2025-06-09", status="upcoming")
    # not in the fresh scrape; now is well after the date → should flip to past + bump seq
    result = classify(EventStore(events=[stored]), [], NOW)
    flipped = result.merged.by_key()["wwdc-2025-06-09"]
    assert flipped.status == "past"
    assert flipped.sequence == 1
    assert [e.key for e in result.changed] == ["wwdc-2025-06-09"]


def _status_at(event: Event, now: datetime) -> str:
    """Classify a single carried-forward event at ``now`` and read back its status."""
    return classify(EventStore(events=[event]), [], now).merged.by_key()[event.key].status


def test_multiday_all_day_event_stays_upcoming_through_its_span() -> None:
    # WWDC runs Mon-Fri: start Mon Jun 8, exclusive end Sat Jun 13 (the iCal DTEND).
    wwdc = _ev("wwdc-2026-06-08", "WWDC26", "2026-06-08", status="upcoming")
    wwdc.end = "2026-06-13"
    # Mid-conference (Wed Jun 10) it is still on — must not drop to past on day two.
    assert _status_at(wwdc, datetime(2026, 6, 10, 17, 0, tzinfo=UTC)) == "upcoming"
    # On the last day (Fri Jun 12) it is still current.
    assert _status_at(wwdc, datetime(2026, 6, 12, 23, 0, tzinfo=UTC)) == "upcoming"
    # The morning after it ends (Sat Jun 13) it is finally past.
    assert _status_at(wwdc, datetime(2026, 6, 13, 0, 0, tzinfo=UTC)) == "past"


def test_single_day_all_day_classification_is_unchanged() -> None:
    # No end → exclusive end is the next day, matching the old start-only behavior.
    ev = _ev("special-event-2026-06-08", "Apple Event", "2026-06-08", status="upcoming")
    assert _status_at(ev, datetime(2026, 6, 8, 23, 0, tzinfo=UTC)) == "upcoming"  # event day
    assert _status_at(ev, datetime(2026, 6, 9, 0, 0, tzinfo=UTC)) == "past"  # day after


def test_timed_event_in_progress_counts_as_upcoming() -> None:
    # 10:00-12:00 PT keynote. At 11:00 PT it is live, not yet past.
    ev = _ev(
        "special-event-2026-09-09",
        "Apple Event",
        "2026-09-09T10:00:00-07:00",
        all_day=False,
        status="upcoming",
    )
    ev.end = "2026-09-09T12:00:00-07:00"
    assert _status_at(ev, datetime(2026, 9, 9, 18, 0, tzinfo=UTC)) == "upcoming"  # 11:00 PT
    assert _status_at(ev, datetime(2026, 9, 9, 19, 30, tzinfo=UTC)) == "past"  # 12:30 PT


def test_timed_event_without_end_uses_default_duration() -> None:
    # No end → upcoming for DEFAULT_EVENT_DURATION_MINUTES (120) past the start, so a
    # just-started keynote isn't shown as over the instant it begins.
    ev = _ev(
        "special-event-2026-09-09",
        "Apple Event",
        "2026-09-09T10:00:00-07:00",
        all_day=False,
        status="upcoming",
    )
    assert _status_at(ev, datetime(2026, 9, 9, 18, 0, tzinfo=UTC)) == "upcoming"  # 11:00 PT
    assert _status_at(ev, datetime(2026, 9, 9, 19, 30, tzinfo=UTC)) == "past"  # 12:30 PT
