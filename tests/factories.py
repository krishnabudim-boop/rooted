"""Shared sample objects (architect-owned; do not edit in work packages).

Mutually consistent: the ContentPlan references real block ids of
sample_content_doc(), excerpts have exact offsets, the chart's numbers occur in
the text. Packages build on these so they can test without upstream stages.
Copy-and-modify (``model_copy(update=...)``) for edge cases in your own tests.
"""

from __future__ import annotations

from rooted.models import (
    BBox,
    Block,
    BlockType,
    Brief,
    Callout,
    ChartSeries,
    ChartSpec,
    ChartType,
    ColorOverride,
    ColorRole,
    ColorStat,
    ContentDoc,
    ContentPlan,
    CoverPlan,
    DataPoint,
    DesignSpec,
    FontCluster,
    Grid,
    InlineRun,
    KeyStat,
    LayoutElement,
    LayoutPlan,
    LayoutUnit,
    Margins,
    NormBBox,
    PageSize,
    PageTemplate,
    PageType,
    PaletteColor,
    PlannedText,
    ProvenanceKind,
    PullQuote,
    RawDesignStats,
    RawPageStats,
    Region,
    RenderedVisual,
    ReportProfile,
    RunningElement,
    RunningSlot,
    Section,
    TableCell,
    TableData,
    TextExcerpt,
    TypeRole,
    TypeStyle,
    VisualKind,
    VisualPlan,
    VisualSet,
    VisualSpec,
)


def _p(bid: str, text: str, btype: BlockType = BlockType.PARAGRAPH, level: int = 0, **kw) -> Block:
    return Block(id=bid, type=btype, level=level, runs=[InlineRun(text=text)], **kw)


def _cell(t: str) -> TableCell:
    return TableCell(runs=[InlineRun(text=t)])


def sample_content_doc() -> ContentDoc:
    blocks = [
        _p("b0001", "Annual Impact Report 2025", BlockType.HEADING, 1),
        _p("b0002", "Rooted Foundation"),
        _p("b0003", "Introduction", BlockType.HEADING, 1),
        Block(
            id="b0004",
            type=BlockType.PARAGRAPH,
            runs=[
                InlineRun(text="In 2025 we served "),
                InlineRun(text="12,400 families", bold=True),
                InlineRun(text=" across 3 regions, up 18% from 2024."),
            ],
        ),
        _p("b0005", "Our mission is to help every family put down roots in a safe and stable home."),
        _p("b0006", "Food security", BlockType.LIST_ITEM, 0),
        _p("b0007", "Housing support", BlockType.LIST_ITEM, 0),
        _p("b0008", "Results", BlockType.HEADING, 1),
        _p("b0009", "Program reach by region: North 5,200, Central 4,100 and South 3,100 families."),
        Block(
            id="b0010",
            type=BlockType.TABLE,
            table=TableData(
                rows=[
                    [_cell("Region"), _cell("Families")],
                    [_cell("North"), _cell("5,200")],
                    [_cell("Central"), _cell("4,100")],
                    [_cell("South"), _cell("3,100")],
                ],
                header_rows=1,
            ),
        ),
        _p("b0011", "Every family deserves a place to put down roots.", BlockType.QUOTE),
        _p("b0012", "Looking ahead", BlockType.HEADING, 2),
        _p("b0013", "Next year we will open two new centers and expand housing support."),
    ]
    words = sum(len(b.text.split()) for b in blocks)
    return ContentDoc(
        source_path="sample.docx",
        source_sha256="0" * 64,
        title="Annual Impact Report 2025",
        author="Rooted Foundation",
        blocks=blocks,
        word_count=words,
    )


def excerpt(doc: ContentDoc, block_id: str, text: str) -> TextExcerpt:
    """Exact excerpt helper: raises if ``text`` is not in the block."""
    full = doc.block(block_id).text
    start = full.index(text)
    return TextExcerpt(block_id=block_id, start=start, end=start + len(text), text=text)


def verbatim(doc: ContentDoc, block_id: str, text: str) -> PlannedText:
    return PlannedText(text=text, provenance=ProvenanceKind.VERBATIM, source=excerpt(doc, block_id, text))


