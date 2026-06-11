"""Favicon / app-icon generator.

Rasterizes the Apple glyph (the same SVG path used in the site header) onto an
aurora-gradient tile and writes the full icon set the page references:

* ``favicon.svg``         — vector, rounded tile (modern browsers prefer it)
* ``favicon.ico``         — 16/32/48 px fallback for older browsers / tab restore
* ``apple-touch-icon.png``— 180 px, full-bleed (iOS rounds the corners itself)
* ``assets/icon-192.png`` — 192 px maskable (PWA / Android home screen)
* ``assets/icon-512.png`` — 512 px maskable (PWA splash / install)

These are *static* assets: they never depend on event data, so they're generated
once and committed under ``docs/``. Re-run after a brand change with::

    uv run python -m apple_events_tracker.icons

Drawing is pure Pillow (no SVG rasterizer, no external fonts) — the glyph is the
SVG cubic-bezier path flattened to polygons here, so the output is self-contained
and reproducible. The palette mirrors ``docs/assets/style.css`` and :mod:`og`.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image, ImageDraw

# Palette mirrors docs/assets/style.css and og.py.
_ACCENT = (41, 151, 255)  # --accent (Apple blue)
_ACCENT_2 = (191, 90, 242)  # --accent-2 (violet)
_GLYPH = (255, 255, 255)  # white Apple mark

# The Apple glyph as it appears in templates/index.html.j2 (24x24 viewBox): the body
# subpath plus the leaf subpath. Kept identical to the header SVG so tab and page match.
_APPLE_PATH = (
    "M17.05 12.94c-.02-2.27 1.85-3.36 1.94-3.41-1.06-1.55-2.71-1.76-3.29-1.78"
    "-1.4-.14-2.73.82-3.44.82-.71 0-1.8-.8-2.96-.78-1.52.02-2.93.88-3.71 2.24"
    "-1.58 2.74-.4 6.8 1.13 9.02.75 1.09 1.64 2.31 2.81 2.27 1.13-.05 1.56-.73 "
    "2.92-.73 1.36 0 1.75.73 2.94.71 1.21-.02 1.98-1.11 2.72-2.21.86-1.27 "
    "1.21-2.5 1.23-2.56-.03-.01-2.36-.91-2.38-3.62z"
    "M14.8 6.2c.62-.76 1.04-1.8.93-2.85-.9.04-1.99.6-2.63 1.35-.57.67-1.08 "
    "1.74-.94 2.76 1 .08 2.02-.51 2.64-1.26z"
)

# SVG path d-string source, kept for the vector favicon (avoids re-serializing).
_APPLE_PATH_SVG = (
    "M17.05 12.94c-.02-2.27 1.85-3.36 1.94-3.41-1.06-1.55-2.71-1.76-3.29-1.78"
    "-1.4-.14-2.73.82-3.44.82-.71 0-1.8-.8-2.96-.78-1.52.02-2.93.88-3.71 2.24"
    "-1.58 2.74-.4 6.8 1.13 9.02.75 1.09 1.64 2.31 2.81 2.27 1.13-.05 1.56-.73 "
    "2.92-.73 1.36 0 1.75.73 2.94.71 1.21-.02 1.98-1.11 2.72-2.21.86-1.27 "
    "1.21-2.5 1.23-2.56-.03-.01-2.36-.91-2.38-3.62z"
    "M14.8 6.2c.62-.76 1.04-1.8.93-2.85-.9.04-1.99.6-2.63 1.35-.57.67-1.08 "
    "1.74-.94 2.76 1 .08 2.02-.51 2.64-1.26z"
)

_SS = 4  # supersample factor for anti-aliasing
_BEZIER_STEPS = 24  # segments per cubic when flattening

_TOKEN_RE = re.compile(r"([MmCcLlZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")


def _tokens(d: str) -> list[str]:
    return [m.group(0) for m in _TOKEN_RE.finditer(d)]


def _flatten_path(d: str) -> list[list[tuple[float, float]]]:
    """Flatten an SVG path (M/m, C/c, L/l, Z/z) into a list of polygon subpaths.

    Cubic beziers are sampled into ``_BEZIER_STEPS`` straight segments. Coordinates are
    returned in the original viewBox units; the caller scales them to the target size.
    """
    toks = _tokens(d)
    i = 0
    cx = cy = 0.0
    start_x = start_y = 0.0
    subpaths: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    cmd = ""

    def num() -> float:
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < len(toks):
        tok = toks[i]
        if tok.isalpha():
            cmd = tok
            i += 1
            if cmd in ("Z", "z"):
                if current:
                    subpaths.append(current)
                cx, cy = start_x, start_y
                current = []
            continue

        if cmd in ("M", "m"):
            x, y = num(), num()
            if cmd == "m":
                x, y = cx + x, cy + y
            if current:
                subpaths.append(current)
            current = [(x, y)]
            cx, cy = x, y
            start_x, start_y = x, y
            # Subsequent coordinate pairs after an M are implicit L commands.
            cmd = "L" if cmd == "M" else "l"
        elif cmd in ("L", "l"):
            x, y = num(), num()
            if cmd == "l":
                x, y = cx + x, cy + y
            current.append((x, y))
            cx, cy = x, y
        elif cmd in ("C", "c"):
            x1, y1, x2, y2, x, y = (num() for _ in range(6))
            if cmd == "c":
                x1, y1, x2, y2, x, y = (
                    cx + x1,
                    cy + y1,
                    cx + x2,
                    cy + y2,
                    cx + x,
                    cy + y,
                )
            for s in range(1, _BEZIER_STEPS + 1):
                t = s / _BEZIER_STEPS
                mt = 1 - t
                bx = mt**3 * cx + 3 * mt**2 * t * x1 + 3 * mt * t**2 * x2 + t**3 * x
                by = mt**3 * cy + 3 * mt**2 * t * y1 + 3 * mt * t**2 * y2 + t**3 * y
                current.append((bx, by))
            cx, cy = x, y
        else:  # pragma: no cover - the Apple path uses only M/c/z
            raise ValueError(f"unsupported path command: {cmd!r}")

    if current:
        subpaths.append(current)
    return subpaths


def _glyph_bounds(subpaths: list[list[tuple[float, float]]]) -> tuple[float, float, float, float]:
    xs = [p[0] for sp in subpaths for p in sp]
    ys = [p[1] for sp in subpaths for p in sp]
    return min(xs), min(ys), max(xs), max(ys)


def _gradient_tile(size: int) -> Image.Image:
    """A diagonal blue→violet gradient square (RGBA), echoing the site's aurora."""
    grad = Image.new("RGBA", (size, size))
    px = grad.load()
    assert px is not None
    denom = max(1, 2 * (size - 1))
    for y in range(size):
        for x in range(size):
            t = (x + y) / denom
            r = round(_ACCENT[0] + (_ACCENT_2[0] - _ACCENT[0]) * t)
            g = round(_ACCENT[1] + (_ACCENT_2[1] - _ACCENT[1]) * t)
            b = round(_ACCENT[2] + (_ACCENT_2[2] - _ACCENT[2]) * t)
            px[x, y] = (r, g, b, 255)
    return grad


