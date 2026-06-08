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
# CONFIGURE ME: replace these placeholders, or rely on GITHUB_REPOSITORY in CI.
_PLACEHOLDER_OWNER = "OWNER"
_PLACEHOLDER_REPO = "REPO"


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
USER_AGENT = "apple-events-tracker/0.1 (+https://github.com/{owner}/{repo}; unofficial calendar)"
REQUEST_TIMEOUT_SECONDS = 20.0
MAX_RETRIES = 4
BACKOFF_BASE_SECONDS = 1.5  # exponential: base ** attempt

# --------------------------------------------------------------------------------------
# Calendar / feed metadata
# --------------------------------------------------------------------------------------
PRODID = "-//Apple Events Tracker (Unofficial)//EN"
CAL_NAME = "Apple Events (Unofficial)"
CAL_TIMEZONE = "America/Los_Angeles"  # Apple states event times in Pacific Time
REFRESH_INTERVAL = "PT12H"

# OD-5: opt-in reminder, default 30 minutes before timed (upcoming) events.
ENABLE_VALARM = True
VALARM_MINUTES_BEFORE = 30

DISCLAIMER = (
    "Unofficial tracker. Not affiliated with, authorized, or endorsed by Apple Inc. "
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

# Long-form date, e.g. "September 9, 2025" (comma optional, day optional for some copy).
LONG_DATE_PATTERN = re.compile(
    r"\b(January|February|March|April|May|June|July|August|September|October|November|December)"
    r"\s+(\d{1,2})(?:,)?\s+(\d{4})\b",
    re.IGNORECASE,
)

# Time-of-day like "10 a.m. PT" / "10:00 a.m. PDT" (DS-2 TZID is still preferred).
TIME_PATTERN = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.m\.|p\.m\.|am|pm)\b",
    re.IGNORECASE,
)

# Candidate container selectors for the "View recent Apple Events" list. Tried in order;
# parsing falls back to a generic heuristic if none match (markup drift resilience).
RECENT_LIST_SELECTORS: list[str] = [
    "section.recent-events",
    '[data-analytics-section="recent-events"]',
    "section[aria-label*='recent' i]",
    "ul.recent-events__list",
]

# Candidate selectors for the hero / upcoming-event block.
HERO_SELECTORS: list[str] = [
    "section.hero",
    "[data-analytics-section='hero']",
    "section[aria-label*='upcoming' i]",
    "header.hero",
]

# Selectors / link text that indicate the "Add to calendar" (.ics) link (DS-2).
# The path itself contains a build hash and must be discovered, never hardcoded.
ADD_TO_CALENDAR_TEXT = re.compile(r"add to calendar|add to your calendar", re.IGNORECASE)


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
