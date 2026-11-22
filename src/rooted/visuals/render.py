"""Render VisualSpecs to SVG files styled from the DesignSpec (WP4)."""


from __future__ import annotations

import logging
from pathlib import Path

from rooted.models import (
    ColorRole,
    ContentDoc,
    DesignSpec,
    RenderedVisual,
    VisualKind,
    VisualPlan,
    VisualSpec,
)
from rooted.visuals.charts import render_chart
from rooted.visuals.diagrams import diagram_svg
from rooted.visuals.svg import DECOR_ASPECT, Theme, cover_art_svg, decor_svg, icon_svg

log = logging.getLogger(__name__)

ICON_SIZES_PT = {"small": 24.0, "medium": 48.0, "large": 96.0}


def text_width_pt(design: DesignSpec) -> float:
    return design.page_size.width_pt - design.margins.left - design.margins.right


def visual_width_pt(design: DesignSpec, size: str) -> float:
    """small = 1/3 of the text width, medium = one column, large = full text width."""
    full = text_width_pt(design)
    if size == "small":
        return round(full / 3, 2)
    if size == "large":
        return round(full, 2)
    cols = max(1, design.grid.columns)
    return round((full - design.grid.gutter_pt * (cols - 1)) / cols, 2)


def _render_one(v: VisualSpec, design: DesignSpec, doc: ContentDoc, theme: Theme, out_dir: Path) -> tuple[str, float, float]:
    """Returns (relative path, width_pt, height_pt)."""
    rel_dir = Path(out_dir.name)
    title = v.alt_text or (v.caption.text if v.caption else "")
    if v.kind == VisualKind.USER_IMAGE or (v.kind == VisualKind.COVER_ART and v.image_block_id):
        img = doc.block(v.image_block_id or "").image
        if img is None:
            raise ValueError(f"block {v.image_block_id} has no image")
        width = (design.page_size.width_pt if v.kind == VisualKind.COVER_ART
                 else visual_width_pt(design, v.size))
        return img.path, width, round(width * img.height_px / max(img.width_px, 1), 2)

    width = visual_width_pt(design, v.size)
    if v.kind == VisualKind.CHART and v.chart:
        svg, height = render_chart(v.chart, theme, width)
    elif v.kind == VisualKind.DIAGRAM and v.diagram:
        svg, height = diagram_svg(v.diagram, theme, width, title)
    elif v.kind == VisualKind.ICON and v.icon_name:
        side = ICON_SIZES_PT.get(v.size, 48.0)
        svg, width, height = icon_svg(v.icon_name, theme, side, ColorRole.PRIMARY, title), side, side
    elif v.kind == VisualKind.DECOR and v.decor:
        height = round(width * DECOR_ASPECT.get(v.decor.motif, 0.3), 2)
        svg = decor_svg(v.decor, theme, width, height, title)
    elif v.kind == VisualKind.COVER_ART and v.decor:
        width, height = design.page_size.width_pt, design.page_size.height_pt
        svg = cover_art_svg(v.decor, theme, width, height, title)
    else:
        raise ValueError(f"visual {v.id} ({v.kind.value}) is missing its spec")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{v.id}.svg").write_text(svg, encoding="utf-8")
    return (rel_dir / f"{v.id}.svg").as_posix(), round(width, 2), round(height, 2)


def render_visuals_checked(plan: VisualPlan, design: DesignSpec, doc: ContentDoc,
                           out_dir: Path) -> tuple[list[RenderedVisual], dict[str, str]]:
    """Like :func:`render_visuals` but also returns ``{visual_id: error}`` for
    visuals that failed to render (they are skipped, never fatal)."""
    out_dir = Path(out_dir)
    theme = Theme.from_design(design)
    rendered: list[RenderedVisual] = []
    errors: dict[str, str] = {}
    for v in plan.visuals:
        try:
            path, w, h = _render_one(v, design, doc, theme, out_dir)
        except Exception as e:  # one bad visual must not sink the build
            log.warning("visual %s failed to render: %s", v.id, e)
            errors[v.id] = f"render failed: {e}"
            continue
        rendered.append(RenderedVisual(visual_id=v.id, path=path, width_pt=w, height_pt=h))
    return rendered, errors


def render_visuals(plan: VisualPlan, design: DesignSpec, doc: ContentDoc, out_dir: Path) -> list[RenderedVisual]:
    """Charts: matplotlib -> SVG with ``svg.fonttype = 'none'`` so text stays
    live and uses the CSS @font-face fonts; colors from
    ``design.figure.series_roles``. Diagrams/decor/cover art/icons: hand-built
    SVG (no external deps). USER_IMAGE: referenced from the docx asset path.
    Sizes: small = 1/3 text width, medium = 1 column, large = full text width.
    ``RenderedVisual.path`` is relative to the work dir (``<out_dir.name>/<id>.svg``)."""
    return render_visuals_checked(plan, design, doc, out_dir)[0]