def _draw_glyph(img: Image.Image, glyph_frac: float) -> None:
    """Fill the Apple glyph (white) centered on ``img``, scaled to ``glyph_frac`` of it."""
    size = img.width
    subpaths = _flatten_path(_APPLE_PATH)
    min_x, min_y, max_x, max_y = _glyph_bounds(subpaths)
    gw, gh = max_x - min_x, max_y - min_y
    target = size * glyph_frac
    scale = target / max(gw, gh)
    # Center the glyph's bounding box on the tile.
    off_x = (size - gw * scale) / 2 - min_x * scale
    off_y = (size - gh * scale) / 2 - min_y * scale
    draw = ImageDraw.Draw(img)
    for sp in subpaths:
        pts = [(x * scale + off_x, y * scale + off_y) for x, y in sp]
        draw.polygon(pts, fill=_GLYPH)


def _rounded_mask(size: int, radius_frac: float) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    r = int(size * radius_frac)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    return mask


def _render_tile(size: int, *, glyph_frac: float, radius_frac: float | None) -> Image.Image:
    """Render one icon at ``size`` px: gradient tile + white glyph, optionally rounded.

    ``radius_frac=None`` yields a full-bleed square (for maskable / apple-touch icons,
    which the platform masks itself); a value rounds the corners with transparency.
    """
    ss = size * _SS
    tile = _gradient_tile(ss)
    _draw_glyph(tile, glyph_frac)
    if radius_frac is not None:
        tile.putalpha(_rounded_mask(ss, radius_frac))
    return tile.resize((size, size), Image.Resampling.LANCZOS)


def _favicon_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'width="24" height="24" role="img" aria-label="Apple Events Tracker">\n'
        "  <defs>\n"
        '    <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">\n'
        f'      <stop offset="0" stop-color="rgb{_ACCENT}"/>\n'
        f'      <stop offset="1" stop-color="rgb{_ACCENT_2}"/>\n'
        "    </linearGradient>\n"
        "  </defs>\n"
        '  <rect width="24" height="24" rx="5.4" fill="url(#g)"/>\n'
        # Glyph scaled to ~62% and centered (the raw path spans ~x6-21, y3-21).
        '  <g transform="translate(2.1 1.5) scale(0.83)">\n'
        f'    <path d="{_APPLE_PATH_SVG}" fill="#fff"/>\n'
        "  </g>\n"
        "</svg>\n"
    )


def generate_icons(out_dir: str | Path = "docs") -> list[Path]:
    """Write the full icon set under ``out_dir`` (and ``out_dir/assets``); return paths."""
    out = Path(out_dir)
    assets = out / "assets"
    out.mkdir(parents=True, exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    svg_path = out / "favicon.svg"
    svg_path.write_text(_favicon_svg(), encoding="utf-8")
    written.append(svg_path)

    # Multi-size .ico from one high-res rounded master.
    ico_master = _render_tile(64, glyph_frac=0.62, radius_frac=0.22)
    ico_path = out / "favicon.ico"
    ico_master.save(ico_path, sizes=[(16, 16), (32, 32), (48, 48)])
    written.append(ico_path)

    # apple-touch-icon: full-bleed (iOS applies its own corner mask).
    touch = _render_tile(180, glyph_frac=0.6, radius_frac=None)
    touch_path = out / "apple-touch-icon.png"
    touch.save(touch_path, "PNG", optimize=True)
    written.append(touch_path)

    # Maskable PWA icons: full-bleed, glyph inside the central safe zone.
    for px in (192, 512):
        icon = _render_tile(px, glyph_frac=0.52, radius_frac=None)
        p = assets / f"icon-{px}.png"
        icon.save(p, "PNG", optimize=True)
        written.append(p)

    return written


def main() -> int:
    paths = generate_icons("docs")
    for p in paths:
        print(f"wrote {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
