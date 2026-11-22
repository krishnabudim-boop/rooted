"""matplotlib chart renderers, one function per ChartType (WP4).

Charts are drawn with the Agg-independent SVG backend and ``svg.fonttype =
'none'`` so text stays live text that picks up the document's @font-face
fonts. Colors come only from the DesignSpec palette. Numbers printed on the
chart are the ``raw`` strings the user wrote; no value is computed or invented
(value-axis tick labels, which are scale marks, appear only on line and
stacked charts).
"""


from __future__ import annotations

import io
import logging
import math
import os
import re
from typing import Callable

import matplotlib

matplotlib.use("Agg")
import matplotlib.font_manager as fm  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402

from rooted.models import ChartSpec, ChartType, ColorRole, DesignSpec, TypeRole  # noqa: E402
from rooted.visuals.svg import Theme, esc, f, svg_doc, wrap_text  # noqa: E402

__all__ = ["render_chart", "chart_height", "CHART_RENDERERS"]

_HEX = re.compile(r"#[0-9a-fA-F]{6}\b")
_registered: set[str] = set()


def chart_height(chart_type: ChartType, width_pt: float, n_categories: int) -> float:
    """Natural height for a chart of ``width_pt``."""
    if chart_type == ChartType.BAR:
        return max(width_pt * 0.3, 34.0 * n_categories + 20.0)
    if chart_type == ChartType.DONUT:
        return max(width_pt * 0.5, 110.0)
    if chart_type == ChartType.STAT_TILES:
        per_row = _tiles_per_row(width_pt, n_categories)
        rows = math.ceil(n_categories / per_row)
        return rows * 84.0 + (rows - 1) * 10.0
    return width_pt * 0.55


def _register_fonts(design: DesignSpec) -> None:
    """Make embedded font files known to matplotlib so text measures correctly."""
    for res in design.fonts:
        for face in res.faces:
            if face.path not in _registered and os.path.isfile(face.path):
                _registered.add(face.path)
                try:
                    fm.fontManager.addfont(face.path)
                except Exception:  # pragma: no cover - unreadable font file
                    pass


def _setup(theme: Theme, width: float, height: float):
    _register_fonts(theme.design)
    body = theme.design.style(TypeRole.BODY)
    plt.rcParams.update({
        "svg.fonttype": "none",
        "svg.hashsalt": "rooted",
        "font.family": [theme.family],
        "font.size": 8,
        "axes.unicode_minus": False,
        "text.color": theme.color(ColorRole.TEXT),
        "axes.labelcolor": theme.color(ColorRole.TEXT_MUTED),
        "xtick.color": theme.color(ColorRole.TEXT_MUTED),
        "ytick.color": theme.color(ColorRole.TEXT_MUTED),
        "font.weight": "normal" if body.weight < 600 else "bold",
    })
    fig = plt.figure(figsize=(width / 72.0, height / 72.0), dpi=72)
    fig.patch.set_alpha(0.0)
    return fig


def _style_axes(ax, theme: Theme, *, grid_axis: str | None, show_value_axis: bool, horizontal: bool = False):
    rule = theme.color(ColorRole.RULE)
    ax.set_facecolor("none")
    ax.patch.set_alpha(0.0)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    cat_side = "left" if horizontal else "bottom"
    ax.spines[cat_side].set_visible(True)
    ax.spines[cat_side].set_color(rule)
    ax.tick_params(length=0, labelsize=8)
    if grid_axis and theme.design.figure.gridlines and show_value_axis:
        ax.grid(axis=grid_axis, color=rule, linewidth=0.5)
        ax.set_axisbelow(True)
    val_axis = ax.xaxis if horizontal else ax.yaxis
    if not show_value_axis:
        val_axis.set_visible(False)


def _to_svg(fig, title: str) -> str:
    buf = io.StringIO()
    meta = {"Date": None, "Creator": "rooted"}
    if title:
        meta["Title"] = title
    fig.savefig(buf, format="svg", metadata=meta)
    plt.close(fig)
    svg = buf.getvalue()
    # normalize palette hexes to the DesignSpec's uppercase form
    svg = _HEX.sub(lambda m: m.group(0).upper(), svg)
    return svg


