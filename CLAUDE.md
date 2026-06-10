# CLAUDE.md

## Overview
**Apple Events Tracker** — an auto-updating tracker for Apple special events, managed with [uv](https://docs.astral.sh/uv/) on Python 3.14+. A scheduled GitHub Action scrapes <https://www.apple.com/apple-events/>, keeps a structured last-known-good record in git, and publishes an iCalendar feed (`docs/feed.ics`) plus a static GitHub Pages site (`docs/index.html`). Full requirements live in [OBJECTIVE.md](OBJECTIVE.md); see [README.md](README.md) for usage. Key principle: git holds the canonical state — a failed/suspicious scrape never overwrites it.

Run the pipeline locally with `uv run apple-events-tracker` (add `--from-file tests/fixtures/...` for an offline run, `--dry-run` to write nothing).

## Commands
All commands are wrapped in the [Makefile](Makefile):

- `make sync` — sync dependencies (`uv sync`)
- `make lint` — lint (`uv run ruff check .`)
- `make format` — format (`uv run ruff format .`)
- `make type-check` — type-check (`uv run mypy .`)
- `make preview` — render the site from `data/events.json` and serve it locally to preview before committing (re-renders on each reload; `PORT=8080 make preview` to change port)
- `make post-create` — first-time setup (tool checks, `uv init`, `uv sync`); runs automatically on devcontainer creation

Direct `uv` usage:

- `uv add <pkg>` — add a runtime dependency
- `uv add --dev <pkg>` — add a dev dependency
- `uv run <cmd>` — run a command/script inside the project environment

## Toolchain & conventions
- **Python `>=3.14`**, package manager is **uv**. Use `uv` / `uv run` — not bare `pip`. The devcontainer ships a **native** Python at `/usr/local/bin/python` (no virtualenv); uv installs into that system environment via `UV_PROJECT_ENVIRONMENT=/usr/local`.
- **ruff** is both formatter and linter. Format-on-save, organize-imports, and `fixAll.ruff` are enabled in [.vscode/settings.json](.vscode/settings.json). Run `make format` and `make lint` before considering work done.
- **mypy** for type checking via `make type-check`.
- Tool configuration belongs in [pyproject.toml](pyproject.toml) under `[tool.*]` sections.

## Layout
- [src/apple_events_tracker/](src/apple_events_tracker/) — the package: `config` (selectors, event-name patterns, repo identity), `model` (data model + validation), `fetch` (conditional HTTP), `parse` (DS-1 HTML + DS-2 `.ics` — the fragile part), `diff` (change detection), `ics` (feed gen + validation), `site` (Jinja render), `notify` (GitHub issues), `cli` (orchestrator).
- [templates/index.html.j2](templates/index.html.j2) — site template.
- [data/](data/) — `events.json` (canonical last-known-good, committed), `seed_events.json` (curated historical backfill), `http_cache.json` (ETag/Last-Modified).
- [docs/](docs/) — GitHub Pages root: generated `index.html`, `feed.ics`, `assets/`.
- [tests/](tests/) — pytest suite; `tests/fixtures/` holds saved HTML snapshots the parser asserts against (refresh intentionally when Apple changes the page).
- [.github/workflows/](.github/workflows/) — `update.yml` (weekly cron + dispatch pipeline) and `ci.yml` (lint/type/test on push).

**Note:** `data/` and `docs/` are intentionally committed (canonical state + published site) — not gitignored.

## Git conventions
- **Never add co-authors to commits.** Do not include `Co-Authored-By:` trailers (or any other co-author attribution) in commit messages. Commits must have a single author.

## Dev environment
Devcontainer "Python + uv" with VS Code extensions for Python, Pylance, ruff, mypy, and Jupyter.