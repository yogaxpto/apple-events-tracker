"""Tests for the static website renderer (site.render_site)."""

from __future__ import annotations

import json
import re
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


def test_past_events_rendered_from_single_json_island(tmp_path: Path) -> None:
    """The archive is delivered once as a JSON data island and rendered client-side.

    petite-vue renders the cards with v-for from this island, so no cards are
    server-rendered, and the island carries display-ready fields so the client
    never re-implements formatting.
    """
    html = render_site(_full_store(), RuntimeConfig(), GENERATED_AT, out_dir=tmp_path)

    match = re.search(
        r'<script type="application/json" id="aet-past-events">(.*?)</script>',
        html,
        re.S,
    )
    assert match is not None
    events = json.loads(match.group(1))

    # Both past events present, upcoming excluded (it lives in the hero, not the archive).
    assert [e["title"] for e in events] == ["Apple Special Event (Sept 2025)", "WWDC25"]
    assert all(e["title"] != "WWDC26" for e in events)

    # Display-ready shape: the server formats the date once, the client just places it.
    first = events[0]
    assert first["kind_label"] == "Special Event"
    assert first["date_display"] == "September 9, 2025"

    # Cards are not server-rendered — the v-for template is the only past-card markup.
    assert html.count('class="card past-card"') == 1
    assert 'v-for="ev in past"' in html
    assert 'v-scope="{ past: pastEvents }"' in html


def test_render_site_loads_petite_vue_with_integrity(tmp_path: Path) -> None:
    """The reactive runtime is a pinned, SRI-pinned CDN script (supply-chain safety)."""
    html = render_site(_full_store(), RuntimeConfig(), GENERATED_AT, out_dir=tmp_path)

    assert "petite-vue@0.4.1/dist/petite-vue.iife.js" in html
    assert 'integrity="sha384-' in html
    assert 'crossorigin="anonymous"' in html
    # No-JS visitors still get a route to the data.
    assert "<noscript>" in html
    assert "subscribe to the calendar feed" in html


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


def test_render_site_declares_and_copies_favicons(tmp_path: Path) -> None:
    """Icon <link>s are declared (relative, so they resolve under a /repo/ subpath) and
    the static icon files are copied alongside the page."""
    html = render_site(_full_store(), RuntimeConfig(), GENERATED_AT, out_dir=tmp_path)

    assert '<link rel="icon" href="favicon.svg" type="image/svg+xml" />' in html
    assert '<link rel="apple-touch-icon" href="apple-touch-icon.png" />' in html

    for name in ("favicon.svg", "favicon.ico", "apple-touch-icon.png"):
        assert (tmp_path / name).exists(), name


def test_render_site_links_and_copies_web_manifest(tmp_path: Path) -> None:
    """The PWA manifest is linked and copied, with relative icon/start paths so install
    works from the /repo/ subpath."""
    html = render_site(_full_store(), RuntimeConfig(), GENERATED_AT, out_dir=tmp_path)

    assert '<link rel="manifest" href="site.webmanifest" />' in html

    manifest_path = tmp_path / "site.webmanifest"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["start_url"] == "." and manifest["scope"] == "."
    icon_srcs = {icon["src"] for icon in manifest["icons"]}
    assert {"assets/icon-192.png", "assets/icon-512.png"} <= icon_srcs
    for icon in manifest["icons"]:
        if icon["src"].endswith(".png"):
            assert (tmp_path / icon["src"]).exists(), icon["src"]


def test_render_site_emits_event_json_ld(tmp_path: Path) -> None:
    """A schema.org @graph with an Event node for the next keynote (rich-result eligible)."""
    html = render_site(_full_store(), RuntimeConfig(), GENERATED_AT, out_dir=tmp_path)

    match = re.search(
        r'<script type="application/ld\+json">(.*?)</script>', html, re.S
    )
    assert match is not None
    data = json.loads(match.group(1))
    assert data["@context"] == "https://schema.org"
    types = {node["@type"] for node in data["@graph"]}
    assert {"WebSite", "Event"} <= types

    event = next(n for n in data["@graph"] if n["@type"] == "Event")
    assert event["name"] == "WWDC26"
    assert event["startDate"] == "2026-06-08T10:00:00-07:00"
    assert event["endDate"] == "2026-06-08T12:00:00-07:00"
    assert event["location"]["@type"] == "VirtualLocation"


def test_render_site_links_feed_for_autodiscovery(tmp_path: Path) -> None:
    config = RuntimeConfig()
    html = render_site(_full_store(), config, GENERATED_AT, out_dir=tmp_path)
    assert (
        f'<link rel="alternate" type="text/calendar" href="{config.site.feed_url}"' in html
    )


def test_render_site_writes_robots_and_sitemap(tmp_path: Path) -> None:
    import xml.etree.ElementTree as ET

    config = RuntimeConfig()
    render_site(_full_store(), config, GENERATED_AT, out_dir=tmp_path)

    robots = (tmp_path / "robots.txt").read_text(encoding="utf-8")
    assert "User-agent: *" in robots
    assert f"Sitemap: {config.site.pages_base_url}/sitemap.xml" in robots

    sitemap = tmp_path / "sitemap.xml"
    root = ET.fromstring(sitemap.read_text(encoding="utf-8"))  # valid XML
    ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
    locs = [el.text for el in root.iter(f"{ns}loc")]
    assert locs == [f"{config.site.pages_base_url}/"]
    lastmod = root.find(f"{ns}url/{ns}lastmod")
    assert lastmod is not None and lastmod.text == GENERATED_AT


def test_render_site_writes_404_with_absolute_assets(tmp_path: Path) -> None:
    """The 404 page uses absolute asset/home URLs (it's served at arbitrary depths) and
    is marked noindex."""
    config = RuntimeConfig()
    render_site(_full_store(), config, GENERATED_AT, out_dir=tmp_path)

    page = (tmp_path / "404.html").read_text(encoding="utf-8")
    base = config.site.pages_base_url
    assert '<meta name="robots" content="noindex" />' in page
    assert f'href="{base}/assets/style.css"' in page
    assert f'href="{base}/"' in page


def test_render_site_emits_generator_and_copies_nojekyll(tmp_path: Path) -> None:
    html = render_site(_full_store(), RuntimeConfig(), GENERATED_AT, out_dir=tmp_path)
    assert '<meta name="generator" content="apple-events-tracker" />' in html
    assert (tmp_path / ".nojekyll").exists()
