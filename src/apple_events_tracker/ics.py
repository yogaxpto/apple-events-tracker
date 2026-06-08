"""iCalendar feed generation + validation (§6, CAL-1..CAL-6, OD-5).

Builds a single aggregate ``VCALENDAR`` from an :class:`EventStore`:

- upcoming events are *timed* with an explicit ``TZID`` and an embedded
  ``VTIMEZONE`` (CAL-4);
- archived events are *all-day* (``DTSTART;VALUE=DATE``, exclusive ``DTEND``)
  (CAL-4, DM-3);
- an opt-in 30-minute ``VALARM`` is attached to timed upcoming events only
  (CAL-5, OD-5).

Output is deterministic (NFR-5): ``DTSTAMP`` derives from ``store.generated_at``
rather than ``datetime.now()``, so building the same store twice yields identical
bytes. :func:`validate_ics` is the RES-2 publish gate — it round-trips the bytes
and rejects empty or malformed feeds.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from icalendar import Alarm, Calendar, Timezone
from icalendar import Event as ICalEvent

from . import config as cfg
from .config import RuntimeConfig
from .model import Event, EventStore

# Deterministic fallback when ``store.generated_at`` is None (NFR-5: never now()).
_FALLBACK_DTSTAMP = datetime(2001, 1, 1, 0, 0, 0, tzinfo=UTC)


def _parse_dtstamp(generated_at: str | None) -> datetime:
    """Parse the UTC ISO ``generated_at`` (e.g. ``2026-06-08T08:00:00Z``)."""
    if not generated_at:
        return _FALLBACK_DTSTAMP
    iso = generated_at.replace("Z", "+00:00")
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _uid_for(event: Event, config: RuntimeConfig) -> str:
    return event.uid if event.uid else f"{event.key}@{config.site.uid_domain}"


def _description_for(event: Event) -> str:
    parts = [event.description]
    if event.watch_url:
        parts.append(f"Watch: {event.watch_url}")
    parts.append("")
    parts.append(cfg.DISCLAIMER)
    return "\n".join(parts)


def _build_vevent(event: Event, config: RuntimeConfig, dtstamp: datetime) -> ICalEvent:
    vevent = ICalEvent()
    vevent.add("UID", _uid_for(event, config))
    vevent.add("DTSTAMP", dtstamp)
    vevent.add("SEQUENCE", event.sequence)
    vevent.add("SUMMARY", event.title)
    vevent.add("DESCRIPTION", _description_for(event))
    vevent.add("URL", event.watch_url or event.source_url)

    if event.all_day:
        start = event.start_date()
        vevent.add("DTSTART", start)
        vevent.add("DTEND", start + timedelta(days=1))
    else:
        # Re-anchor to the named zone (e.g. America/Los_Angeles) so DTSTART/DTEND
        # emit ``TZID=America/Los_Angeles`` linking to the embedded VTIMEZONE,
        # rather than an anonymous ``UTC-07:00`` fixed offset (CAL-4).
        tz = ZoneInfo(event.tzid) if event.tzid else None
        start = event.start_datetime()
        end = event.end_datetime()
        if end is None:
            end = start + timedelta(minutes=cfg.DEFAULT_EVENT_DURATION_MINUTES)
        if tz is not None:
            start = start.astimezone(tz)
            end = end.astimezone(tz)
        vevent.add("DTSTART", start)
        vevent.add("DTEND", end)

        if cfg.ENABLE_VALARM and event.status == "upcoming":
            alarm = Alarm()
            alarm.add("ACTION", "DISPLAY")
            alarm.add("DESCRIPTION", f"Reminder: {event.title}")
            alarm.add("TRIGGER", timedelta(minutes=-cfg.VALARM_MINUTES_BEFORE))
            vevent.add_component(alarm)

    return vevent


def build_calendar(store: EventStore, config: RuntimeConfig) -> bytes:
    """Serialize the aggregate ``VCALENDAR`` for ``store`` to iCalendar bytes."""
    cal = Calendar()
    cal.add("VERSION", "2.0")
    cal.add("PRODID", cfg.PRODID)
    cal.add("CALSCALE", "GREGORIAN")
    cal.add("METHOD", "PUBLISH")
    cal.add("X-WR-CALNAME", cfg.CAL_NAME)
    cal.add("X-WR-TIMEZONE", cfg.CAL_TIMEZONE)
    cal.add("X-WR-CALDESC", cfg.DISCLAIMER)
    cal.add("REFRESH-INTERVAL;VALUE=DURATION", cfg.REFRESH_INTERVAL)
    cal.add("X-PUBLISHED-TTL", cfg.REFRESH_INTERVAL)

    # Embed a VTIMEZONE for the calendar timezone so timed events resolve in
    # every client (CAL-4, CAL-6).
    vtimezone = Timezone.from_tzinfo(ZoneInfo(cfg.CAL_TIMEZONE), tzid=cfg.CAL_TIMEZONE)
    cal.add_component(vtimezone)

    dtstamp = _parse_dtstamp(store.generated_at)
    for event in store.sorted_events():
        cal.add_component(_build_vevent(event, config, dtstamp))

    data: bytes = cal.to_ical()
    return data


def validate_ics(data: bytes) -> None:
    """RES-2 publish gate: reject empty or malformed feeds (raise ``ValueError``)."""
    if not data:
        raise ValueError("empty iCalendar payload")
    try:
        cal = Calendar.from_ical(data)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a validation failure
        raise ValueError(f"feed does not parse as iCalendar: {exc}") from exc

    if cal.get("VERSION") is None:
        raise ValueError("VCALENDAR missing VERSION")
    if cal.get("PRODID") is None:
        raise ValueError("VCALENDAR missing PRODID")

    vevents = list(cal.walk("VEVENT"))
    if not vevents:
        raise ValueError("feed contains no VEVENTs")

    for vevent in vevents:
        for required in ("UID", "DTSTART", "DTSTAMP"):
            if vevent.get(required) is None:
                uid = vevent.get("UID", "<unknown>")
                raise ValueError(f"VEVENT {uid} missing {required}")


def write_feed(path: str | Path, store: EventStore, config: RuntimeConfig) -> bytes:
    """Build, validate (RES-2), then write the feed to ``path``; return the bytes."""
    data = build_calendar(store, config)
    validate_ics(data)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return data
