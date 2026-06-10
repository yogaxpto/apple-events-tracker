"""Canonical data model + validation for ``data/events.json`` (§5).

An :class:`Event` is the normalized record for a single Apple special event. The
:class:`EventStore` is the on-disk container (schema_version, generated_at, events).

Dates are stored as ISO 8601 strings to preserve exact representation:
- timed events (upcoming): ``2026-06-08T10:00:00-07:00``
- all-day events (archived): ``2026-06-08`` (date only, ``all_day=True``)

Ordering is deterministic (NFR-5): events are always sorted by start date descending
(newest first) then key, so committed diffs are minimal and meaningful.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1

VALID_KINDS = {"wwdc", "special-event", "unknown"}
VALID_STATUSES = {"upcoming", "past"}

# Plausibility bounds for parsed dates (RES-3). Apple's first keynote-style events
# predate this, but the on-page archive never goes back that far.
MIN_PLAUSIBLE_YEAR = 2001
MAX_PLAUSIBLE_YEAR = 2100


class ValidationError(ValueError):
    """Raised when an event or store fails schema/plausibility validation (RES-3)."""


@dataclass
class Event:
    key: str
    title: str
    kind: str
    status: str
    start: str  # ISO date (all-day) or ISO datetime with offset (timed)
    all_day: bool
    source_url: str
    uid: str = ""
    end: str | None = None
    tzid: str | None = None
    description: str = ""
    watch_url: str | None = None
    sequence: int = 0
    first_seen: str | None = None
    last_changed: str | None = None

    # -- date helpers ---------------------------------------------------------------
    def start_date(self) -> date:
        """Calendar date of the event start (works for both all-day and timed)."""
        return (
            datetime.fromisoformat(self.start).date()
            if not self.all_day
            else date.fromisoformat(self.start)
        )

    def start_datetime(self) -> datetime:
        """Parsed start as a datetime (timed events only)."""
        if self.all_day:
            raise ValueError(f"event {self.key} is all-day; no start datetime")
        return datetime.fromisoformat(self.start)

    def end_datetime(self) -> datetime | None:
        if self.all_day or self.end is None:
            return None
        return datetime.fromisoformat(self.end)

    def end_date_exclusive(self) -> date:
        """Exclusive end date of an all-day event (the iCal ``DTEND`` convention).

        Uses the stored ``end`` when present (a multi-day span), else the day after the
        start — i.e. a single-day event. Lets a multi-day event count as current through
        its final day instead of flipping to 'past' the morning after it begins.
        """
        if self.end is not None:
            return date.fromisoformat(self.end)
        return self.start_date() + timedelta(days=1)

    # -- fields that participate in change detection (FR-7) -------------------------
    def comparable_fields(self) -> dict[str, Any]:
        """User-visible fields whose change marks an event 'changed' and bumps SEQUENCE."""
        return {
            "title": self.title,
            "start": self.start,
            "end": self.end,
            "tzid": self.tzid,
            "all_day": self.all_day,
            "description": self.description,
            "watch_url": self.watch_url,
            "status": self.status,
        }

    # -- (de)serialization ----------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        # Stable key order for minimal diffs (NFR-5).
        order = [
            "key",
            "uid",
            "title",
            "kind",
            "status",
            "start",
            "end",
            "tzid",
            "all_day",
            "description",
            "source_url",
            "watch_url",
            "sequence",
            "first_seen",
            "last_changed",
        ]
        raw = asdict(self)
        return {k: raw[k] for k in order}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Event:
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in d.items() if k in known})

    # -- validation -----------------------------------------------------------------
    def validate(self) -> None:
        if not self.key:
            raise ValidationError("event missing key")
        if not self.title:
            raise ValidationError(f"event {self.key} missing title")
        if self.kind not in VALID_KINDS:
            raise ValidationError(f"event {self.key} has invalid kind {self.kind!r}")
        if self.status not in VALID_STATUSES:
            raise ValidationError(f"event {self.key} has invalid status {self.status!r}")
        if not self.source_url:
            raise ValidationError(f"event {self.key} missing source_url")

        try:
            sd = self.start_date()
        except ValueError as exc:
            raise ValidationError(
                f"event {self.key} has unparseable start {self.start!r}: {exc}"
            ) from exc
        if not (MIN_PLAUSIBLE_YEAR <= sd.year <= MAX_PLAUSIBLE_YEAR):
            raise ValidationError(f"event {self.key} start year {sd.year} implausible")

        if self.all_day:
            if self.end is not None and self.end != self.start:
                # all-day events may carry an exclusive end date; tolerate but require parseable
                try:
                    date.fromisoformat(self.end)
                except ValueError as exc:
                    raise ValidationError(f"event {self.key} bad all-day end {self.end!r}") from exc
        else:
            try:
                self.start_datetime()
            except ValueError as exc:
                raise ValidationError(
                    f"event {self.key} timed start not a datetime: {exc}"
                ) from exc
            if self.end is not None:
                try:
                    datetime.fromisoformat(self.end)
                except ValueError as exc:
                    raise ValidationError(f"event {self.key} bad timed end {self.end!r}") from exc
        if self.sequence < 0:
            raise ValidationError(f"event {self.key} negative sequence")


@dataclass
class EventStore:
    events: list[Event] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    generated_at: str | None = None

    def by_key(self) -> dict[str, Event]:
        return {e.key: e for e in self.events}

    def sorted_events(self) -> list[Event]:
        """Deterministic order: newest start first, then key (NFR-5)."""
        return sorted(self.events, key=lambda e: (e.start_date(), e.key), reverse=True)

    def upcoming(self) -> list[Event]:
        return [e for e in self.sorted_events() if e.status == "upcoming"]

    def past(self) -> list[Event]:
        return [e for e in self.sorted_events() if e.status == "past"]

    def validate(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError(
                f"schema_version {self.schema_version} != supported {SCHEMA_VERSION}"
            )
        seen: set[str] = set()
        for e in self.events:
            e.validate()
            if e.key in seen:
                raise ValidationError(f"duplicate event key {e.key!r}")
            seen.add(e.key)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "events": [e.to_dict() for e in self.sorted_events()],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EventStore:
        return cls(
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            generated_at=d.get("generated_at"),
            events=[Event.from_dict(e) for e in d.get("events", [])],
        )


def derive_key(kind: str, event_date: date) -> str:
    """Stable diff identity (DM-1): ``<kind>-<YYYY-MM-DD>``."""
    return f"{kind}-{event_date.isoformat()}"


def load_store(path: str | Path) -> EventStore:
    """Load the last-known-good store; return an empty store if the file is absent."""
    p = Path(path)
    if not p.exists():
        return EventStore()
    data = json.loads(p.read_text(encoding="utf-8"))
    return EventStore.from_dict(data)


def save_store(path: str | Path, store: EventStore) -> None:
    """Write the store with deterministic ordering and a trailing newline."""
    store.validate()
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(store.to_dict(), indent=2, ensure_ascii=False, sort_keys=False)
    p.write_text(text + "\n", encoding="utf-8")
