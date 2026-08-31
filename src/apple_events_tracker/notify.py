"""Run notifications (FR-18, NOTE-1, RES-5).

Two channels, both dependency-free and best-effort:

* :func:`notify_new_events` announces newly-detected events as a note on the CI run
  (job summary + annotation). New events need no human action — subscribers get them
  through the calendar feed — so they don't warrant an issue.
* :func:`report_failure` opens **or updates a single** tracking issue when a run fails
  (RES-5): failures do need a human, and the single issue alerts without inbox spam.

:func:`report_failure` no-ops (with a log line) when ``GITHUB_TOKEN`` is absent — e.g.
local runs. Secrets are never logged or written to committed files (NOTE-3).
"""

from __future__ import annotations

import logging
import os

import httpx

from .config import RuntimeConfig
from .model import Event

log = logging.getLogger(__name__)

FAILURE_LABEL = "scrape-failing"
FAILURE_TITLE = "Scrape/parse failing — Apple may have changed the page"


def _api_base() -> str:
    return os.environ.get("GITHUB_API_URL", "https://api.github.com")


def _repo_slug(config: RuntimeConfig) -> str:
    return os.environ.get("GITHUB_REPOSITORY", "") or f"{config.site.owner}/{config.site.repo}"


def _token() -> str | None:
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")


def _client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=_api_base(),
        timeout=20.0,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )


def notify_new_events(new_events: list[Event], config: RuntimeConfig) -> None:
    """Announce newly-detected events as a note on the CI run (FR-18).

    Appends a Markdown section to the Actions job summary and emits a ``::notice``
    annotation so the announcement shows on the run's front page. Outside Actions
    (no ``GITHUB_STEP_SUMMARY``) the log line is the whole announcement.
    """
    if not new_events:
        return
    headline = (
        f"New Apple event detected: {new_events[0].title}"
        if len(new_events) == 1
        else f"{len(new_events)} new Apple events detected"
    )
    for e in new_events:
        log.info("new event: %s — %s (%s)", e.title, e.start, e.kind)

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    lines = [f"- **{e.title}** — {e.start} ({e.kind})" for e in new_events]
    summary = (
        f"### {headline}\n\n" + "\n".join(lines) + "\n\n"
        f"Feed: {config.site.feed_url}\nSite: {config.site.pages_base_url}/\n"
    )
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write(summary)
    # Workflow commands are parsed from stdout, not the logging stream (stderr).
    details = "; ".join(f"{e.title} on {e.start}" for e in new_events)
    print(f"::notice title={headline}::{details}")


def report_failure(message: str, config: RuntimeConfig) -> None:
    """Open or update the single failure tracking issue (RES-5). No-op without a token."""
    token = _token()
    if not token:
        log.info("no GITHUB_TOKEN; skipping failure issue. reason: %s", message)
        return
    slug = _repo_slug(config)
    try:
        with _client(token) as client:
            existing = client.get(
                f"/repos/{slug}/issues",
                params={"state": "open", "labels": FAILURE_LABEL, "per_page": "1"},
            )
            existing.raise_for_status()
            items = existing.json()
            if items:
                number = items[0]["number"]
                client.post(
                    f"/repos/{slug}/issues/{number}/comments",
                    json={"body": f"Run failed again:\n\n```\n{message}\n```"},
                ).raise_for_status()
                log.info("updated failure issue #%s", number)
            else:
                resp = client.post(
                    f"/repos/{slug}/issues",
                    json={
                        "title": FAILURE_TITLE,
                        "body": (
                            "An automated run failed. The published feed/site keep the "
                            "last-known-good state.\n\n```\n" + message + "\n```"
                        ),
                        "labels": [FAILURE_LABEL],
                    },
                )
                resp.raise_for_status()
                log.info("opened failure issue #%s", resp.json().get("number"))
    except httpx.HTTPError as exc:
        log.warning("failed to file failure issue: %s", exc)
