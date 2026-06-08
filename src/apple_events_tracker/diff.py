"""Change detection / classification (FR-6, FR-7, FR-8, DM-4).

Merges a fresh scrape into the last-known-good store **without ever deleting** known
events (seed + previously-seen). Each scraped event is classified new / changed /
unchanged; ``sequence`` is bumped on change, and ``first_seen`` / ``last_changed`` are
maintained. Status is recomputed against ``now`` so events that have happened flip
upcoming → past even on a run that scrapes nothing new.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from .model import Event, EventStore


def iso_utc(now: datetime) -> str:
    """Render ``now`` as a ``...Z`` UTC timestamp (seconds precision, deterministic)."""
    return now.astimezone(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _recompute_status(event: Event, now: datetime) -> str:
    if event.all_day:
        return "upcoming" if event.start_date() >= now.date() else "past"
    return "upcoming" if event.start_datetime() >= now else "past"


@dataclass
class DiffResult:
    new: list[Event] = field(default_factory=list)
    changed: list[Event] = field(default_factory=list)
    unchanged: list[Event] = field(default_factory=list)
    merged: EventStore = field(default_factory=EventStore)

    @property
    def has_changes(self) -> bool:
        return bool(self.new or self.changed)


def classify(old: EventStore, scraped: list[Event], now: datetime) -> DiffResult:
    """Classify ``scraped`` against ``old`` and produce the merged store."""
    old_by_key = old.by_key()
    scraped_by_key = {e.key: e for e in scraped}
    result = DiffResult()
    merged: dict[str, Event] = {}

    # 1) Walk the fresh scrape: new / changed / unchanged.
    for key, fresh in scraped_by_key.items():
        fresh.status = _recompute_status(fresh, now)
        prev = old_by_key.get(key)
        if prev is None:
            fresh.first_seen = iso_utc(now)
            fresh.last_changed = iso_utc(now)
            fresh.sequence = 0
            merged[key] = fresh
            result.new.append(fresh)
            continue

        # Preserve a previously-known Apple UID if this scrape lacks one (DM-2 stability).
        if not fresh.uid and prev.uid:
            fresh.uid = prev.uid
        fresh.first_seen = prev.first_seen or iso_utc(now)

        if fresh.comparable_fields() != prev.comparable_fields():
            fresh.sequence = prev.sequence + 1  # DM-4
            fresh.last_changed = iso_utc(now)
            merged[key] = fresh
            result.changed.append(fresh)
        else:
            # Identical — keep the stored record verbatim (stable sequence/timestamps).
            merged[key] = prev
            result.unchanged.append(prev)

    # 2) Carry forward known events absent from this scrape (never delete; seed/archive).
    for key, prev in old_by_key.items():
        if key in merged:
            continue
        new_status = _recompute_status(prev, now)
        if new_status != prev.status:
            # An upcoming event that has now happened: a real, user-visible change.
            prev.status = new_status
            prev.sequence += 1
            prev.last_changed = iso_utc(now)
            result.changed.append(prev)
        merged[key] = prev

    result.merged = EventStore(events=list(merged.values()))
    return result
