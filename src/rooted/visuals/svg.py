"""Hand-built SVG: icons, decor motifs, cover art, and shared helpers (WP4).

Everything here is deterministic (``seed``-driven ``random.Random``) and uses
only DesignSpec palette colors and the DesignSpec font family.
"""


from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence
from xml.sax.saxutils import escape

from rooted.models import ColorRole, DecorSpec, DesignSpec, TypeRole
from rooted.visuals.icons import ICON_NAMES, icon_fragment

__all__ = [
    "ICON_NAMES", "Theme", "esc", "f", "svg_doc", "wrap_text", "icon_svg",
    "decor_fragment", "decor_svg", "cover_art_svg", "DECOR_ASPECT",
]


def esc(text: str) -> str:
    return escape(text, {'"': "&quot;"})


def f(x: float) -> str:
    """Compact, deterministic number formatting."""
    s = f"{x:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


@dataclass(frozen=True)
class Theme:
    """Palette and font resolved from a DesignSpec."""

    design: DesignSpec
    family: str

    @classmethod
    def from_design(cls, design: DesignSpec, role: TypeRole = TypeRole.BODY) -> Theme:
        return cls(design=design, family=design.style(role).family)

    def color(self, role: ColorRole) -> str:
        return self.design.color(role).upper()

    def series_colors(self) -> list[str]:
        roles = self.design.figure.series_roles or [ColorRole.PRIMARY, ColorRole.ACCENT]
        return [self.color(r) for r in roles]

    def family_for(self, role: TypeRole) -> str:
        return self.design.style(role).family

    def weight_for(self, role: TypeRole) -> int:
        return self.design.style(role).weight


def svg_doc(width: float, height: float, body: str, *, title: str = "", desc: str = "",
            view_box: tuple[float, float, float, float] | None = None) -> str:
    vb = view_box or (0, 0, width, height)
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{f(width)}pt" height="{f(height)}pt" '
            f'viewBox="{f(vb[0])} {f(vb[1])} {f(vb[2])} {f(vb[3])}" role="img">')
    meta = (f"<title>{esc(title)}</title>" if title else "") + (f"<desc>{esc(desc)}</desc>" if desc else "")
    return f"{head}{meta}{body}</svg>\n"


def wrap_text(text: str, max_width: float, font_size: float, char_w: float = 0.54) -> list[str]:
    """Greedy word wrap using an average glyph width of ``char_w`` em."""
    per_line = max(1, int(max_width / (font_size * char_w)))
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if len(trial) <= per_line or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


# --------------------------------------------------------------------------
# Icons
# --------------------------------------------------------------------------


def icon_svg(name: str, theme: Theme, size_pt: float = 48, role: ColorRole = ColorRole.PRIMARY,
             title: str = "") -> str:
    inner = icon_fragment(name, theme.color(role))
    return svg_doc(size_pt, size_pt, inner, title=title, view_box=(0, 0, 24, 24))


# --------------------------------------------------------------------------
# Decor motifs
# --------------------------------------------------------------------------

# height / width of each motif when the caller does not dictate a size
DECOR_ASPECT: dict[str, float] = {
    "band": 0.16, "corner": 1.0, "dots": 0.4, "waves": 0.3, "diagonal": 0.3, "grid": 0.5,
}


def _decor_colors(theme: Theme, spec: DecorSpec) -> list[str]:
    roles = spec.color_roles or [ColorRole.PRIMARY, ColorRole.ACCENT]
    return [theme.color(r) for r in roles]


