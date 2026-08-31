"""Pipeline orchestrator (§2 architecture diagram).

fetch (conditional) → parse → fetch DS-2 → diff vs last-known-good → if changed & valid:
write events.json, regenerate feed.ics, rebuild site, notify. Git commit/push and Pages
deploy are handled by the GitHub Actions workflow, not this CLI.

Exit codes: 0 = success (whether or not anything changed); 1 = structure/parse break or
fetch failure (last-known-good is left untouched, a tracking issue is filed — RES-1/4/5).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

from . import config as cfg
from . import diff, ics, notify, parse, site
from .config import RuntimeConfig
from .fetch import FetchError, HttpCache, fetch_conditional, fetch_text
from .model import Event, EventStore, load_store, save_store

log = logging.getLogger("apple_events_tracker")


def _parse_now(value: str | None) -> datetime:
    if value:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return datetime.now(tz=UTC)


def _load_baseline(events_file: Path, seed_file: Path) -> tuple[EventStore, set[str]]:
    """Last-known-good baseline and the set of seed-origin keys (for notify filtering)."""
    baseline = load_store(events_file)
    seed_keys: set[str] = set()
    if seed_file.exists():
        seed = load_store(seed_file)
        seed_keys = set(seed.by_key())
        # Seed the baseline on first run so historical events are recorded once.
        existing = baseline.by_key()
        for ev in seed.events:
            existing.setdefault(ev.key, ev)
        baseline = EventStore(events=list(existing.values()))
    return baseline, seed_keys


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="apple-events-tracker", description=__doc__)
    p.add_argument("--data-dir", default="data")
    p.add_argument("--docs-dir", default="docs")
    p.add_argument("--source-url", default=cfg.SOURCE_URL)
    p.add_argument("--from-file", help="Parse a local HTML file instead of fetching (offline).")
    p.add_argument("--no-conditional", action="store_true", help="Ignore the HTTP cache.")
    p.add_argument("--no-fetch-ics", action="store_true", help="Skip DS-2 event.ics fetch.")
    p.add_argument("--now", help="Override 'now' (ISO-8601) for deterministic runs/tests.")
    p.add_argument("--dry-run", action="store_true", help="Compute and report; write nothing.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def run(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = RuntimeConfig()
    now = _parse_now(args.now)
    data_dir = Path(args.data_dir)
    docs_dir = Path(args.docs_dir)
    events_file = data_dir / "events.json"
    seed_file = data_dir / "seed_events.json"
    cache_file = data_dir / "http_cache.json"
    feed_file = docs_dir / "feed.ics"
    index_file = docs_dir / "index.html"

    # --- 1. acquire DS-1 ---------------------------------------------------------------
    offline = bool(args.from_file)
    try:
        if offline:
            html = Path(args.from_file).read_text(encoding="utf-8")
            base_url = args.source_url
        else:
            cache = HttpCache.load(cache_file)
            fetched = fetch_conditional(args.source_url, cache, config)
            if fetched.not_modified:
                # FR-1: Apple's page is unchanged, but time keeps moving — an event that
                # has now happened must still flip upcoming → past even with no fresh HTML.
                # Recompute status from last-known-good and rebuild only if that changes
                # something, so the published page stays correct between full scrapes
                # instead of advertising a past event as "upcoming".
                log.info("304 Not Modified — refreshing time-based status from last-known-good.")
                return _refresh_status(
                    args, now, config, events_file, seed_file, feed_file, index_file, docs_dir
                )
            html = fetched.text
            base_url = fetched.url
            if not args.dry_run:
                cache.save()
    except FetchError as exc:
        log.error("fetch failed: %s", exc)
        notify.report_failure(f"Fetch failed: {exc}", config)
        return 1

    # --- 2/3. parse DS-1 (+ DS-2) ------------------------------------------------------
    ics_fetcher = None if (args.no_fetch_ics or offline) else (lambda u: fetch_text(u, config))
    try:
        scrape = parse.build_events(
            html, config, now=now, ics_fetcher=ics_fetcher, base_url=base_url
        )
    except parse.StructureError as exc:
        log.error("structure error: %s", exc)
        notify.report_failure(str(exc), config)
        return 1

    log.info(
        "parsed: %d event(s) | hero=%s recent_section=%s recent=%d active_stream=%s",
        len(scrape.events),
        scrape.hero_present,
        scrape.recent_section_present,
        scrape.recent_count,
        scrape.active_stream,
    )

    # RES-1: a page that matches nothing we recognise is a structure break.
    if (
        not scrape.events
        and not scrape.active_stream
        and not scrape.hero_present
        and not scrape.recent_section_present
    ):
        msg = "DS-1 matched no hero, no recent section, and no events — page likely changed."
        log.error(msg)
        notify.report_failure(msg, config)
        return 1

    # --- 4. diff vs last-known-good ----------------------------------------------------
    baseline, seed_keys = _load_baseline(events_file, seed_file)
    seed = load_store(seed_file).events if seed_file.exists() else []
    combined = _combine(scrape.events, seed)
    result = diff.classify(baseline, combined, now)

    for e in result.new:
        log.info("NEW      %s  %s", e.key, e.title)
    for e in result.changed:
        log.info("CHANGED  %s  %s (seq=%d)", e.key, e.title, e.sequence)
    log.info(
        "classification: %d new, %d changed, %d unchanged",
        len(result.new),
        len(result.changed),
        len(result.unchanged),
    )

    bootstrap = not (events_file.exists() and feed_file.exists() and index_file.exists())
    stale = site.output_is_stale(index_file)
    if not result.has_changes and not bootstrap and not stale:
        log.info("no new or changed events — no write, no commit (FR-8).")
        return 0
    if stale and not result.has_changes and not bootstrap:
        log.info("template/renderer changed since last publish — republishing site.")
    if args.dry_run:
        log.info(
            "dry-run: %d changes pending; writing nothing.", len(result.new) + len(result.changed)
        )
        return 0

    # --- 5/6. validate, publish, notify -----------------------------------------------
    return _publish(result, config, now, events_file, feed_file, index_file, docs_dir, seed_keys)


def _publish(
    result: diff.DiffResult,
    config: RuntimeConfig,
    now: datetime,
    events_file: Path,
    feed_file: Path,
    index_file: Path,
    docs_dir: Path,
    seed_keys: set[str],
) -> int:
    """Validate the merged store, then write events.json/feed.ics/site and notify (RES-2)."""
    generated_at = diff.iso_utc(now)
    merged = result.merged
    merged.generated_at = generated_at
    try:
        merged.validate()  # RES-3
        cal_bytes = ics.build_calendar(merged, config)
        ics.validate_ics(cal_bytes)  # RES-2 publish gate
    except Exception as exc:
        log.error("validation failed, keeping last-known-good: %s", exc)
        notify.report_failure(f"Validation failed: {exc}", config)
        return 1

    save_store(events_file, merged)
    feed_file.parent.mkdir(parents=True, exist_ok=True)
    feed_file.write_bytes(cal_bytes)
    site.render_site(merged, config, generated_at, out_dir=docs_dir)
    log.info("wrote %s, %s, and %s", events_file, feed_file, index_file)

    # Notify on genuinely new *upcoming* events (FR-18).
    new_upcoming = [e for e in result.new if e.status == "upcoming" and e.key not in seed_keys]
    notify.notify_new_events(new_upcoming, config)
    return 0


def _refresh_status(
    args: argparse.Namespace,
    now: datetime,
    config: RuntimeConfig,
    events_file: Path,
    seed_file: Path,
    feed_file: Path,
    index_file: Path,
    docs_dir: Path,
) -> int:
    """Recompute time-based status from last-known-good without a fresh scrape (304 path).

    Carries every known event forward, flipping upcoming → past for any whose date has
    passed, and republishes only if that produced a change. Keeps the static page correct
    between full scrapes — Apple's page rarely changes, so most runs land here.
    """
    baseline, seed_keys = _load_baseline(events_file, seed_file)
    result = diff.classify(baseline, [], now)

    for e in result.changed:
        log.info("CHANGED  %s  %s → %s (seq=%d)", e.key, e.title, e.status, e.sequence)

    bootstrap = not (events_file.exists() and feed_file.exists() and index_file.exists())
    stale = site.output_is_stale(index_file)
    if not result.has_changes and not bootstrap and not stale:
        log.info("no status transitions due — no write, no commit (FR-8).")
        return 0
    if stale and not result.has_changes and not bootstrap:
        log.info("template/renderer changed since last publish — republishing site.")
    if args.dry_run:
        log.info("dry-run: %d status change(s) pending; writing nothing.", len(result.changed))
        return 0
    return _publish(result, config, now, events_file, feed_file, index_file, docs_dir, seed_keys)


def _combine(scraped: list[Event], seed: list[Event]) -> list[Event]:
    """Union scraped + seed events, preferring richer scraped data on key conflicts."""
    by_key: dict[str, Event] = {e.key: e for e in seed}
    for e in scraped:  # scraped wins
        by_key[e.key] = e
    return list(by_key.values())


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
