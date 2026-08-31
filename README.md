# Apple Events Tracker

An automatically-updating tracker for Apple special events. A scheduled
GitHub Action scrapes <https://www.apple.com/apple-events/>, keeps a structured record in
git, and publishes:

- a **subscribable iCalendar feed** — `feed.ics` (subscribe once, forget), and
- a **static website** listing the upcoming event and a historical archive.

There is no server and no backend — git holds the canonical last-known-good state, and a
failed or suspicious scrape never overwrites it, so the published feed never breaks or
empties.

> **Disclaimer.** This project is **not affiliated with, authorized, or
> endorsed by Apple Inc.** "Apple", "WWDC", and event names are trademarks of Apple Inc.
> This tracker only collects publicly available scheduling metadata and links out to
> Apple's media rather than copying it.

---

## Subscribe

> Replace `OWNER`/`REPO` below with your GitHub identity (see **Configure** — the values
> are derived automatically in CI from `GITHUB_REPOSITORY`).

- **Calendar app (recommended):** `webcal://OWNER.github.io/REPO/feed.ics`
- **Raw feed URL:** `https://OWNER.github.io/REPO/feed.ics`
- **Website:** `https://OWNER.github.io/REPO/`

The website's subscribe panel offers one-tap `webcal://`, "Add to Google Calendar", and a
raw `.ics` download.

---

## How it works

```
cron / manual ─▶ GitHub Actions
                  1. fetch apple-events page (conditional, 304-aware)
                  2. parse → candidate events (hero + "recent" list)
                  3. fetch the official event.ics for the upcoming event
                  4. diff vs data/events.json (last-known-good)
                  5. if changed & valid: write events.json, regenerate feed.ics,
                     rebuild docs/, commit + push, notify on new events
                  └─▶ GitHub Pages deploy
```

| Stage | Module |
|-------|--------|
| Conditional HTTP + retry/backoff + ETag cache | [`src/apple_events_tracker/fetch.py`](src/apple_events_tracker/fetch.py) |
| DS-1 (HTML) + DS-2 (`event.ics`) parsing — the fragile part | [`src/apple_events_tracker/parse.py`](src/apple_events_tracker/parse.py) |
| Canonical data model + validation | [`src/apple_events_tracker/model.py`](src/apple_events_tracker/model.py) |
| Change detection / classification (new / changed / unchanged) | [`src/apple_events_tracker/diff.py`](src/apple_events_tracker/diff.py) |
| iCalendar feed generation + validation | [`src/apple_events_tracker/ics.py`](src/apple_events_tracker/ics.py) |
| Static site rendering (Jinja2) | [`src/apple_events_tracker/site.py`](src/apple_events_tracker/site.py) |
| Run notifications (CI notes + failure issues) | [`src/apple_events_tracker/notify.py`](src/apple_events_tracker/notify.py) |
| Pipeline orchestrator (CLI) | [`src/apple_events_tracker/cli.py`](src/apple_events_tracker/cli.py) |
| Centralized config (selectors, event-name patterns, repo identity) | [`src/apple_events_tracker/config.py`](src/apple_events_tracker/config.py) |

Published artifacts (committed to git, served by Pages):

- [`data/events.json`](data/events.json) — canonical last-known-good state.
- [`data/seed_events.json`](data/seed_events.json) — curated historical backfill, merged in.
- [`docs/feed.ics`](docs/feed.ics) — the subscription feed.
- `docs/index.html` — the generated website.

## Resilience (why the feed never breaks)

- **Never publish empty/partial data** — a recognizable section that yields nothing is
  treated as a structure break: the run aborts, keeps last-known-good, and fails loudly.
- **Validate before publish** — the generated `.ics` is round-tripped through a validator
  before any file is written.
- **Never delete** — known events (seed + previously seen) are merged, never dropped.
- **Conditional + polite** — `If-None-Match` / `If-Modified-Since`, exponential backoff,
  and a descriptive `User-Agent`. The daily run is conditional, so unchanged days return
  `304` and only refresh time-based status — Apple's HTML is fetched in full only when it
  actually changes.
- **Alerting** — a failing run opens/updates a single tracking GitHub issue; a newly
  detected event is announced as a note on the CI run (job summary + annotation) —
  subscribers get the event itself through the calendar feed.

## Configure

Repo identity (used in feed UIDs, subscribe links, Pages URLs) resolves automatically from
`GITHUB_REPOSITORY` in GitHub Actions. For local runs, either export
`GITHUB_REPOSITORY=owner/repo` or edit the placeholders at the top of
[`src/apple_events_tracker/config.py`](src/apple_events_tracker/config.py). Tunables
(cadence, the 30-minute reminder, calendar metadata, selectors, event-name patterns) all
live in that file.

### Enabling GitHub Pages (one-time)

In **Settings → Pages**, set **Source = GitHub Actions**. The
[`update`](.github/workflows/update.yml) workflow then deploys `docs/` on each run that
produces changes (and on any manual run).

## Develop

```bash
make sync          # uv sync
make lint          # ruff check
make format        # ruff format
make type-check    # mypy (strict)
uv run pytest      # fixture + unit + integration tests

# Run the pipeline locally against a saved fixture (no network):
uv run apple-events-tracker --from-file tests/fixtures/apple-events-with-recent.html \
  --now 2026-06-08T17:00:00Z --verbose

# Run it for real (fetches apple.com, honours the HTTP cache):
uv run apple-events-tracker --verbose
```

Useful flags: `--dry-run` (compute & report, write nothing), `--no-fetch-ics`,
`--no-conditional`, `--data-dir` / `--docs-dir`, `--now ISO8601`.

### Refreshing parser fixtures (RES-6)

`tests/fixtures/` holds saved HTML snapshots that the parser tests assert against. When
Apple changes the page, capture a fresh snapshot and update the expectations:

```bash
curl -sSL https://www.apple.com/apple-events/ -o tests/fixtures/apple-events-with-recent.html
```

## License

[MIT](LICENSE). The event data is factual scheduling metadata. Review Apple's site Terms
of Use and `robots.txt` before publishing your own instance — this project flags the
consideration but is not legal advice.
