"""Fixture-based parser tests (RES-6) for DS-1 and DS-2.

Asserts the parser extracts the expected events from saved HTML snapshots and documents
the expected page structure. Refresh fixtures intentionally when Apple changes the page.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from apple_events_tracker import parse
from apple_events_tracker.config import RuntimeConfig
from apple_events_tracker.parse import (
    StructureError,
    build_events,
    find_add_to_calendar_url,
    parse_event_ics,
)

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 6, 8, 17, 0, tzinfo=UTC)


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _soup(name: str) -> BeautifulSoup:
    return BeautifulSoup(_read(name), "lxml")


# --- DS-2: event.ics -------------------------------------------------------------------
def test_parse_event_ics_extracts_authoritative_fields() -> None:
    parsed = parse_event_ics(_read("event.ics"))
    assert parsed.uid == "6E5B2F9C-WWDC26-KEYNOTE@apple.com"
    assert parsed.start is not None and parsed.start.hour == 10
    assert parsed.end is not None and parsed.end.hour == 12
    assert parsed.tzid == "America/Los_Angeles"


def test_find_add_to_calendar_url_resolves_relative_href() -> None:
    soup = _soup("apple-events-with-recent.html")
    url = find_add_to_calendar_url(soup, "https://www.apple.com/apple-events/")
    assert url is not None and url.endswith("/built/assets/event/event.ics")
    assert url.startswith("https://www.apple.com/")


# --- DS-1: between-cycles fixture ------------------------------------------------------
def test_recent_fixture_extracts_hero_and_recent() -> None:
    result = build_events(_read("apple-events-with-recent.html"), RuntimeConfig(), now=NOW)
    assert result.hero_present
    assert result.recent_section_present
    assert result.recent_count == 4
    keys = {e.key for e in result.events}
    assert "wwdc-2026-06-08" in keys  # hero / upcoming
    assert "wwdc-2025-06-09" in keys  # recent WWDC25
    assert "special-event-2025-09-09" in keys  # recent "Apple Event"

    upcoming = [e for e in result.events if e.status == "upcoming"]
    assert [e.title for e in upcoming] == ["WWDC26"]
    past_titles = {e.title for e in result.events if e.status == "past"}
    assert "WWDC25" in past_titles


def test_ds2_is_authoritative_when_ics_available() -> None:
    """When the .ics is fetchable, the upcoming event becomes timed with Apple's UID/TZID (FR-4)."""
    ics_text = _read("event.ics")
    result = build_events(
        _read("apple-events-with-recent.html"),
        RuntimeConfig(),
        now=NOW,
        ics_fetcher=lambda _url: ics_text,
    )
    upcoming = next(e for e in result.events if e.status == "upcoming")
    assert upcoming.all_day is False
    assert upcoming.tzid == "America/Los_Angeles"
    assert upcoming.uid == "6E5B2F9C-WWDC26-KEYNOTE@apple.com"
    assert upcoming.start_datetime().hour == 10


# --- DS-1: active-window fixture (the real saved page) ---------------------------------
def test_active_stream_fixture_is_valid_state_not_error() -> None:
    result = build_events(_read("apple-events-active-stream.html"), RuntimeConfig(), now=NOW)
    # No recent list during an active announcement window — a valid state, not a crash.
    assert result.active_stream is True
    assert result.recent_section_present is False


# --- defensive behaviour ---------------------------------------------------------------
def test_structure_error_when_recent_section_present_but_empty() -> None:
    broken = """
    <html><head><title>Apple Events - Apple</title></head><body><main>
      <section class="recent-events" aria-label="View recent Apple Events">
        <h2>View recent Apple Events</h2>
        <ul class="recent-events__list">
          <li class="recent-events__item"><span>totally different markup</span></li>
        </ul>
      </section>
    </main></body></html>
    """
    with pytest.raises(StructureError):
        build_events(broken, RuntimeConfig(), now=NOW)


def test_kind_classification() -> None:
    from apple_events_tracker.config import classify_kind

    assert classify_kind("WWDC26") == "wwdc"
    assert classify_kind("Apple Special Event") == "special-event"
    assert classify_kind("Apple Event") == "special-event"
    assert classify_kind("Random Marketing Page") == "unknown"


def test_no_match_at_all_yields_no_events_without_section() -> None:
    blank = "<html><head><title>Apple</title></head><body><main><p>hi</p></main></body></html>"
    result = build_events(blank, RuntimeConfig(), now=NOW)
    assert result.events == []
    assert result.recent_section_present is False
    assert isinstance(result, parse.ScrapeResult)
