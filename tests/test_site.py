"""Tests for the static website renderer (site.render_site)."""

from __future__ import annotations

from pathlib import Path

from markupsafe import escape

from apple_events_tracker import config as config_module
from apple_events_tracker.config import RuntimeConfig
from apple_events_tracker.model import Event, EventStore
from apple_events_tracker.site import render_site

GENERATED_AT = "2026-06-08T08:00:00Z"


def _upcoming_event() -> Event:
    return Event(
        key="wwdc-2026-06-08",
        title="WWDC26",
        kind="wwdc",
        status="upcoming",
        start="2026-06-08T10:00:00-07:00",
        all_day=False,
        source_url=config_module.SOURCE_URL,
        end="2026-06-08T12:00:00-07:00",
        tzid="America/Los_Angeles",
        description="Watch online today at 10 a.m. PT.",
        watch_url="https://www.apple.com/apple-events/stream/",
    )


def _past_events() -> list[Event]:
    return [
        Event(
            key="special-event-2025-09-09",
            title="Apple Special Event (Sept 2025)",
            kind="special-event",
            status="past",
            start="2025-09-09",
            all_day=True,
            source_url=config_module.SOURCE_URL,
            description="iPhone announcement.",
        ),
        Event(
            key="wwdc-2025-06-09",
            title="WWDC25",
            kind="wwdc",
            status="past",
            start="2025-06-09",
            all_day=True,
            source_url=config_module.SOURCE_URL,
            description="Developer keynote.",
        ),
    ]


def _full_store() -> EventStore:
    return EventStore(events=[_upcoming_event(), *_past_events()])


def test_render_site_contains_expected_content(tmp_path: Path) -> None:
    store = _full_store()
    config = RuntimeConfig()
    html = render_site(store, config, GENERATED_AT, out_dir=tmp_path)

    # Upcoming + past titles.
    assert "WWDC26" in html
    assert "Apple Special Event (Sept 2025)" in html

    # Disclaimer text (FR-15, §12). Autoescaping (mandatory) escapes the quotes.
    assert str(escape(config_module.DISCLAIMER)) in html

    # Subscribe affordances (FR-13).
    assert config.site.webcal_url in html
    assert config.site.feed_url in html
    assert "calendar.google.com/calendar/r?cid=" in html

    # Source link + last-updated (FR-14).
    assert config_module.SOURCE_URL in html
    assert "Last updated" in html
    assert GENERATED_AT in html

    # Files written to disk.
    index = tmp_path / "index.html"
    assert index.exists()
    assert index.read_text(encoding="utf-8") == html
    assert (tmp_path / "assets" / "style.css").exists()


def test_render_site_no_upcoming_message(tmp_path: Path) -> None:
    store = EventStore(events=_past_events())
    html = render_site(store, RuntimeConfig(), GENERATED_AT, out_dir=tmp_path)

    assert "No upcoming Apple event announced right now" in html
    # Past events still render.
    assert "WWDC25" in html
    assert (tmp_path / "index.html").exists()


def test_render_site_emits_social_preview_tags(tmp_path: Path) -> None:
    config = RuntimeConfig()
    html = render_site(_full_store(), config, GENERATED_AT, out_dir=tmp_path)

    base = config.site.pages_base_url
    # Open Graph + Twitter cards reflect the next upcoming event.
    assert 'property="og:title" content="WWDC26 — June 8' in html
    assert f'property="og:url" content="{base}/"' in html
    assert 'name="twitter:card" content="summary_large_image"' in html
    # Absolute, cache-busted image URL (crawlers don't resolve relative paths).
    assert f'property="og:image" content="{base}/assets/og.png?v=' in html
    assert 'property="og:image:width" content="1200"' in html

    # The referenced card is a real 1200x630 PNG.
    from PIL import Image

    card = tmp_path / "assets" / "og.png"
    assert card.exists()
    with Image.open(card) as im:
        assert im.size == (1200, 630)


def test_render_site_social_tags_fallback_without_upcoming(tmp_path: Path) -> None:
    html = render_site(
        EventStore(events=_past_events()), RuntimeConfig(), GENERATED_AT, out_dir=tmp_path
    )

    assert 'property="og:title" content="Apple Events Tracker"' in html
    assert (tmp_path / "assets" / "og.png").exists()
