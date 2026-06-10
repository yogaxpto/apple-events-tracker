"""Integration tests for the pipeline orchestrator (offline, via --from-file)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from apple_events_tracker import cli, config, ics
from apple_events_tracker.cli import run
from apple_events_tracker.fetch import FetchResult

REPO = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    data_dir = tmp_path / "data"
    docs_dir = tmp_path / "docs"
    data_dir.mkdir()
    shutil.copy(REPO / "data" / "seed_events.json", data_dir / "seed_events.json")
    return data_dir, docs_dir


def _args(data_dir: Path, docs_dir: Path, fixture: str) -> list[str]:
    return [
        "--from-file",
        str(FIXTURES / fixture),
        "--data-dir",
        str(data_dir),
        "--docs-dir",
        str(docs_dir),
        "--now",
        "2026-06-08T17:00:00Z",
    ]


def test_full_pipeline_writes_valid_artifacts(tmp_path: Path) -> None:
    data_dir, docs_dir = _setup(tmp_path)
    code = run(_args(data_dir, docs_dir, "apple-events-with-recent.html"))
    assert code == 0

    events = json.loads((data_dir / "events.json").read_text())
    assert events["schema_version"] == 1
    assert events["generated_at"] == "2026-06-08T17:00:00Z"
    keys = {e["key"] for e in events["events"]}
    assert "wwdc-2026-06-08" in keys
    assert len(events["events"]) > 30  # seed backfill merged in

    feed = (docs_dir / "feed.ics").read_bytes()
    ics.validate_ics(feed)  # RES-2: must be a valid calendar
    assert b"BEGIN:VEVENT" in feed

    index = (docs_dir / "index.html").read_text()
    assert "WWDC26" in index
    assert "Unofficial" in index
    assert (docs_dir / "assets" / "style.css").exists()


def test_rerun_is_idempotent_no_write(tmp_path: Path) -> None:
    data_dir, docs_dir = _setup(tmp_path)
    run(_args(data_dir, docs_dir, "apple-events-with-recent.html"))
    before = (data_dir / "events.json").read_bytes()
    code = run(_args(data_dir, docs_dir, "apple-events-with-recent.html"))
    assert code == 0
    assert (data_dir / "events.json").read_bytes() == before  # FR-8: no noisy rewrite


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    data_dir, docs_dir = _setup(tmp_path)
    code = run([*_args(data_dir, docs_dir, "apple-events-with-recent.html"), "--dry-run"])
    assert code == 0
    assert not (data_dir / "events.json").exists()
    assert not (docs_dir / "feed.ics").exists()


def _online_args(data_dir: Path, docs_dir: Path, now: str) -> list[str]:
    return ["--data-dir", str(data_dir), "--docs-dir", str(docs_dir), "--now", now]


def test_304_refreshes_status_without_scrape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 304 must still flip a passed event upcoming → past and republish (FR-1)."""
    data_dir, docs_dir = _setup(tmp_path)
    # Establish last-known-good with WWDC26 upcoming as of its date.
    run(_args(data_dir, docs_dir, "apple-events-with-recent.html"))
    before = json.loads((data_dir / "events.json").read_text())
    wwdc_before = next(e for e in before["events"] if e["key"] == "wwdc-2026-06-08")
    assert wwdc_before["status"] == "upcoming"

    # Apple's page is unchanged (304), but two days have passed.
    monkeypatch.setattr(
        cli,
        "fetch_conditional",
        lambda *a, **k: FetchResult(url=config.SOURCE_URL, status_code=304, text="", headers={}),
    )
    code = run(_online_args(data_dir, docs_dir, "2026-06-10T17:00:00Z"))
    assert code == 0

    after = json.loads((data_dir / "events.json").read_text())
    wwdc_after = next(e for e in after["events"] if e["key"] == "wwdc-2026-06-08")
    assert wwdc_after["status"] == "past"  # flipped without a fresh scrape
    assert wwdc_after["sequence"] == wwdc_before["sequence"] + 1
    assert after["generated_at"] == "2026-06-10T17:00:00Z"
    # Hero no longer headlines the passed event.
    index = (docs_dir / "index.html").read_text()
    assert "Nothing on the calendar" in index


def test_304_no_due_transitions_is_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A 304 with nothing newly past leaves the committed state untouched (FR-8)."""
    data_dir, docs_dir = _setup(tmp_path)
    run(_args(data_dir, docs_dir, "apple-events-with-recent.html"))
    before = (data_dir / "events.json").read_bytes()

    monkeypatch.setattr(
        cli,
        "fetch_conditional",
        lambda *a, **k: FetchResult(url=config.SOURCE_URL, status_code=304, text="", headers={}),
    )
    # Same instant as last-known-good: WWDC26 is still upcoming, no flip due.
    code = run(_online_args(data_dir, docs_dir, "2026-06-08T17:00:00Z"))
    assert code == 0
    assert (data_dir / "events.json").read_bytes() == before


def test_broken_page_keeps_last_known_good_and_fails(tmp_path: Path) -> None:
    data_dir, docs_dir = _setup(tmp_path)
    # First, a good run to establish last-known-good.
    run(_args(data_dir, docs_dir, "apple-events-with-recent.html"))
    good = (data_dir / "events.json").read_bytes()

    broken = tmp_path / "broken.html"
    broken.write_text(
        "<html><head><title>Apple Events - Apple</title></head><body><main>"
        '<section class="recent-events" aria-label="View recent Apple Events">'
        "<h2>View recent Apple Events</h2><ul><li>nope</li></ul></section></main></body></html>"
    )
    code = run(
        [
            "--from-file",
            str(broken),
            "--data-dir",
            str(data_dir),
            "--docs-dir",
            str(docs_dir),
            "--now",
            "2026-06-08T17:00:00Z",
        ]
    )
    assert code == 1  # RES-1: structure break fails loudly
    assert (data_dir / "events.json").read_bytes() == good  # last-known-good untouched