def sample_brief() -> Brief:
    return Brief(
        raw_text="8 pages, navy and gold, executive tone",
        target_pages=8,
        palette_overrides=[
            ColorOverride(role=ColorRole.PRIMARY, hex="#1F2A44", name="navy"),
            ColorOverride(role=ColorRole.ACCENT, hex="#C9A227", name="gold"),
        ],
        tone="executive",
    )


def _style(role: TypeRole, size: float, weight: int = 400, color: ColorRole = ColorRole.TEXT, **kw) -> TypeStyle:
    return TypeStyle(role=role, source_font="Gotham", family="Montserrat", fallback_stack=["Arial", "sans-serif"],
                     size_pt=size, weight=weight, color_role=color, **kw)


def sample_design_spec() -> DesignSpec:
    """A4, 2-column body, navy/gold palette, Montserrat everywhere."""
    typography = [
        _style(TypeRole.DISPLAY, 40, 700, ColorRole.ON_PRIMARY, line_height=1.1),
        _style(TypeRole.TITLE, 30, 700, ColorRole.PRIMARY, line_height=1.1),
        _style(TypeRole.H1, 20, 700, ColorRole.PRIMARY, space_before_pt=18, space_after_pt=8),
        _style(TypeRole.H2, 14, 600, ColorRole.PRIMARY, space_before_pt=12, space_after_pt=6),
        _style(TypeRole.H3, 11, 600, ColorRole.TEXT, space_before_pt=8, space_after_pt=4),
        _style(TypeRole.BODY, 10, 400, line_height=1.45, space_after_pt=6),
        _style(TypeRole.LEAD, 13, 300, ColorRole.PRIMARY, line_height=1.35),
        _style(TypeRole.CAPTION, 8, 400, ColorRole.TEXT_MUTED),
        _style(TypeRole.HEADER, 8, 500, ColorRole.TEXT_MUTED, uppercase=True, letter_spacing_em=0.08),
        _style(TypeRole.FOOTER, 8, 400, ColorRole.TEXT_MUTED),
        _style(TypeRole.PULL_QUOTE, 16, 300, ColorRole.ACCENT, italic=True, line_height=1.3),
        _style(TypeRole.STAT_VALUE, 32, 700, ColorRole.ACCENT),
        _style(TypeRole.STAT_LABEL, 9, 500, ColorRole.TEXT),
        _style(TypeRole.TABLE_HEADER, 9, 600, ColorRole.ON_PRIMARY),
        _style(TypeRole.TABLE_BODY, 9, 400),
        _style(TypeRole.LABEL, 8, 700, ColorRole.ACCENT, uppercase=True, letter_spacing_em=0.1),
    ]
    palette = [
        PaletteColor(role=ColorRole.PRIMARY, hex="#1F2A44", weight=0.4, source="brief"),
        PaletteColor(role=ColorRole.ACCENT, hex="#C9A227", weight=0.1, source="brief"),
        PaletteColor(role=ColorRole.TEXT, hex="#222222", weight=0.3),
        PaletteColor(role=ColorRole.TEXT_MUTED, hex="#6B7280", weight=0.05),
        PaletteColor(role=ColorRole.BACKGROUND, hex="#FFFFFF", weight=0.1),
        PaletteColor(role=ColorRole.SURFACE, hex="#F3F1EA", weight=0.03),
        PaletteColor(role=ColorRole.RULE, hex="#D1D5DB", weight=0.01),
        PaletteColor(role=ColorRole.ON_PRIMARY, hex="#FFFFFF", weight=0.01),
    ]
    templates = [
        PageTemplate(
            page_type=PageType.COVER,
            background_role=ColorRole.PRIMARY,
            show_header=False,
            show_footer=False,
            regions=[
                Region(name="eyebrow", kind="text", bbox=NormBBox(x0=0.1, y0=0.12, x1=0.9, y1=0.16), style_role=TypeRole.LABEL),
                Region(name="title", kind="text", bbox=NormBBox(x0=0.1, y0=0.18, x1=0.9, y1=0.40), style_role=TypeRole.DISPLAY),
                Region(name="subtitle", kind="text", bbox=NormBBox(x0=0.1, y0=0.42, x1=0.9, y1=0.50), style_role=TypeRole.LEAD),
                Region(name="accent_band", kind="shape", bbox=NormBBox(x0=0.0, y0=0.85, x1=1.0, y1=0.88),
                       fill_role=ColorRole.ACCENT),
                Region(name="byline", kind="text", bbox=NormBBox(x0=0.1, y0=0.90, x1=0.9, y1=0.95),
                       style_role=TypeRole.STAT_LABEL),
            ],
            description="Full navy bleed, large white title upper third, thin gold band near bottom.",
        ),
        PageTemplate(
            page_type=PageType.SECTION_OPENER,
            columns=1,
            regions=[
                Region(name="number", kind="text", bbox=NormBBox(x0=0.1, y0=0.30, x1=0.5, y1=0.38), style_role=TypeRole.LABEL),
                Region(name="title", kind="text", bbox=NormBBox(x0=0.1, y0=0.38, x1=0.9, y1=0.55), style_role=TypeRole.TITLE),
            ],
        ),
        PageTemplate(page_type=PageType.BODY),
        PageTemplate(page_type=PageType.DATA, columns=1),
        PageTemplate(page_type=PageType.BACK_COVER, background_role=ColorRole.PRIMARY, show_header=False, show_footer=False),
    ]
    return DesignSpec(
        source_pdf="reference.pdf",
        source_sha256="1" * 64,
        page_size=PageSize(width_pt=595.28, height_pt=841.89, name="A4"),
        margins=Margins(top=64, right=56, bottom=64, left=56),
        grid=Grid(columns=2, gutter_pt=18),
        typography=typography,
        palette=palette,
        header=RunningElement(slots=[RunningSlot(position="left", content="{title}"),
                                     RunningSlot(position="right", content="{section}")],
                              style_role=TypeRole.HEADER, rule=True),
        footer=RunningElement(slots=[RunningSlot(position="outside", content="{page}")], style_role=TypeRole.FOOTER),
        templates=templates,
        motifs=["thin gold rules", "navy full-bleed cover"],
        style_summary="Corporate, restrained, navy with gold accents, geometric sans.",
    )