def _bar_colors(spec: ChartSpec, base: str, muted: str) -> list[str]:
    n = len(spec.categories)
    if spec.highlight_category is None:
        return [base] * n
    return [base if i == spec.highlight_category else muted for i in range(n)]


def _raw(spec: ChartSpec, s: int, i: int) -> str:
    return spec.series[s].points[i].raw


def _wrap_cats(cats: list[str], width_each: float) -> list[str]:
    return ["\n".join(wrap_text(c, width_each, 8)) for c in cats]


def _legend(ax, theme: Theme, spec: ChartSpec) -> None:
    if len(spec.series) > 1:
        leg = ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.18),
                        ncol=min(4, len(spec.series)))
        for t in leg.get_texts():
            t.set_color(theme.color(ColorRole.TEXT))


def _column(spec: ChartSpec, theme: Theme, w: float, h: float, *, horizontal: bool = False) -> str:
    fig = _setup(theme, w, h)
    ax = fig.add_subplot(111)
    cols = theme.series_colors()
    muted = theme.color(ColorRole.TEXT_MUTED)
    n, m = len(spec.categories), len(spec.series)
    group = 0.78
    bw = group / m
    positions = list(range(n))
    vmax = max((p.value for s in spec.series for p in s.points), default=1.0) or 1.0
    vmin = min(0.0, min((p.value for s in spec.series for p in s.points), default=0.0))
    for si, s in enumerate(spec.series):
        colors = _bar_colors(spec, cols[si % len(cols)], muted) if m == 1 else [cols[si % len(cols)]] * n
        offs = [p - group / 2 + bw * (si + 0.5) for p in positions]
        vals = [p.value for p in s.points]
        if horizontal:
            bars = ax.barh(offs, vals, height=bw * 0.92, color=colors, linewidth=0)
        else:
            bars = ax.bar(offs, vals, width=bw * 0.92, color=colors, linewidth=0)
        for i, b in enumerate(bars):
            label = _raw(spec, si, i)
            if horizontal:
                ax.annotate(label, (b.get_width(), b.get_y() + b.get_height() / 2), xytext=(4, 0),
                            textcoords="offset points", va="center", ha="left", fontsize=8,
                            fontweight="bold", color=theme.color(ColorRole.TEXT))
            else:
                ax.annotate(label, (b.get_x() + b.get_width() / 2, b.get_height()), xytext=(0, 3),
                            textcoords="offset points", va="bottom", ha="center", fontsize=8,
                            fontweight="bold", color=theme.color(ColorRole.TEXT))
    cats = _wrap_cats(spec.categories, (w / n) * 0.9 if not horizontal else w * 0.3)
    if horizontal:
        ax.set_yticks(positions)
        ax.set_yticklabels(cats)
        ax.invert_yaxis()
        ax.set_xlim(vmin, vmax * 1.18)
    else:
        ax.set_xticks(positions)
        ax.set_xticklabels(cats)
        ax.set_ylim(vmin, vmax * 1.18)
    _style_axes(ax, theme, grid_axis=None, show_value_axis=False, horizontal=horizontal)
    _legend(ax, theme, spec)
    if spec.value_axis_label:
        (ax.set_xlabel if horizontal else ax.set_ylabel)(spec.value_axis_label, fontsize=8)
    fig.subplots_adjust(left=0.04 if not horizontal else 0.3, right=0.98, top=0.92, bottom=0.2 if m > 1 else 0.14)
    return _to_svg(fig, spec.title.text if spec.title else "")


def render_column(spec: ChartSpec, theme: Theme, w: float, h: float) -> str:
    return _column(spec, theme, w, h)


def render_bar(spec: ChartSpec, theme: Theme, w: float, h: float) -> str:
    return _column(spec, theme, w, h, horizontal=True)


