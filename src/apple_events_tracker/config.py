"""Centralized configuration: repo identity, selectors, event-name patterns, tunables.

Per NFR-4, all parsing selectors and known event-name patterns live here so markup
drift (§9) can be handled in one place. Repo identity is read from the environment
(GitHub Actions sets ``GITHUB_REPOSITORY`` automatically) with an explicit placeholder
fallback for local runs — fill ``OWNER``/``REPO`` below before the first deploy if you
are not relying on the CI environment variable.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------------------
# Repo identity / public URLs
# --------------------------------------------------------------------------------------
# Repo identity defaults. CI overrides these from GITHUB_REPOSITORY, but they must hold
# the real owner/repo so local runs and `make preview` emit correct public URLs and stable
# event UIDs (DM-2) — a placeholder here silently bakes the wrong host into the feed.
_PLACEHOLDER_OWNER = "yogaxpto"
_PLACEHOLDER_REPO = "apple-events-tracker"


@dataclass(frozen=True)
class SiteConfig:
    """Public identity used in UIDs, links, and Pages URLs."""

    owner: str
    repo: str

    @property
    def pages_origin(self) -> str:
        return f"https://{self.owner.lower()}.github.io"

    @property
    def pages_base_url(self) -> str:
        """Base URL where the site + feed are served (no trailing slash)."""
        return f"{self.pages_origin}/{self.repo}"

    @property
    def feed_url(self) -> str:
        return f"{self.pages_base_url}/feed.ics"

    @property
    def webcal_url(self) -> str:
        return f"webcal://{self.owner.lower()}.github.io/{self.repo}/feed.ics"

    @property
    def repo_url(self) -> str:
        return f"https://github.com/{self.owner}/{self.repo}"

    @property
    def uid_domain(self) -> str:
        """Domain used to mint UIDs for events that lack an Apple-provided UID."""
        return f"{self.owner.lower()}.github.io"


def load_site_config() -> SiteConfig:
    """Resolve repo identity from ``GITHUB_REPOSITORY`` or the placeholders above."""
    gh = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if gh and "/" in gh:
        owner, repo = gh.split("/", 1)
        return SiteConfig(owner=owner, repo=repo)
    return SiteConfig(owner=_PLACEHOLDER_OWNER, repo=_PLACEHOLDER_REPO)


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------
SOURCE_URL = "https://www.apple.com/apple-events/"
USER_AGENT = "apple-events-tracker/0.1 (+https://github.com/{owner}/{repo})"
REQUEST_TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.5  # exponential: base ** attempt

# --------------------------------------------------------------------------------------
# Calendar / feed metadata
# --------------------------------------------------------------------------------------
PRODID = "-//Apple Events Tracker//EN"
CAL_NAME = "Apple Events"
CAL_TIMEZONE = "America/Los_Angeles"  # Apple states event times in Pacific Time
REFRESH_INTERVAL = "PT12H"

# OD-5: opt-in reminder, default 30 minutes before timed (upcoming) events.
ENABLE_VALARM = True
VALARM_MINUTES_BEFORE = 30

# Legal disclaimer used in the feed (CALDESC + event descriptions) and on the site. The
# "Unofficial tracker." label is added separately in the site template's visible markup —
# this constant is the affiliation/trademark notice only.
DISCLAIMER = (
    "Not affiliated with, authorized, or endorsed by Apple Inc. "
    '"Apple", "WWDC", and event names are trademarks of Apple Inc.'
)

# Default event duration when DS-2 does not supply a DTEND for an upcoming event.
DEFAULT_EVENT_DURATION_MINUTES = 120

# --------------------------------------------------------------------------------------
# Change detection
# --------------------------------------------------------------------------------------
# RES-1: if the freshly-parsed *recent* set shrinks by more than this many events vs
# last-known-good, treat it as a structure change and abort the write.
MAX_RECENT_SHRINKAGE = 1

# --------------------------------------------------------------------------------------
# Parsing — selectors & patterns (the fragile part, §9). Keep defensive: try several.
# --------------------------------------------------------------------------------------
# Known event-name patterns. The DS-1 contract lists titles like "Apple Event",
# "WWDC", "Apple Special Event", etc. Used both to recognise candidate titles in the
# recent-events list and to classify ``kind``.
EVENT_NAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bWWDC\s*'?\d{0,4}\b", re.IGNORECASE),
    re.compile(r"\bWorldwide Developers Conference\b", re.IGNORECASE),
    re.compile(r"\bApple Special Event\b", re.IGNORECASE),
    re.compile(r"\bSpecial Event\b", re.IGNORECASE),
    re.compile(r"\bApple Event\b", re.IGNORECASE),
    re.compile(r"\bApple Keynote\b", re.IGNORECASE),
    re.compile(r"\bKeynote\b", re.IGNORECASE),
]

# Generic event labels that carry no distinguishing year or codename. Apple's redesigned
# recent-events gallery (2026-06) prints only these bare labels in its ``.headline`` span
# ("Apple Event", "WWDC"), dropping the richer names the curated seed/archive holds
# ("WWDC 2024", "Apple Event — 'Let Loose'"). A scrape that yields one of these must never
# overwrite a richer title already on record — see :func:`prefer_richer_title`.
GENERIC_EVENT_TITLES: frozenset[str] = frozenset(
    {
        "apple event",
        "apple special event",
        "special event",
        "apple keynote",
        "apple event keynote",
        "keynote",
        "wwdc",
    }
)


def is_generic_title(title: str) -> bool:
    """True if ``title`` is a bare event label with no year/codename to distinguish it."""
    return title.strip().lower() in GENERIC_EVENT_TITLES


def _normalize_title(title: str) -> str:
    """Lowercase + collapse whitespace for specificity comparisons."""
    return " ".join(title.split()).lower()


def prefer_richer_title(stored: str, scraped: str) -> str:
    """Choose the title to keep when a scrape re-reports a known event.

    A *less specific* scrape must not downgrade a richer stored title; the curated or
    previously-seen name wins. Two signals mark the scrape as less specific:

    * it is one of the bare :data:`GENERIC_EVENT_TITLES` ("Apple Event", "WWDC") while
      the stored title is not, or
    * its normalized form is a shorter substring of the stored title ("WWDC" within
      "WWDC 2024", "Apple Event" within "Apple Event — 'Let Loose'"). This specificity
      fallback also catches bare labels we have not enumerated.

    Any other scraped title wins, so Apple legitimately renaming or refining an event
    (one specific title to a *different* specific title) is still picked up.
    """
    if not stored:
        return scraped
    norm_stored = _normalize_title(stored)
    norm_scraped = _normalize_title(scraped)
    if norm_scraped == norm_stored:
        return scraped
    less_specific = (is_generic_title(scraped) and not is_generic_title(stored)) or (
        norm_scraped in norm_stored and len(norm_scraped) < len(norm_stored)
    )
    return stored if less_specific else scraped


# Long-form date, e.g. "September 9, 2025" (comma optional, day optional for some copy).
LONG_DATE_PATTERN = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:,)?\s+(\d{4})\b",
    re.IGNORECASE,
)

# Month + day with no year, e.g. "September 9" (year inferred relative to scrape time).
# The lookahead keeps it from half-matching a long-form date that carries its year.
MONTH_DAY_PATTERN = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})\b(?!\s*,?\s*\d{4})",
    re.IGNORECASE,
)

# Numeric month/day like "9/9", optionally "9/9/2026". Apple's 2026-08 hero copy reads
# "Watch a special Apple Event on 9/9 at 10 a.m. PT." with no long-form date anywhere.
NUMERIC_DATE_PATTERN = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{4}))?\b")

# Time-of-day like "10 a.m. PT" / "10:00 a.m. PDT" (DS-2 TZID is still preferred).
TIME_PATTERN = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.m\.|p\.m\.|am|pm)\b",
    re.IGNORECASE,
)

# Candidate container selectors for the "View recent Apple Events" list. Tried in order;
# parsing falls back to a generic heuristic if none match (markup drift resilience).
# The first group matches the live markup as of 2026-06 (a `section-recent-events`
# section wrapping a `recent-events-gallery` carousel); the rest are older/synthetic
# shapes kept as fallbacks.
RECENT_LIST_SELECTORS: list[str] = [
    "section.section-recent-events",
    "[data-component-list='RecentEventsGallery']",
    ".recent-events-gallery",
    "ul.recent-events-gallery-list",
    "section.recent-events",
    '[data-analytics-section="recent-events"]',
    "section[aria-label*='recent' i]",
    "ul.recent-events__list",
]

# Candidate selectors for the hero / upcoming-event block. The first group matches the
# live markup as of 2026-06 (a `section-hero` section); the rest are older/synthetic
# shapes kept as fallbacks.
HERO_SELECTORS: list[str] = [
    "section.section-hero",
    "[data-analytics-section-engagement='name:hero']",
    "section.hero",
    "[data-analytics-section='hero']",
    "section[aria-label*='upcoming' i]",
    "header.hero",
]

# Selectors / link text that indicate the "Add to calendar" (.ics) link (DS-2).
# The path itself contains a build hash and must be discovered, never hardcoded.
ADD_TO_CALENDAR_TEXT = re.compile(r"add to calendar|add to your calendar", re.IGNORECASE)

# A published "Watch" link must point to a human-watchable *page*. Apple's recent-event
# "Watch" buttons are in-page modal players (``films-modal``) whose ``href`` is a raw HLS
# stream manifest (``events-delivery.apple.com/.../vod_index-*.m3u8``); the ``.ics``
# "Add to calendar" link is not a watch target either. A browser navigated straight to
# any of these renders a blank/empty page, so they must never become a watch link.
NON_NAVIGABLE_WATCH_URL = re.compile(
    r"\.m3u8?(?:$|\?)|\.ics(?:$|\?)|events-delivery\.apple\.com",
    re.IGNORECASE,
)


def is_navigable_watch_url(href: str) -> bool:
    """True if ``href`` is a real page we can publish as a 'Watch' link — not a raw media
    stream manifest or the ``.ics`` calendar file."""
    return bool(href) and NON_NAVIGABLE_WATCH_URL.search(href) is None


@dataclass(frozen=True)
class KindRule:
    kind: str
    pattern: re.Pattern[str]


KIND_RULES: list[KindRule] = [
    KindRule("wwdc", re.compile(r"\bWWDC|Worldwide Developers", re.IGNORECASE)),
    KindRule("special-event", re.compile(r"\bSpecial Event\b", re.IGNORECASE)),
    KindRule("special-event", re.compile(r"\bApple Event\b|\bKeynote\b", re.IGNORECASE)),
]


def classify_kind(title: str) -> str:
    """Map a title to a canonical ``kind`` (wwdc | special-event | unknown)."""
    for rule in KIND_RULES:
        if rule.pattern.search(title):
            return rule.kind
    return "unknown"


@dataclass
class RuntimeConfig:
    """Aggregate config object passed through the pipeline."""

    site: SiteConfig = field(default_factory=load_site_config)

    @property
    def user_agent(self) -> str:
        return USER_AGENT.format(owner=self.site.owner, repo=self.site.repo)