def sample_raw_stats() -> RawDesignStats:
    return RawDesignStats(
        source_pdf="reference.pdf",
        source_sha256="1" * 64,
        page_count=2,
        pages=[
            RawPageStats(index=0, width_pt=595.28, height_pt=841.89, char_count=60, image_area_ratio=0.0,
                         fill_area_ratio=1.0, column_estimate=1, dominant_fill="#1F2A44"),
            RawPageStats(index=1, width_pt=595.28, height_pt=841.89, text_bbox=BBox(x0=56, y0=64, x1=539, y1=778),
                         char_count=3200, image_area_ratio=0.1, fill_area_ratio=0.02, column_estimate=2, column_gutter_pt=18),
        ],
        font_clusters=[
            FontCluster(id="f01", font_name="Gotham-Book", size_pt=10, bold=False, italic=False, serif=False, monospace=False,
                        color="#222222", char_count=3000, page_indices=[1], sample_text="Lorem ipsum dolor sit amet"),
            FontCluster(id="f02", font_name="Gotham-Bold", size_pt=20, bold=True, italic=False, serif=False, monospace=False,
                        color="#1F2A44", char_count=40, page_indices=[1], sample_text="Section heading"),
            FontCluster(id="f03", font_name="Gotham-Bold", size_pt=40, bold=True, italic=False, serif=False, monospace=False,
                        color="#FFFFFF", char_count=20, page_indices=[0], sample_text="Report Title"),
        ],
        colors=[ColorStat(hex="#1F2A44", usage="fill", weight=0.6), ColorStat(hex="#222222", usage="text", weight=0.35),
                ColorStat(hex="#C9A227", usage="fill", weight=0.05)],
        rules=[],
        repeated_texts=[],
        margins_estimate=Margins(top=64, right=56, bottom=64, left=56),
        body_cluster_id="f01",
    )