def decor_fragment(spec: DecorSpec, theme: Theme, w: float, h: float) -> str:
    """SVG elements for one motif filling a ``w`` x ``h`` box."""
    rnd = random.Random(f"decor:{spec.motif}:{spec.seed}")
    cols = _decor_colors(theme, spec)
    n = len(cols)
    out: list[str] = []
    m = spec.motif

    if m == "band":
        y = 0.0
        for i in range(max(2, min(3, n + 1))):
            bh = h * (0.5 if i == 0 else rnd.uniform(0.12, 0.25))
            out.append(f'<rect x="0" y="{f(y)}" width="{f(w)}" height="{f(bh)}" fill="{cols[i % n]}"/>')
            y += bh + h * 0.04
    elif m == "corner":
        r = min(w, h)
        for i in range(4):
            rr = r * (1 - i * 0.22)
            out.append(f'<path d="M{f(w)} 0 L{f(w - rr)} 0 A{f(rr)} {f(rr)} 0 0 0 {f(w)} {f(rr)} Z" '
                       f'fill="{cols[i % n]}" fill-opacity="{f(0.55 + 0.15 * (i % 2))}"/>')
    elif m == "dots":
        step = max(8.0, min(w, h) / 6)
        cols_n, rows_n = max(1, int(w // step)), max(1, int(h // step))
        for r_ in range(rows_n):
            for c_ in range(cols_n):
                rad = step * rnd.uniform(0.12, 0.34)
                out.append(f'<circle cx="{f(step * (c_ + 0.5))}" cy="{f(step * (r_ + 0.5))}" r="{f(rad)}" '
                           f'fill="{cols[(r_ + c_) % n]}"/>')
    elif m == "waves":
        layers = 3
        for i in range(layers):
            base = h * (0.35 + 0.22 * i)
            amp = h * rnd.uniform(0.10, 0.22)
            k = rnd.choice((2, 3, 4))
            seg = w / k
            d = f"M0 {f(base)}"
            for s in range(k):
                x0 = s * seg
                sign = 1 if s % 2 == 0 else -1
                d += (f" C{f(x0 + seg * 0.33)} {f(base - sign * amp)} {f(x0 + seg * 0.66)} {f(base + sign * amp)} "
                      f"{f(x0 + seg)} {f(base)}")
            d += f" L{f(w)} {f(h)} L0 {f(h)} Z"
            out.append(f'<path d="{d}" fill="{cols[i % n]}" fill-opacity="{f(0.45 + 0.2 * i)}"/>')
    elif m == "diagonal":
        cid = f"clip-diag-{spec.seed}"
        out.append(f'<clipPath id="{cid}"><rect x="0" y="0" width="{f(w)}" height="{f(h)}"/></clipPath>')
        step = max(6.0, h / rnd.choice((4, 5, 6)))
        sw = step * rnd.uniform(0.25, 0.45)
        lines = []
        x = -h
        i = 0
        while x < w + h:
            lines.append(f'<line x1="{f(x)}" y1="{f(h)}" x2="{f(x + h)}" y2="0" stroke="{cols[i % n]}" '
                         f'stroke-width="{f(sw)}"/>')
            x += step
            i += 1
        out.append(f'<g clip-path="url(#{cid})">{"".join(lines)}</g>')
    elif m == "grid":
        step = max(8.0, min(w, h) / 5)
        cols_n, rows_n = max(1, int(w // step)), max(1, int(h // step))
        rule = theme.color(ColorRole.RULE)
        for r_ in range(rows_n):
            for c_ in range(cols_n):
                fill = cols[rnd.randrange(n)] if rnd.random() < 0.22 else "none"
                out.append(f'<rect x="{f(c_ * step)}" y="{f(r_ * step)}" width="{f(step)}" height="{f(step)}" '
                           f'fill="{fill}" stroke="{rule}" stroke-width="0.5"/>')
    else:
        raise ValueError(f"unknown decor motif {m!r}")
    return "".join(out)


def decor_svg(spec: DecorSpec, theme: Theme, w: float, h: float, title: str = "") -> str:
    return svg_doc(w, h, decor_fragment(spec, theme, w, h), title=title)


def cover_art_svg(spec: DecorSpec, theme: Theme, w: float, h: float, title: str = "") -> str:
    """Page-sized, content-free cover composition: the requested motif in the
    lower part plus a secondary motif (chosen from the seed) in the upper corner."""
    rnd = random.Random(f"cover:{spec.seed}")
    others: Sequence[str] = [m for m in DECOR_ASPECT if m != spec.motif]
    second = rnd.choice(others)
    main_h = h * 0.38
    sec_h = min(h * 0.22, w * 0.4)
    sec_w = min(w * 0.45, w)
    body = (
        f'<g transform="translate({f(w - sec_w)} 0)">'
        f'{decor_fragment(spec.model_copy(update={"motif": second, "seed": spec.seed + 1}), theme, sec_w, sec_h)}</g>'
        f'<g transform="translate(0 {f(h - main_h)})">{decor_fragment(spec, theme, w, main_h)}</g>'
    )
    return svg_doc(w, h, body, title=title)

