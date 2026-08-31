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


# --- DS-1: live recent-events gallery (real saved page, 2026-06) -----------------------
def test_recent_gallery_fixture_extracts_real_events() -> None:
    """Real snapshot of the live page: a ``section-recent-events`` carousel whose items
    nest title/date as ``.headline``/``.subhead`` spans, plus a post-event ``section-hero``
    recap with no date/.ics. The recap must NOT be emitted as an event, and it must not
    borrow a date from the gallery (regression for the markup drift that broke parsing)."""
    result = build_events(_read("apple-events-recent-gallery.html"), RuntimeConfig(), now=NOW)
    assert result.recent_section_present
    assert result.recent_count == 6
    assert result.hero_present  # the WWDC26 recap heading is present...
    # ...but yields no event (no date, no add-to-calendar link).
    assert {e.key for e in result.events} == {
        "special-event-2025-09-09",
        "wwdc-2025-06-09",
        "special-event-2024-09-09",
        "wwdc-2024-06-10",
        "special-event-2024-05-07",
        "special-event-2023-10-30",
    }
    # Every parsed event carries its own gallery date — none borrowed from elsewhere.
    assert all(e.status == "past" for e in result.events)
    assert all(e.title in {"Apple Event", "WWDC"} for e in result.events)
    # The gallery's "Watch" buttons are in-page modal players whose href is a raw HLS
    # stream manifest (events-delivery.apple.com/...m3u8). Those are not navigable pages,
    # so none may be published as a watch link (regression: they rendered as empty pages).
    assert all(e.watch_url is None for e in result.events)


# --- DS-1: tagline hero (real saved page, 2026-08) -------------------------------------
SEPT_NOW = datetime(2026, 8, 31, 12, 0, tzinfo=UTC)


def test_tagline_hero_fixture_detects_event_via_ds2() -> None:
    """Regression: this page parsed as hero_present=False and no event, so CI reported
    "no changes" daily while the Sept 2026 event sat on the page."""
    # Given the real 2026-08 page snapshot — the hero heading is a bare marketing
    # tagline ("Surprise and shine.") and the event name/date appear only in body copy
    # ("Watch a special Apple Event on 9/9 at 10 a.m. PT.") — and a fetchable DS-2 ics
    html = _read("apple-events-hero-tagline.html")
    ics_text = _read("event-sept-2026.ics")

    # When the page is parsed with the DS-2 fetcher available
    result = build_events(html, RuntimeConfig(), now=SEPT_NOW, ics_fetcher=lambda _url: ics_text)

    # Then the hero is recognized and one upcoming event is built from DS-2's
    # authoritative UID/DTSTART/TZID, described by the hero tagline
    assert result.hero_present
    upcoming = [e for e in result.events if e.status == "upcoming"]
    assert len(upcoming) == 1
    ev = upcoming[0]
    assert ev.key == "special-event-2026-09-09"
    assert ev.title == "Apple Event"
    assert ev.all_day is False
    assert ev.start_datetime().hour == 10
    assert ev.tzid == "America/Los_Angeles"
    assert ev.uid == "7F2C9A14-3B6D-4E58-9C1A-0D9E2F6A8B30"
    assert ev.description == "Surprise and shine."


def test_tagline_hero_fixture_detects_event_without_ds2() -> None:
    # Given the same tagline-hero snapshot but no DS-2 fetcher (offline / ics failure)
    html = _read("apple-events-hero-tagline.html")

    # When the page is parsed from HTML alone
    result = build_events(html, RuntimeConfig(), now=SEPT_NOW)

    # Then the title is recovered from the hero body copy and the date from "9/9" with
    # the year inferred from ``now`` — a date-only upcoming event until the ics appears
    upcoming = [e for e in result.events if e.status == "upcoming"]
    assert len(upcoming) == 1
    ev = upcoming[0]
    assert ev.key == "special-event-2026-09-09"
    assert ev.all_day is True
    assert ev.start == "2026-09-09"


def test_structure_error_when_ics_link_present_but_no_event_extracted() -> None:
    # Given a page carrying an "Add to calendar" .ics link — proof an event is
    # scheduled — whose hero yields no recognizable title or date
    broken = """
    <html><head><title>Apple Events - Apple</title></head><body><main>
      <section class="section-hero">
        <h2>Mystery.</h2>
        <a href="/v/apple-events/home/xx/built/assets/event/event.ics">Add to calendar</a>
      </section>
    </main></body></html>
    """

    # When the page is parsed without a reachable DS-2, then the run fails loudly
    # instead of reporting "no changes"
    with pytest.raises(StructureError):
        build_events(broken, RuntimeConfig(), now=SEPT_NOW)


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
