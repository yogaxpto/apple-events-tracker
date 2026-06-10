"""Static website renderer (FR-12..FR-15, §7).

Renders ``templates/index.html.j2`` to ``<out_dir>/index.html`` at build time from an
:class:`~apple_events_tracker.model.EventStore`, so the page works without JavaScript
(WEB-1). The page links a self-hosted stylesheet only — no third-party trackers, cookies,
fonts, or CDNs (WEB-4/WEB-5).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import config as config_module
from . import og
from .config import RuntimeConfig
from .model import Event, EventStore

# Locate project resources relative to this file, never hardcoding /workspace.
_PACKAGE_ROOT = Path(__file__).parent.parent.parent
_TEMPLATES_DIR = _PACKAGE_ROOT / "templates"
_DEFAULT_STYLE_SRC = _PACKAGE_ROOT / "docs" / "assets" / "style.css"

_MONTHS = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)


def _format_event_date(event: Event) -> str:
    """Human-readable date in Pacific Time copy.

    All-day (archived) events show the date only; timed (upcoming) events show
    date + time + " PT" (§7, the source states times in Pacific Time).
    """
    sd = event.start_date()
    base = f"{_MONTHS[sd.month - 1]} {sd.day}, {sd.year}"
    if event.all_day:
        return base
    dt = event.start_datetime()
    minute = f":{dt.minute:02d}" if dt.minute else ""
    suffix = "a.m." if dt.hour < 12 else "p.m."
    hour12 = dt.hour % 12 or 12
    return f"{base} at {hour12}{minute} {suffix} PT"


def _og_copy(store: EventStore) -> dict[str, str]:
    """Social-preview copy derived from the next upcoming event (or a fallback).

    ``card_title``/``card_subtitle`` feed the rendered og.png; ``og_title``/
    ``og_description`` feed the ``<meta>`` tags.
    """
    upcoming = store.upcoming()
    if upcoming:
        event = upcoming[0]
        date_str = _format_event_date(event)
        return {
            "card_title": event.title,
            "card_subtitle": date_str,
            "og_title": f"{event.title} — {date_str}",
            "og_description": (
                f"Next Apple event: {event.title} on {date_str}. "
                "Subscribe once to the calendar feed and never miss a keynote."
            ),
        }
    return {
        "card_title": "Apple Events Tracker",
        "card_subtitle": "Tracking Apple's special events",
        "og_title": "Apple Events Tracker",
        "og_description": (
            "Auto-updating tracker for Apple special events. "
            "Subscribe once to a calendar feed and never miss a keynote."
        ),
    }


def _build_context(
    store: EventStore,
    config: RuntimeConfig,
    generated_at: str,
) -> dict[str, Any]:
    feed_url = config.site.feed_url
    google_calendar_url = f"https://calendar.google.com/calendar/r?cid={quote(feed_url, safe='')}"
    base_url = config.site.pages_base_url
    og_copy = _og_copy(store)
    # Cache-bust the image URL so Facebook/iMessage re-fetch when the page rebuilds
    # (rebuilds only happen on a real change), instead of serving a stale card.
    cache_key = quote(generated_at, safe="") or "v1"
    return {
        "upcoming": store.upcoming(),
        "past": store.past(),
        "generated_at": generated_at,
        "feed_url": feed_url,
        "webcal_url": config.site.webcal_url,
        "repo_url": config.site.repo_url,
        "google_calendar_url": google_calendar_url,
        "source_url": config_module.SOURCE_URL,
        "disclaimer": config_module.DISCLAIMER,
        "page_url": f"{base_url}/",
        "og_title": og_copy["og_title"],
        "og_description": og_copy["og_description"],
        "og_image_url": f"{base_url}/assets/og.png?v={cache_key}",
    }


def render_site(
    store: EventStore,
    config: RuntimeConfig,
    generated_at: str,
    out_dir: str | Path = "docs",
) -> str:
    """Render the static site to ``<out_dir>/index.html`` and return the HTML string.

    Ensures ``<out_dir>/assets/style.css`` exists, copying the bundled stylesheet when
    rendering to a directory other than the default ``docs`` (whose stylesheet is the
    canonical source and is left untouched).
    """
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["event_date"] = _format_event_date

    template = env.get_template("index.html.j2")
    html = template.render(**_build_context(store, config, generated_at))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(html, encoding="utf-8")

    # Social link-preview card (FB / iMessage / Twitter) referenced by the og:image tag.
    og_copy = _og_copy(store)
    og.render_og_image(out, title=og_copy["card_title"], subtitle=og_copy["card_subtitle"])

    assets = out / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    style_dst = assets / "style.css"
    if not style_dst.exists() and _DEFAULT_STYLE_SRC.exists() and style_dst != _DEFAULT_STYLE_SRC:
        shutil.copyfile(_DEFAULT_STYLE_SRC, style_dst)

    return html