def render_stacked_column(spec: ChartSpec, theme: Theme, w: float, h: float) -> str:
    fig = _setup(theme, w, h)
    ax = fig.add_subplot(111)
    cols = theme.series_colors()
    n = len(spec.categories)
    bottoms = [0.0] * n
    on_primary = theme.color(ColorRole.ON_PRIMARY)
    text = theme.color(ColorRole.TEXT)
    for si, s in enumerate(spec.series):
        vals = [p.value for p in s.points]
        color = cols[si % len(cols)]
        ax.bar(range(n), vals, bottom=bottoms, width=0.6, color=color, linewidth=0, label=s.name)
        for i, v in enumerate(vals):
            ax.annotate(_raw(spec, si, i), (i, bottoms[i] + v / 2), ha="center", va="center", fontsize=7,
                        color=on_primary if si % 2 == 0 else text)
        bottoms = [b + v for b, v in zip(bottoms, vals, strict=True)]
    ax.set_xticks(range(n))
    ax.set_xticklabels(_wrap_cats(spec.categories, (w / n) * 0.9))
    _style_axes(ax, theme, grid_axis="y", show_value_axis=True)
    ax.tick_params(axis="y", labelsize=7)
    ax.yaxis.set_ticks_position("none")
    ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=min(4, len(spec.series)))
    if spec.value_axis_label:
        ax.set_ylabel(spec.value_axis_label, fontsize=8)
    fig.subplots_adjust(left=0.1, right=0.98, top=0.94, bottom=0.26)
    return _to_svg(fig, spec.title.text if spec.title else "")


def render_line(spec: ChartSpec, theme: Theme, w: float, h: float) -> str:
    fig = _setup(theme, w, h)
    ax = fig.add_subplot(111)
    cols = theme.series_colors()
    n = len(spec.categories)
    for si, s in enumerate(spec.series):
        color = cols[si % len(cols)]
        vals = [p.value for p in s.points]
        ax.plot(range(n), vals, color=color, linewidth=2, marker="o", markersize=4, label=s.name)
        for i, v in enumerate(vals):
            if i in (0, n - 1) or len(spec.series) == 1:
                ax.annotate(_raw(spec, si, i), (i, v), xytext=(0, 6), textcoords="offset points",
                            ha="center", va="bottom", fontsize=7.5, fontweight="bold",
                            color=theme.color(ColorRole.TEXT))
    ax.set_xticks(range(n))
    ax.set_xticklabels(_wrap_cats(spec.categories, (w / max(n, 1)) * 0.9))
    ax.margins(x=0.08, y=0.2)
    _style_axes(ax, theme, grid_axis="y", show_value_axis=True)
    ax.tick_params(axis="y", labelsize=7)
    if len(spec.series) > 1:
        ax.legend(frameon=False, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.16),
                  ncol=min(4, len(spec.series)))
    if spec.value_axis_label:
        ax.set_ylabel(spec.value_axis_label, fontsize=8)
    fig.subplots_adjust(left=0.1, right=0.97, top=0.92, bottom=0.26 if len(spec.series) > 1 else 0.16)
    return _to_svg(fig, spec.title.text if spec.title else "")


