"""Local preview server — view the site before committing.

Renders ``templates/index.html.j2`` from the canonical ``data/events.json`` and serves
it on localhost. The page is re-rendered on every reload and the stylesheet is re-copied,
so edits to the template or ``docs/assets/style.css`` show up on a browser refresh with no
rebuild step. Output goes to a throwaway temp directory, so your committed ``docs/`` stays
untouched until you regenerate it for real (``uv run apple-events-tracker``).

Developer convenience only — not used by the pipeline or CI. Run via ``make preview``
(``PORT=… make preview`` to pick a port) or::

    uv run python -m apple_events_tracker.preview --port 8000
"""

from __future__ import annotations

import argparse
import shutil
import tempfile
from collections.abc import Callable
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import site as site_module
from .config import RuntimeConfig
from .model import load_store


def _render(data_dir: Path, out_dir: Path) -> None:
    """Render the site from canonical data into ``out_dir`` (refreshing the stylesheet)."""
    store = load_store(data_dir / "events.json")
    site_module.render_site(store, RuntimeConfig(), store.generated_at or "", out_dir=out_dir)
    # render_site only copies the stylesheet when missing; for a live preview always
    # refresh it so CSS edits appear on reload.
    src = Path(site_module._DEFAULT_STYLE_SRC)
    if src.exists():
        (out_dir / "assets").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, out_dir / "assets" / "style.css")


class _PreviewHandler(SimpleHTTPRequestHandler):
    """Serves the preview dir, re-rendering the page on each request for the index."""

    def __init__(self, *args: Any, render: Callable[[], None], **kwargs: Any) -> None:
        self._render = render
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:  # noqa: N802 — http.server casing
        if self.path in ("/", "/index.html"):
            try:
                self._render()
            except Exception as exc:  # noqa: BLE001 — surface render errors in the browser
                self.send_error(500, f"render failed: {exc}")
                return
        super().do_GET()

    def log_message(self, fmt: str, *args: Any) -> None:
        return  # keep the console quiet; we print our own banner


def serve(host: str, port: int, data_dir: Path) -> int:
    preview_dir = Path(tempfile.mkdtemp(prefix="apple-events-preview-"))

    def render() -> None:
        _render(data_dir, preview_dir)

    try:
        render()  # fail fast on a bad template/data before binding the socket
    except Exception as exc:  # noqa: BLE001
        print(f"initial render failed: {exc}")
        shutil.rmtree(preview_dir, ignore_errors=True)
        return 1

    handler = partial(_PreviewHandler, render=render, directory=str(preview_dir))
    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        print(f"could not bind {host}:{port} — {exc}. Try a different PORT.")
        shutil.rmtree(preview_dir, ignore_errors=True)
        return 1

    print(
        f"Preview at http://{host}:{port}/  (re-renders on each reload — Ctrl+C to stop)",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping preview…")
    finally:
        httpd.server_close()
        shutil.rmtree(preview_dir, ignore_errors=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="apple-events-tracker-preview", description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--data-dir", default="data")
    args = p.parse_args(argv)
    return serve(args.host, args.port, Path(args.data_dir))


if __name__ == "__main__":
    raise SystemExit(main())
