"""Conditional, polite HTTP with retry/backoff (FR-1, RES-4, NFR-3).

Stores per-URL ETag / Last-Modified in ``data/http_cache.json`` so repeat runs can send
``If-None-Match`` / ``If-Modified-Since`` and short-circuit on ``304 Not Modified``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from . import config as cfg
from .config import RuntimeConfig


class FetchError(RuntimeError):
    """Raised when a resource cannot be fetched after exhausting retries (RES-4)."""


@dataclass
class FetchResult:
    url: str
    status_code: int
    text: str
    headers: dict[str, str]

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304


@dataclass
class HttpCache:
    """ETag / Last-Modified per URL, persisted to JSON."""

    path: Path
    entries: dict[str, dict[str, str]] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> HttpCache:
        p = Path(path)
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            return cls(path=p, entries=data.get("entries", {}))
        return cls(path=p, entries={})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"entries": dict(sorted(self.entries.items()))}
        self.path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def conditional_headers(self, url: str) -> dict[str, str]:
        entry = self.entries.get(url, {})
        headers: dict[str, str] = {}
        if etag := entry.get("etag"):
            headers["If-None-Match"] = etag
        if lm := entry.get("last_modified"):
            headers["If-Modified-Since"] = lm
        return headers

    def update(self, url: str, response_headers: httpx.Headers) -> None:
        entry: dict[str, str] = {}
        if etag := response_headers.get("ETag"):
            entry["etag"] = etag
        if lm := response_headers.get("Last-Modified"):
            entry["last_modified"] = lm
        if entry:
            self.entries[url] = entry
        else:
            self.entries.pop(url, None)


def _request_with_retry(
    client: httpx.Client,
    url: str,
    headers: dict[str, str],
    *,
    sleep: object = time.sleep,
) -> httpx.Response:
    """GET ``url`` with exponential backoff. 304/2xx return; 4xx (except 429) fail fast."""
    last_exc: Exception | None = None
    for attempt in range(cfg.MAX_RETRIES):
        try:
            resp = client.get(url, headers=headers)
        except httpx.HTTPError as exc:  # network/timeout/transport errors
            last_exc = exc
        else:
            if resp.status_code == 304 or resp.is_success:
                return resp
            # Retry transient server errors and rate limiting; fail fast on other 4xx.
            if resp.status_code < 500 and resp.status_code != 429:
                raise FetchError(f"GET {url} returned {resp.status_code}")
            last_exc = FetchError(f"GET {url} returned {resp.status_code}")
        if attempt < cfg.MAX_RETRIES - 1:
            delay = cfg.BACKOFF_BASE_SECONDS ** (attempt + 1)
            sleep(delay)  # type: ignore[operator]
    raise FetchError(f"GET {url} failed after {cfg.MAX_RETRIES} attempts: {last_exc}")


def _client(config: RuntimeConfig) -> httpx.Client:
    return httpx.Client(
        follow_redirects=True,
        timeout=cfg.REQUEST_TIMEOUT_SECONDS,
        headers={"User-Agent": config.user_agent},
    )


def fetch_conditional(
    url: str,
    cache: HttpCache,
    config: RuntimeConfig,
    *,
    sleep: object = time.sleep,
) -> FetchResult:
    """Conditional GET (FR-1). On 304 the returned result has ``not_modified`` True and
    empty text. On 200 the cache is updated with the new validators."""
    with _client(config) as client:
        resp = _request_with_retry(client, url, cache.conditional_headers(url), sleep=sleep)
    if resp.status_code == 304:
        return FetchResult(url=url, status_code=304, text="", headers=dict(resp.headers))
    cache.update(str(resp.url), resp.headers)
    # also key the cache under the requested url so a redirect target doesn't lose validators
    cache.update(url, resp.headers)
    return FetchResult(
        url=str(resp.url),
        status_code=resp.status_code,
        text=resp.text,
        headers=dict(resp.headers),
    )


def fetch_text(url: str, config: RuntimeConfig, *, sleep: object = time.sleep) -> str:
    """Unconditional GET with retry, returning the body text (used for DS-2 event.ics)."""
    with _client(config) as client:
        resp = _request_with_retry(client, url, {}, sleep=sleep)
    if resp.status_code == 304:  # no validators were sent, so this is unexpected
        raise FetchError(f"unexpected 304 for unconditional GET {url}")
    return resp.text
