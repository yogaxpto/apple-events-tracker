"""DS-1 (HTML) + DS-2 (event.ics) parsing → normalized :class:`Event` objects.

This is the fragile part (§9): Apple rebuilds the page per event, so every selector has a
fallback and parsing is heuristic. All selectors / patterns live in :mod:`config`
(NFR-4). The page has two observed shapes:

* **between-cycles** — a hero block for the next event plus a "View recent Apple Events"
  list (see ``tests/fixtures/apple-events-with-recent.html``).
* **active-window** — during an announcement the page redirects to the live
  ``/event-stream/`` keynote page, which carries no recent list and no ``event.ics``
  (see ``tests/fixtures/apple-events-active-stream.html``). "No upcoming event" / "no
  recent list" is a valid state here, not an error (§3).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString, Tag

from . import config as cfg
from .config import RuntimeConfig
from .model import Event, derive_key

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


class StructureError(RuntimeError):
    """Raised when the page structure looks broken (RES-1): a recognizable section is
    present but no events could be extracted from it. The caller keeps last-known-good
    and fails the run loudly."""


@dataclass
class ParsedIcs:
    uid: str | None
    summary: str | None
    start: datetime | None
    end: datetime | None
    tzid: str | None


@dataclass
class ScrapeResult:
    """Outcome of parsing a single fetch of DS-1."""

    events: list[Event] = field(default_factory=list)
    hero_present: bool = False
    recent_section_present: bool = False
    recent_count: int = 0
    active_stream: bool = False


# ---------------------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------------------
def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


def _text(node: Tag | NavigableString | None) -> str:
    return node.get_text(" ", strip=True) if node is not None else ""


def _match_title(text: str) -> str | None:
    """Return the matched event title if ``text`` contains a known event-name pattern."""
    for pat in cfg.EVENT_NAME_PATTERNS:
        if pat.search(text):
            return text.strip()
    return None


def _find_long_date(text: str) -> date | None:
    m = cfg.LONG_DATE_PATTERN.search(text)
    if not m:
        return None
    month = _MONTHS[m.group(1).lower()]
    day = int(m.group(2))
    year = int(m.group(3))
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _first_selector(soup: BeautifulSoup, selectors: list[str]) -> Tag | None:
    for sel in selectors:
        try:
            node = soup.select_one(sel)
        except Exception:  # invalid selector under markup drift — skip
            continue
        if node is not None:
            return node
    return None


# ---------------------------------------------------------------------------------------
# DS-2: event.ics
# ---------------------------------------------------------------------------------------
def find_add_to_calendar_url(soup: BeautifulSoup, base_url: str) -> str | None:
    """Discover the DS-2 ``event.ics`` link by text or by ``.ics`` href (never hardcode
    the build-hash path)."""
    for a in soup.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        href = str(a["href"])
        label = a.get_text(" ", strip=True)
        if href.lower().endswith(".ics") or cfg.ADD_TO_CALENDAR_TEXT.search(label):
            return urljoin(base_url, href)
    return None


def parse_event_ics(ics_text: str) -> ParsedIcs:
    """Extract UID / SUMMARY / DTSTART / DTEND / TZID from the official single-event ics."""
    from icalendar import Calendar  # local import keeps module import light

    cal = Calendar.from_ical(ics_text)
    for comp in cal.walk("VEVENT"):
        start = comp.get("DTSTART")
        end = comp.get("DTEND")
        start_dt = getattr(start, "dt", None)
        end_dt = getattr(end, "dt", None)
        tzid: str | None = None
        if start is not None and "TZID" in getattr(start, "params", {}):
            tzid = str(start.params["TZID"])
        elif isinstance(start_dt, datetime) and start_dt.tzinfo is not None:
            tzid = getattr(start_dt.tzinfo, "key", None) or str(start_dt.tzinfo)
        uid = str(comp.get("UID")) if comp.get("UID") else None
        summary = str(comp.get("SUMMARY")) if comp.get("SUMMARY") else None
        return ParsedIcs(
            uid=uid,
            summary=summary,
            start=start_dt if isinstance(start_dt, datetime) else None,
            end=end_dt if isinstance(end_dt, datetime) else None,
            tzid=tzid,
        )
    return ParsedIcs(uid=None, summary=None, start=None, end=None, tzid=None)


# ---------------------------------------------------------------------------------------
# DS-1: HTML
# ---------------------------------------------------------------------------------------
def _looks_like_active_stream(soup: BeautifulSoup) -> bool:
    og = soup.find("meta", attrs={"property": "og:url"})
    if isinstance(og, Tag) and "event-stream" in str(og.get("content", "")).lower():
        return True
    title = _text(soup.find("title"))
    return "event stream" in title.lower()


def _find_recent_section(soup: BeautifulSoup) -> Tag | None:
    node = _first_selector(soup, cfg.RECENT_LIST_SELECTORS)
    if node is not None:
        return node
    # Fallback: a heading whose text mentions "recent apple events" → its container.
    for heading in soup.find_all(["h1", "h2", "h3"]):
        if isinstance(heading, Tag) and "recent apple events" in _text(heading).lower():
            return heading.find_parent(["section", "div", "main"]) or heading.parent
    return None


def _parse_hero(soup: BeautifulSoup, base_url: str) -> dict[str, object] | None:
    """Extract the upcoming/hero event from DS-1: title, date, watch + add-to-cal links."""
    hero = _first_selector(soup, cfg.HERO_SELECTORS)
    scope = hero if hero is not None else soup.find("main") or soup

    title: str | None = None
    for heading in scope.find_all(["h1", "h2"]) if isinstance(scope, Tag) else []:
        cand = _match_title(_text(heading))
        if cand:
            title = cand
            break
    if not title:
        return None

    block_text = _text(scope) if isinstance(scope, Tag) else ""
    event_date = _find_long_date(block_text)
    add_to_cal = find_add_to_calendar_url(soup, base_url)

    watch_url: str | None = None
    if isinstance(scope, Tag):
        for a in scope.find_all("a", href=True):
            if not isinstance(a, Tag):
                continue
            label = a.get_text(" ", strip=True).lower()
            href = str(a["href"])
            if href.lower().endswith(".ics"):
                continue
            if "watch" in label or "stream" in label or "watch" in href.lower():
                watch_url = urljoin(base_url, href)
                break

    return {
        "title": title,
        "date": event_date,
        "add_to_calendar": add_to_cal,
        "watch_url": watch_url,
        "present": True,
    }


def _parse_recent(soup: BeautifulSoup, base_url: str) -> tuple[list[dict[str, object]], bool]:
    """Parse the 'View recent Apple Events' list. Returns (items, section_present)."""
    section = _find_recent_section(soup)
    if section is None:
        return [], False

    items: list[dict[str, object]] = []
    candidates = section.find_all("li") or section.find_all(["article", "div"])
    for li in candidates:
        if not isinstance(li, Tag):
            continue
        heading = li.find(["h2", "h3", "h4"])
        raw_title = _text(heading) if heading else _text(li)
        title = _match_title(raw_title)
        event_date = _find_long_date(_text(li))
        if not title or event_date is None:
            continue
        watch_url: str | None = None
        for a in li.find_all("a", href=True):
            if isinstance(a, Tag):
                watch_url = urljoin(base_url, str(a["href"]))
                break
        # blurb: first paragraph that is not the date line
        blurb = ""
        for p in li.find_all("p"):
            ptext = _text(p)
            if ptext and _find_long_date(ptext) is None:
                blurb = ptext
                break
        items.append({"title": title, "date": event_date, "watch_url": watch_url, "blurb": blurb})
    return items, True


# ---------------------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------------------
def _iso(dt: datetime) -> str:
    return dt.isoformat()


def build_events(
    html: str,
    config: RuntimeConfig,
    *,
    now: datetime,
    ics_fetcher: Callable[[str], str] | None = None,
    base_url: str | None = None,
) -> ScrapeResult:
    """Parse DS-1 HTML into normalized events. ``ics_fetcher`` fetches DS-2 given a URL
    (injected for testability); when ``None``, the upcoming event is built from HTML
    alone. ``now`` is timezone-aware UTC and drives upcoming/past classification."""
    base = base_url or cfg.SOURCE_URL
    soup = _soup(html)
    result = ScrapeResult(active_stream=_looks_like_active_stream(soup))
    by_key: dict[str, Event] = {}

    # --- hero / upcoming ---------------------------------------------------------------
    hero = _parse_hero(soup, base)
    if hero:
        result.hero_present = True
        title = str(hero["title"])
        kind = cfg.classify_kind(title)
        add_to_cal = hero.get("add_to_calendar")
        parsed_ics: ParsedIcs | None = None
        if add_to_cal and ics_fetcher is not None:
            try:
                parsed_ics = parse_event_ics(ics_fetcher(str(add_to_cal)))
            except Exception:
                parsed_ics = None  # DS-2 best-effort; HTML date is the fallback

        if parsed_ics and parsed_ics.start is not None:
            # FR-4: DS-2 is authoritative when present.
            start_dt = parsed_ics.start
            end_dt = parsed_ics.end
            event_date = start_dt.date()
            status = "upcoming" if start_dt >= now else "past"
            ev = Event(
                key=derive_key(kind, event_date),
                uid=parsed_ics.uid or "",
                title=title,
                kind=kind,
                status=status,
                start=_iso(start_dt),
                end=_iso(end_dt) if end_dt else None,
                tzid=parsed_ics.tzid or cfg.CAL_TIMEZONE,
                all_day=False,
                description=_hero_description(hero),
                source_url=cfg.SOURCE_URL,
                watch_url=hero.get("watch_url") or None,  # type: ignore[arg-type]
            )
            by_key[ev.key] = ev
        elif hero.get("date") is not None:
            # No DS-2: keep the upcoming event as a date-only entry until the ics appears.
            event_date = hero["date"]  # type: ignore[assignment]
            assert isinstance(event_date, date)
            status = "upcoming" if event_date >= now.date() else "past"
            ev = Event(
                key=derive_key(kind, event_date),
                uid="",
                title=title,
                kind=kind,
                status=status,
                start=event_date.isoformat(),
                all_day=True,
                description=_hero_description(hero),
                source_url=cfg.SOURCE_URL,
                watch_url=hero.get("watch_url") or None,  # type: ignore[arg-type]
            )
            by_key[ev.key] = ev

    # --- recent / archive --------------------------------------------------------------
    recent_items, section_present = _parse_recent(soup, base)
    result.recent_section_present = section_present
    result.recent_count = len(recent_items)
    for item in recent_items:
        title = str(item["title"])
        kind = cfg.classify_kind(title)
        event_date = item["date"]  # type: ignore[assignment]
        assert isinstance(event_date, date)
        key = derive_key(kind, event_date)
        if key in by_key:  # hero already captured this event with richer data
            continue
        by_key[key] = Event(
            key=key,
            uid="",
            title=title,
            kind=kind,
            status="upcoming" if event_date >= now.date() else "past",
            start=event_date.isoformat(),
            all_day=True,
            description=str(item.get("blurb") or ""),
            source_url=cfg.SOURCE_URL,
            watch_url=item.get("watch_url") or None,  # type: ignore[arg-type]
        )

    # RES-1: a recognizable recent section that yields nothing is a structure break.
    if section_present and len(recent_items) == 0:
        raise StructureError(
            "recent-events section present but no events parsed — Apple may have "
            "changed the page markup"
        )

    result.events = list(by_key.values())
    return result


def _hero_description(hero: dict[str, object]) -> str:
    # The hero block rarely has a dedicated blurb element we can isolate reliably;
    # keep a short, stable description. Watch link is carried separately.
    return "Upcoming Apple event."