def sample_content_plan(doc: ContentDoc | None = None) -> ContentPlan:
    doc = doc or sample_content_doc()
    return ContentPlan(
        profile=ReportProfile(
            purpose="Report annual program impact to donors",
            audience="Donors and board members",
            report_type="annual_report",
            tone="executive",
            summary="Nonprofit annual impact report with reach figures by region.",
        ),
        cover=CoverPlan(
            title=verbatim(doc, "b0001", "Annual Impact Report 2025"),
            byline=verbatim(doc, "b0002", "Rooted Foundation"),
            consumed_block_ids=["b0001", "b0002"],
        ),
        sections=[
            Section(
                id="s01",
                title=verbatim(doc, "b0003", "Introduction"),
                heading_block_id="b0003",
                block_ids=["b0003", "b0004", "b0005", "b0006", "b0007"],
                opener=True,
                page_budget=3.0,
                pull_quotes=[PullQuote(id="q01", excerpt=excerpt(doc, "b0005", "help every family put down roots"),
                                       anchor_block_id="b0005")],
                key_stats=[
                    KeyStat(id="k01", value="18%",
                            label=PlannedText(text="growth in families served", provenance=ProvenanceKind.GENERATED_LABEL),
                            source=excerpt(doc, "b0004", "up 18% from 2024"), anchor_block_id="b0004"),
                ],
            ),
            Section(
                id="s02",
                title=verbatim(doc, "b0008", "Results"),
                heading_block_id="b0008",
                block_ids=["b0008", "b0009", "b0010", "b0012", "b0013"],
                opener=True,
                page_type=PageType.DATA,
                page_budget=3.0,
                callouts=[Callout(id="c01", kind="highlight", block_ids=["b0011"])],
            ),
        ],
        target_pages=8,
    )


def sample_visual_set(doc: ContentDoc | None = None) -> VisualSet:
    doc = doc or sample_content_doc()
    pts = [("North", 5200, "5,200"), ("Central", 4100, "4,100"), ("South", 3100, "3,100")]
    chart = ChartSpec(
        chart_type=ChartType.COLUMN,
        title=PlannedText(text="Families served by region", provenance=ProvenanceKind.GENERATED_LABEL),
        categories=[p[0] for p in pts],
        series=[ChartSeries(name="Families",
                            points=[DataPoint(value=v, raw=r, source=excerpt(doc, "b0009", r)) for _, v, r in pts])],
        unit="families",
    )
    plan = VisualPlan(
        visuals=[
            VisualSpec(id="v01", kind=VisualKind.CHART, section_id="s02", anchor_block_id="b0009", placement="full_width",
                       size="large", priority="required", chart=chart, alt_text="Column chart of families served by region"),
        ],
        rationale="Regional figures are comparable quantities; one column chart.",
    )
    return VisualSet(plan=plan, rendered=[RenderedVisual(visual_id="v01", path="visuals/v01.svg", width_pt=483, height_pt=260)])


def sample_layout_plan() -> LayoutPlan:
    return LayoutPlan(
        units=[
            LayoutUnit(id="u01", page_type=PageType.COVER, fixed=True),
            LayoutUnit(id="u02", page_type=PageType.SECTION_OPENER, section_id="s01", fixed=True,
                       elements=[LayoutElement(id="e0001", kind="heading", block_ids=["b0003"])]),
            LayoutUnit(id="u03", page_type=PageType.BODY, section_id="s01", elements=[
                LayoutElement(id="e0002", kind="paragraph", block_ids=["b0004"]),
                LayoutElement(id="e0003", kind="key_stats", ref_id="k01", optional=True),
                LayoutElement(id="e0004", kind="paragraph", block_ids=["b0005"]),
                LayoutElement(id="e0005", kind="pull_quote", ref_id="q01", optional=True),
                LayoutElement(id="e0006", kind="list", block_ids=["b0006", "b0007"]),
            ]),
            LayoutUnit(id="u04", page_type=PageType.SECTION_OPENER, section_id="s02", fixed=True,
                       elements=[LayoutElement(id="e0007", kind="heading", block_ids=["b0008"])]),
            LayoutUnit(id="u05", page_type=PageType.DATA, section_id="s02", elements=[
                LayoutElement(id="e0008", kind="paragraph", block_ids=["b0009"]),
                LayoutElement(id="e0009", kind="visual", visual_id="v01"),
                LayoutElement(id="e0010", kind="table", block_ids=["b0010"]),
                LayoutElement(id="e0011", kind="callout", ref_id="c01", block_ids=["b0011"]),
                LayoutElement(id="e0012", kind="heading", block_ids=["b0012"], keep_with_next=True),
                LayoutElement(id="e0013", kind="paragraph", block_ids=["b0013"]),
            ]),
            LayoutUnit(id="u06", page_type=PageType.BACK_COVER, fixed=True),
        ],
        target_pages=8,
    )