def render_donut(spec: ChartSpec, theme: Theme, w: float, h: float) -> str:
    fig = _setup(theme, w, h)
    ax = fig.add_axes([0.0, 0.04, 0.45, 0.92])
    cols = theme.series_colors()
    s = spec.series[0]
    vals = [max(p.value, 0.0) for p in s.points]
    if sum(vals) <= 0:
        vals = [1.0] * len(vals)
    colors = [cols[i % len(cols)] for i in range(len(vals))]
    ax.pie(vals, colors=colors, startangle=90, counterclock=False,
           wedgeprops={"width": 0.38, "linewidth": 1.5, "edgecolor": theme.color(ColorRole.BACKGROUND)})
    ax.set_aspect("equal")
    # legend as text: swatch + category + the user's own number
    lx = w * 0.5
    row_h = min(22.0, (h - 16) / max(len(vals), 1))
    y0 = h / 2 - (len(vals) - 1) * row_h / 2
    for i, p in enumerate(s.points):
        yy = 1 - (y0 + i * row_h) / h
        fig.patches.append(plt.Rectangle((lx / w, yy - 0.025), 9 / w, 9 / h, transform=fig.transFigure,
                                         facecolor=colors[i], linewidth=0))
        fig.text((lx + 15) / w, yy, f"{spec.categories[i]}  ", ha="left", va="center", fontsize=8,
                 color=theme.color(ColorRole.TEXT))
        fig.text((w - 4) / w, yy, p.raw, ha="right", va="center", fontsize=8, fontweight="bold",
                 color=theme.color(ColorRole.TEXT))
    return _to_svg(fig, spec.title.text if spec.title else "")


def _tiles_per_row(width: float, n: int) -> int:
    return max(1, min(n, 4, int(width // 90)))


def render_stat_tiles(spec: ChartSpec, theme: Theme, w: float, h: float) -> str:
    n = len(spec.categories)
    per_row = _tiles_per_row(w, n)
    gap = 10.0
    tw = (w - gap * (per_row - 1)) / per_row
    th = 84.0
    surf = theme.color(ColorRole.SURFACE)
    vstyle = theme.design.style(TypeRole.STAT_VALUE)
    lstyle = theme.design.style(TypeRole.STAT_LABEL)
    vcol = theme.color(vstyle.color_role)
    lcol = theme.color(lstyle.color_role)
    accent = theme.color(ColorRole.ACCENT)
    body = ""
    for i in range(n):
        r, c = divmod(i, per_row)
        x, y = c * (tw + gap), r * (th + gap)
        raw = spec.series[0].points[i].raw
        size = min(vstyle.size_pt, 30.0, (tw - 16) / max(len(raw), 1) / 0.62)
        body += (f'<rect x="{f(x)}" y="{f(y)}" width="{f(tw)}" height="{f(th)}" rx="4" fill="{surf}"/>'
                 f'<rect x="{f(x)}" y="{f(y)}" width="3" height="{f(th)}" fill="{accent}"/>'
                 f'<text x="{f(x + tw / 2)}" y="{f(y + 38)}" text-anchor="middle" font-family="{esc(vstyle.family)}" '
                 f'font-size="{f(size)}" font-weight="{vstyle.weight}" fill="{vcol}">{esc(raw)}</text>')
        for li, line in enumerate(wrap_text(spec.categories[i], tw - 16, 8.5)[:2]):
            body += (f'<text x="{f(x + tw / 2)}" y="{f(y + 56 + li * 11)}" text-anchor="middle" '
                     f'font-family="{esc(lstyle.family)}" font-size="8.5" fill="{lcol}">{esc(line)}</text>')
    return svg_doc(w, h, body, title=spec.title.text if spec.title else "")


CHART_RENDERERS: dict[ChartType, Callable[[ChartSpec, Theme, float, float], str]] = {
    ChartType.COLUMN: render_column,
    ChartType.BAR: render_bar,
    ChartType.STACKED_COLUMN: render_stacked_column,
    ChartType.LINE: render_line,
    ChartType.DONUT: render_donut,
    ChartType.STAT_TILES: render_stat_tiles,
}


def render_chart(spec: ChartSpec, theme: Theme, width_pt: float, height_pt: float | None = None) -> tuple[str, float]:
    """Render ``spec`` to an SVG string. Returns ``(svg, height_pt)``."""
    height = height_pt or chart_height(spec.chart_type, width_pt, len(spec.categories))
    prev = logging.getLogger("matplotlib.font_manager").level
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)  # family may not be installed
    try:
        with plt.rc_context():
            svg = CHART_RENDERERS[spec.chart_type](spec, theme, width_pt, height)
    finally:
        logging.getLogger("matplotlib.font_manager").setLevel(prev)
    return svg, height
