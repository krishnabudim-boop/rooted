"""Page budgeting helpers (WP2). Pure functions, no LLM."""

from __future__ import annotations

import re

from rooted.content.brief import _SIZES
from rooted.models import (
    BlockType,
    Brief,
    ContentDoc,
    ContentPlan,
    DesignSpec,
    PageCapacity,
    PageType,
    Section,
    TypeRole,
)

AVG_WORD_CHARS = 6.0  # average English word plus the following space
GLYPH_EM = 0.5  # average glyph advance as a fraction of the font size
HEADING_OVERHEAD = 0.15
DEFAULT_OPENER_FLOW_FRACTION = 0.55

_CUSTOM = re.compile(r"^(\d+(?:\.\d+)?)x(\d+(?:\.\d+)?)(in|mm|cm)$")
_PER_UNIT = {"in": 72.0, "mm": 72.0 / 25.4, "cm": 72.0 / 2.54}


def page_dimensions(design: DesignSpec, brief: Brief | None) -> tuple[float, float]:
    """Page size in points; a brief page size overrides the design's."""
    if brief is not None and brief.page_size:
        key = brief.page_size.strip().lower()
        for name, w, h in _SIZES.values():
            if name.lower() == key:
                return w, h
        m = _CUSTOM.match(key)
        if m:
            f = _PER_UNIT[m.group(3)]
            return float(m.group(1)) * f, float(m.group(2)) * f
    return design.page_size.width_pt, design.page_size.height_pt


def _words_per_page(
    design: DesignSpec, width: float, height: float, columns: int, height_fraction: float = 1.0
) -> float:
    body = design.style(TypeRole.BODY)
    m = design.margins
    text_w = max(width - m.left - m.right, 1.0)
    text_h = max((height - m.top - m.bottom) * height_fraction, 1.0)
    columns = max(columns, 1)
    gutter = design.grid.gutter_pt
    col_w = max((text_w - gutter * (columns - 1)) / columns, 1.0)
    chars_per_line = col_w / (GLYPH_EM * body.size_pt)
    words_per_line = max(chars_per_line / AVG_WORD_CHARS, 1.0)
    lines = text_h / (body.size_pt * body.line_height)
    return lines * columns * words_per_line * (1.0 - HEADING_OVERHEAD)


def estimate_capacity(design: DesignSpec, brief: Brief) -> PageCapacity:
    """Words that fit on a body page: text-area width/height from page size,
    margins and grid; average glyph width ~0.5 * body size; line height from
    body TypeStyle; minus ~15% for headings/spacing."""
    width, height = page_dimensions(design, brief)
    body_tpl = design.template(PageType.BODY)
    columns = (body_tpl.columns if body_tpl and body_tpl.columns else design.grid.columns) or 1
    body_words = _words_per_page(design, width, height, columns)

    opener = design.template(PageType.SECTION_OPENER)
    if opener is not None:
        o_cols = opener.columns or design.grid.columns or 1
        flow = [r for r in opener.regions if r.kind == "flow"]
        fraction = (flow[0].bbox.y1 - flow[0].bbox.y0) if flow else DEFAULT_OPENER_FLOW_FRACTION
        opener_words = _words_per_page(design, width, height, o_cols, min(max(fraction, 0.1), 1.0))
    else:
        opener_words = body_words * DEFAULT_OPENER_FLOW_FRACTION

    def wanted(flag: bool | None, page_type: PageType) -> int:
        if flag is None:
            return int(design.template(page_type) is not None)
        return int(flag)

    fixed = (
        wanted(brief.include_cover, PageType.COVER)
        + wanted(brief.include_back_cover, PageType.BACK_COVER)
        + wanted(brief.include_toc, PageType.TOC)
    )
    # An unspecified TOC is only counted when the design has a TOC page template.
    return PageCapacity(
        words_per_body_page=round(body_words, 1),
        words_per_opener_page=round(opener_words, 1),
        fixed_pages=fixed,
    )


def _block_weight(doc: ContentDoc, block_id: str, capacity: PageCapacity) -> float:
    try:
        b = doc.block(block_id)
    except KeyError:
        return 0.0
    words = float(len(b.text.split()))
    if b.type == BlockType.TABLE and b.table is not None:
        return max(words, 12.0 * len(b.table.rows))
    if b.type == BlockType.IMAGE:
        return 0.35 * capacity.words_per_body_page
    return words


def section_weight(section: Section, doc: ContentDoc, capacity: PageCapacity) -> float:
    """Word-equivalents a section needs: its words, tables and images weighted
    as visuals, stats and pull quotes, plus the page area an opener loses."""
    ids = list(section.block_ids) + [i for c in section.callouts for i in c.block_ids]
    w = sum(_block_weight(doc, i, capacity) for i in ids)
    w += 30.0 * len(section.key_stats) + 40.0 * len(section.pull_quotes)
    if section.opener:
        w += max(capacity.words_per_body_page - capacity.words_per_opener_page, 0.0)
    return max(w, 1.0)


def assign_page_budgets(
    plan: ContentPlan, doc: ContentDoc, capacity: PageCapacity, target_pages: int | None
) -> ContentPlan:
    """Fill ``Section.page_budget`` proportionally to word count + visuals so
    the sum equals ``target_pages - capacity.fixed_pages``."""
    out = plan.model_copy(deep=True)
    if target_pages is None:
        target_pages = plan.target_pages
    out.target_pages = target_pages
    if not out.sections:
        return out
    weights = [section_weight(s, doc, capacity) for s in out.sections]
    if target_pages is None:
        # No target: natural length.
        for s, w in zip(out.sections, weights, strict=True):
            s.page_budget = round(w / max(capacity.words_per_body_page, 1.0), 4)
        return out
    available = max(target_pages - capacity.fixed_pages, 0)
    if available == 0:
        out.notes.append(f"target_pages={target_pages} leaves no body pages after {capacity.fixed_pages} fixed pages")
    total_w = sum(weights)
    budgets = [round(available * w / total_w, 4) for w in weights]
    budgets[-1] = round(available - sum(budgets[:-1]), 4)  # exact sum
    for s, b in zip(out.sections, budgets, strict=True):
        s.page_budget = max(b, 0.0)
    return out
