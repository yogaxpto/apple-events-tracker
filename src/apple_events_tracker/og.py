"""Open Graph card generator (social link previews).

Renders a 1200x630 PNG to ``<out_dir>/assets/og.png`` featuring the next upcoming
event, so Facebook / iMessage / Twitter show a rich preview card instead of a bare
link. The card is drawn with Pillow's bundled scalable default font, so the PNG is
fully self-contained — no external fonts or CDNs, consistent with WEB-4/WEB-5.

This module only *draws*; the caller (:mod:`site`) computes the title/subtitle copy
so date formatting stays in one place.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

OG_WIDTH = 1200
OG_HEIGHT = 630

# Palette mirrors docs/assets/style.css so the card matches the live site.
_BG = (5, 6, 10)  # --bg
_TEXT = (245, 246, 248)  # --text
_TEXT_DIM = (167, 173, 186)  # --text-dim
_ACCENT = (41, 151, 255)  # --accent (Apple blue)
_ACCENT_2 = (191, 90, 242)  # --accent-2 (violet)
_ACCENT_3 = (255, 55, 95)  # --accent-3 (pink)

# Aurora blobs: (center_x_frac, center_y_frac, radius_px, color, alpha) — mirrors the
# CSS .aurora__blob placements and opacities.
_BLOBS = (
    (0.0, 0.0, 520, _ACCENT, 82),
    (1.05, 0.05, 540, _ACCENT_2, 72),
    (0.45, 1.1, 560, _ACCENT_3, 46),
)

_MARGIN = 80


def _aurora_background() -> Image.Image:
    """Dark base with soft, blurred colored blobs echoing the site's aurora."""
    base = Image.new("RGBA", (OG_WIDTH, OG_HEIGHT), (*_BG, 255))
    for cx, cy, radius, color, alpha in _BLOBS:
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        x, y = cx * OG_WIDTH, cy * OG_HEIGHT
        ImageDraw.Draw(layer).ellipse(
            (x - radius, y - radius, x + radius, y + radius),
            fill=(*color, alpha),
        )
        layer = layer.filter(ImageFilter.GaussianBlur(radius * 0.55))
        base = Image.alpha_composite(base, layer)
    return base.convert("RGB")


def _font(size: int) -> ImageFont.FreeTypeFont:
    """Pillow's bundled scalable default font (Aileron) at ``size`` px."""
    # load_default(size=...) returns the scalable Aileron TrueType (Pillow >= 10.1);
    # the union return type includes the legacy bitmap ImageFont, which we never hit.
    font = ImageFont.load_default(size=size)
    assert isinstance(font, ImageFont.FreeTypeFont)
    return font


def _wrap(
    text: str, font: ImageFont.FreeTypeFont, max_width: float, draw: ImageDraw.ImageDraw
) -> list[str]:
    """Greedy word-wrap to fit ``max_width`` (long single words are left intact)."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if not current or draw.textlength(trial, font=font) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def render_og_image(
    out_dir: str | Path,
    *,
    title: str,
    subtitle: str,
    eyebrow: str = "Apple event tracker",
    footer: str = "Apple Events Tracker",
) -> Path:
    """Draw the social card to ``<out_dir>/assets/og.png`` and return its path."""
    img = _aurora_background()
    draw = ImageDraw.Draw(img)
    content_width = OG_WIDTH - 2 * _MARGIN

    # Eyebrow with a small accent dot, mirroring the site's hero eyebrow.
    eyebrow_font = _font(30)
    dot_r = 7
    dot_cy = _MARGIN + 16
    draw.ellipse(
        (_MARGIN, dot_cy - dot_r, _MARGIN + 2 * dot_r, dot_cy + dot_r),
        fill=_ACCENT_3,
    )
    draw.text(
        (_MARGIN + 2 * dot_r + 18, _MARGIN),
        eyebrow.upper(),
        font=eyebrow_font,
        fill=_TEXT_DIM,
    )

    # Title — large, wrapped, faux-bold via a stroke.
    title_font = _font(120)
    title_lines = _wrap(title, title_font, content_width, draw)
    line_h = int(title_font.size * 1.12)
    block_h = line_h * len(title_lines)
    # Vertically center the title block in the middle band of the card.
    y = (OG_HEIGHT - block_h) // 2 - 10
    for line in title_lines:
        draw.text(
            (_MARGIN, y), line, font=title_font, fill=_TEXT, stroke_width=2, stroke_fill=_TEXT
        )
        y += line_h

    # Subtitle (date / tagline) below the title.
    subtitle_font = _font(48)
    draw.text((_MARGIN, y + 14), subtitle, font=subtitle_font, fill=_ACCENT)

    # Footer wordmark, bottom-left.
    footer_font = _font(32)
    fy = OG_HEIGHT - _MARGIN - footer_font.size
    draw.text((_MARGIN, fy), footer, font=footer_font, fill=_TEXT_DIM)

    assets = Path(out_dir) / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    path = assets / "og.png"
    img.save(path, "PNG", optimize=True)
    return path
