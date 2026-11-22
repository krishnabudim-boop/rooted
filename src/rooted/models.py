"""Shared data models for every Rooted pipeline stage.

This module is the contract between work packages. It is owned by the
architect; implementers must not edit it. Request changes through the
coordinator (see docs/PLAN.md, "Change control").

Conventions
-----------
* Units: all geometry is in PostScript points (1 pt = 1/72 in), origin at the
  top-left of the page, y grows downward (PyMuPDF convention).
* Colors: ``#RRGGBB`` uppercase hex strings (validated by ``HexColor``).
* IDs: stable, human-readable strings. Blocks: ``b0001``; sections ``s01``;
  callouts ``c01``; pull quotes ``q01``; key stats ``k01``; visuals ``v01``.
* Models marked **LLM output** are passed as structured-output schemas to
  ``LLMClient.structured``. They must stay schema-friendly: no ``dict`` fields,
  no ``Any``, no recursive types. Defaults are fine.
* Every stage artifact is serialized with ``model_dump_json(indent=2)`` into
  the work dir and reloaded with ``model_validate_json``.

Runtime syntax note: ``Optional``/``List`` are used instead of ``X | None`` so
the module also imports on older interpreters used by tooling; the project
itself targets Python 3.11+.
"""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator
from typing_extensions import Annotated

SCHEMA_VERSION = "1"

# --------------------------------------------------------------------------
# Primitives
# --------------------------------------------------------------------------

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _normalize_hex(value: str) -> str:
    m = _HEX_RE.match(value.strip())
    if not m:
        raise ValueError(f"not a hex color: {value!r}")
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.upper()


HexColor = Annotated[str, AfterValidator(_normalize_hex)]
"""``#RRGGBB`` color. Accepts ``#abc``/``abc``/``#aabbcc`` and normalizes."""


class RootedModel(BaseModel):
    """Base for all models: forbids unknown keys (keeps JSON artifacts honest
    and yields ``additionalProperties: false`` in structured-output schemas)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class BBox(RootedModel):
    """Axis-aligned rectangle in points, page coordinates, top-left origin."""

    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0


class NormBBox(RootedModel):
    """Rectangle as fractions (0..1) of the page width/height. Used for LLM
    vision output and page-template regions so they survive page-size changes."""

    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)

    @field_validator("x0", "y0", "x1", "y1", mode="before")
    @classmethod
    def _clamp(cls, v):  # type: ignore[no-untyped-def]
        # LLM output may overshoot slightly (1.02); the schema bounds are stripped for the API, so clamp here.
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return min(1.0, max(0.0, float(v)))
        return v


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------


class Stage(StrEnum):
    """Pipeline stages, in execution order. Artifact file = ``<value>.json``."""

    INGEST = "ingest"
    BRIEF = "brief"
    DESIGN = "design"
    CONTENT = "content"
    VISUALS = "visuals"
    LAYOUT = "layout"
    RENDER = "render"


STAGE_ORDER: List[Stage] = list(Stage)


class BlockType(StrEnum):
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    IMAGE = "image"
    CAPTION = "caption"
    QUOTE = "quote"
    PAGE_BREAK = "page_break"


class PageType(StrEnum):
    """Page archetypes a reference document is decomposed into."""

    COVER = "cover"
    TOC = "toc"
    SECTION_OPENER = "section_opener"
    BODY = "body"
    DATA = "data"  # chart/table heavy page
    FULL_VISUAL = "full_visual"  # full-bleed image / art page
    BACK_COVER = "back_cover"


class TypeRole(StrEnum):
    """Typographic roles. Every DesignSpec defines a TypeStyle for each."""

    DISPLAY = "display"  # cover title
    TITLE = "title"  # section opener title
    H1 = "h1"
    H2 = "h2"
    H3 = "h3"
    BODY = "body"
    LEAD = "lead"  # intro / standfirst paragraph
    CAPTION = "caption"
    HEADER = "header"  # running header
    FOOTER = "footer"  # running footer / folio
    PULL_QUOTE = "pull_quote"
    STAT_VALUE = "stat_value"
    STAT_LABEL = "stat_label"
    TABLE_HEADER = "table_header"
    TABLE_BODY = "table_body"
    LABEL = "label"  # small caps-ish labels, eyebrows, callout titles


class ColorRole(StrEnum):
    PRIMARY = "primary"
    SECONDARY = "secondary"
    ACCENT = "accent"
    ACCENT_2 = "accent_2"
    TEXT = "text"
    TEXT_MUTED = "text_muted"
    HEADING = "heading"
    BACKGROUND = "background"
    SURFACE = "surface"  # panel / callout fill
    RULE = "rule"
    ON_PRIMARY = "on_primary"  # text color on primary fills


class EditPolicy(StrEnum):
    """What the brief allows us to do to the user's words."""

    VERBATIM = "verbatim"  # default: no rewriting, no dropping
    LIGHT_EDIT = "light_edit"  # typo/whitespace fixes, heading shortening
    CONDENSE = "condense"  # may shorten/drop to hit page count (always traced)


class VisualsPolicy(StrEnum):
    NONE = "none"
    MINIMAL = "minimal"
    NORMAL = "normal"
    RICH = "rich"


class ProvenanceKind(StrEnum):
    VERBATIM = "verbatim"  # exact substring of a source block
    GENERATED_LABEL = "generated_label"  # short label written by Claude (<= 8 words)
    REWRITTEN = "rewritten"  # only permitted when EditPolicy != VERBATIM


# --------------------------------------------------------------------------
# Stage 1: Ingest  (docx -> ContentDoc)
# --------------------------------------------------------------------------


class InlineRun(RootedModel):
    """A run of text with uniform inline formatting."""

    text: str
    bold: bool = False
    italic: bool = False
    underline: bool = False
    superscript: bool = False
    subscript: bool = False
    code: bool = False
    link: Optional[str] = None


class TableCell(RootedModel):
    runs: List[InlineRun] = Field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1

    @property
    def text(self) -> str:
        return "".join(r.text for r in self.runs)


class TableData(RootedModel):
    rows: List[List[TableCell]]
    header_rows: int = 1
    caption: Optional[str] = None


class ImageRef(RootedModel):
    """An image extracted from the docx into ``<work_dir>/assets/``."""

    path: str  # relative to work dir
    mime: str
    width_px: int
    height_px: int
    alt_text: Optional[str] = None
    width_pt: Optional[float] = None  # display size from the docx wp:extent
    height_pt: Optional[float] = None


class Block(RootedModel):
    """One structural unit of the source document. ``id`` is stable and is the
    anchor for all traceability."""

    id: str
    type: BlockType
    level: int = 0  # heading level (1..6) or list nesting depth (0..)
    ordered: bool = False  # list items only
    list_id: Optional[str] = None  # docx numId (list items only)
    list_label: Optional[str] = None  # computed marker, e.g. "3." or "a)" (ordered items)
    runs: List[InlineRun] = Field(default_factory=list)
    table: Optional[TableData] = None
    image: Optional[ImageRef] = None
    style_name: Optional[str] = None  # original docx style, for debugging

    @property
    def text(self) -> str:
        """Plain text of the block (tables: cells joined by tab/newline)."""
        if self.table is not None:
            return "\n".join("\t".join(c.text for c in row) for row in self.table.rows)
        return "".join(r.text for r in self.runs)


class ContentDoc(RootedModel):
    """Output of ``rooted.ingest``. Artifact: ``ingest.json``."""

    schema_version: str = SCHEMA_VERSION
    source_path: str
    source_sha256: str
    title: Optional[str] = None  # docx core properties
    author: Optional[str] = None
    blocks: List[Block]
    word_count: int = 0

    def block(self, block_id: str) -> Block:
        for b in self.blocks:
            if b.id == block_id:
                return b
        raise KeyError(block_id)

    @property
    def block_ids(self) -> List[str]:
        return [b.id for b in self.blocks]


# --------------------------------------------------------------------------
# Stage 2a: Brief  (free text -> Brief)       LLM output: Brief
# --------------------------------------------------------------------------


class ColorOverride(RootedModel):
    role: ColorRole
    hex: str  # validated into HexColor by Brief consumers; LLM may emit "#1F2A44"
    name: Optional[str] = None  # e.g. "navy"


class FontOverride(RootedModel):
    role: Optional[TypeRole] = None  # None = all text roles
    family: str


class Brief(RootedModel):
    """Structured interpretation of the user's brief. **LLM output** (the
    ``raw_text`` field is filled in by code afterwards). Artifact: ``brief.json``.

    Anything the brief does not mention stays ``None`` and the reference
    design wins."""

    raw_text: str = ""
    target_pages: Optional[int] = None
    page_tolerance: int = 0  # accepted |actual - target|
    page_size: Optional[str] = None  # "A4", "Letter", "6x9in" ... None = reference
    palette_overrides: List[ColorOverride] = Field(default_factory=list)
    font_overrides: List[FontOverride] = Field(default_factory=list)
    tone: Optional[str] = None
    audience: Optional[str] = None
    edit_policy: EditPolicy = EditPolicy.VERBATIM
    visuals_policy: VisualsPolicy = VisualsPolicy.NORMAL
    include_cover: Optional[bool] = None
    include_back_cover: Optional[bool] = None
    include_toc: Optional[bool] = None
    extra_constraints: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Stage 3: Design extraction  (reference PDF -> RawDesignStats -> DesignSpec)
# --------------------------------------------------------------------------


class FontCluster(RootedModel):
    """Deterministic: spans grouped by (normalized font name, rounded size,
    weight, color)."""

    id: str  # "f01"
    font_name: str  # as in PDF, subset prefix stripped ("Montserrat-Bold")
    size_pt: float
    bold: bool
    italic: bool
    serif: bool
    monospace: bool
    color: HexColor
    char_count: int
    page_indices: List[int]
    sample_text: str  # <= 120 chars
    embedded: bool = True


class ColorStat(RootedModel):
    hex: HexColor
    usage: str  # "text" | "fill" | "stroke" | "image"
    weight: float  # share of usage-weighted area/chars, 0..1


class RuleStat(RootedModel):
    """A horizontal/vertical line or thin rect found via get_drawings()."""

    page_index: int
    bbox: BBox
    color: HexColor
    width_pt: float


class RepeatedText(RootedModel):
    """Header/footer candidate: text at a stable y-position across pages."""

    pattern: str  # digits replaced with '#'
    y_center_pt: float
    x_align: str  # "left" | "center" | "right"
    page_indices: List[int]
    cluster_id: Optional[str] = None


class FilledRect(RootedModel):
    """A filled, non-background rectangle measured on a reference page (callout panels, accent bars,
    table header bands). Full-page backgrounds are excluded (see ``RawPageStats.dominant_fill``)."""

    page_index: int
    bbox: BBox
    fill: HexColor


class RawPageStats(RootedModel):
    index: int
    width_pt: float
    height_pt: float
    text_bbox: Optional[BBox] = None  # union of body text spans
    char_count: int
    image_area_ratio: float
    fill_area_ratio: float  # colored rects
    column_estimate: int
    column_gutter_pt: Optional[float] = None
    dominant_fill: Optional[HexColor] = None
    png_path: Optional[str] = None  # rendered thumbnail, relative to work dir


class RawTextLine(RootedModel):
    """One horizontal text line measured on a reference page, tagged with its font cluster."""

    page_index: int
    bbox: BBox
    cluster_id: str
    chars: int = 0


class RawDesignStats(RootedModel):
    """Deterministic extraction (PyMuPDF only, no LLM). Artifact:
    ``design_raw.json``."""

    source_pdf: str
    source_sha256: str
    page_count: int
    pages: List[RawPageStats]
    font_clusters: List[FontCluster]
    colors: List[ColorStat]
    rules: List[RuleStat]
    repeated_texts: List[RepeatedText]
    margins_estimate: Margins
    body_cluster_id: Optional[str] = None  # cluster with most characters
    # Measured layout metrics; None when not measurable (consumers fall back to heuristics).
    body_line_height_pt: Optional[float] = None  # median baseline-to-baseline pitch of body text
    paragraph_spacing_pt: Optional[float] = None  # median extra gap between paragraphs, beyond the line pitch
    filled_boxes: List[FilledRect] = Field(default_factory=list)
    text_lines: List[RawTextLine] = Field(default_factory=list)  # per-page line bboxes + cluster ids (capped)


# ---- LLM output: vision analysis of rendered reference pages ----


class PageClassification(RootedModel):
    page_index: int
    page_type: PageType
    description: str


class RoleAssignment(RootedModel):
    cluster_id: str
    role: TypeRole


class ColorRoleAssignment(RootedModel):
    hex: str
    role: ColorRole


class FontCharacter(RootedModel):
    cluster_id: str
    classification: str  # serif | sans | slab | mono | script | display
    similar_open_fonts: List[str] = Field(default_factory=list)  # Google Fonts names


RegionName = Literal[
    "eyebrow", "title", "subtitle", "byline", "date", "number", "text", "body", "contact", "tagline", "back_text",
    "hero", "shape", "flow",
]
RegionKind = Literal["text", "image", "shape", "flow"]


class RegionNote(RootedModel):
    name: RegionName  # the names the layout reads; shapes/images/flow use "shape"/"hero"/"flow"
    kind: RegionKind
    bbox: NormBBox
    style_role: Optional[TypeRole] = None
    fill_hex: Optional[str] = None


class TemplateNote(RootedModel):
    page_type: PageType
    example_page_index: int
    columns: int
    background_hex: Optional[str] = None
    regions: List[RegionNote] = Field(default_factory=list)
    shows_header: bool = True
    shows_footer: bool = True


class VisionDesignNotes(RootedModel):
    """**LLM output** of the vision pass. Claude labels deterministic clusters
    and describes layout; it never supplies font sizes or page geometry (those
    come from RawDesignStats). Artifact: ``design_vision.json``."""

    overall_style: str
    page_classifications: List[PageClassification]
    role_assignments: List[RoleAssignment]
    color_roles: List[ColorRoleAssignment]
    font_characters: List[FontCharacter]
    templates: List[TemplateNote]
    callout_description: Optional[str] = None
    table_description: Optional[str] = None
    figure_description: Optional[str] = None
    motifs: List[str] = Field(default_factory=list)  # "diagonal gold rule", "rounded cards"


# ---- DesignSpec ----


class PageSize(RootedModel):
    width_pt: float
    height_pt: float
    name: Optional[str] = None  # "A4", "Letter", or None for custom


class Margins(RootedModel):
    top: float
    right: float
    bottom: float
    left: float


class Grid(RootedModel):
    columns: int = 1
    gutter_pt: float = 18.0
    baseline_pt: Optional[float] = None


class TypeStyle(RootedModel):
    role: TypeRole
    source_font: Optional[str] = None  # name found in reference PDF
    family: str  # CSS family to use (after font resolution)
    fallback_stack: List[str] = Field(default_factory=list)  # CSS fallbacks
    size_pt: float
    weight: int = 400  # 100..900
    italic: bool = False
    color_role: ColorRole = ColorRole.TEXT
    line_height: float = 1.3  # multiple of size
    letter_spacing_em: float = 0.0
    uppercase: bool = False
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0


class PaletteColor(RootedModel):
    role: ColorRole
    hex: HexColor
    weight: float = 0.0  # prominence in the reference, 0..1
    source: str = "extracted"  # extracted | vision | brief | default


class RunningSlot(RootedModel):
    """One slot of a running header/footer. ``content`` tokens:
    ``{page}``, ``{pages}``, ``{section}``, ``{title}``, or literal text."""

    position: str  # "left" | "center" | "right" | "outside" | "inside"
    content: str


class RunningElement(RootedModel):
    slots: List[RunningSlot] = Field(default_factory=list)
    style_role: TypeRole = TypeRole.FOOTER
    rule: bool = False  # hairline separating it from body
    rule_color_role: ColorRole = ColorRole.RULE
    y_center_pt: Optional[float] = None  # measured vertical center in the reference, from page top
    hidden_on: List[PageType] = Field(default_factory=lambda: [PageType.COVER, PageType.BACK_COVER])


class Region(RootedModel):
    """Fixed-position element of a page template (cover title box, color
    band, hero image...)."""

    name: str
    kind: str  # "text" | "image" | "shape" | "flow"
    bbox: NormBBox
    style_role: Optional[TypeRole] = None
    fill_role: Optional[ColorRole] = None


class PageTemplate(RootedModel):
    page_type: PageType
    margins: Optional[Margins] = None  # None = DesignSpec.margins
    columns: Optional[int] = None  # None = DesignSpec.grid.columns
    background_role: Optional[ColorRole] = None
    regions: List[Region] = Field(default_factory=list)
    show_header: bool = True
    show_footer: bool = True
    description: str = ""


class BoxStyle(RootedModel):
    """Callouts, sidebars, stat tiles."""

    fill_role: Optional[ColorRole] = ColorRole.SURFACE
    border_role: Optional[ColorRole] = None
    border_width_pt: float = 0.0
    border_sides: str = "all"  # all | left | top | bottom | none
    radius_pt: float = 0.0
    padding_pt: float = 12.0
    title_role: TypeRole = TypeRole.LABEL


class TableStyle(RootedModel):
    header_fill_role: Optional[ColorRole] = ColorRole.PRIMARY
    header_text_role: ColorRole = ColorRole.ON_PRIMARY
    zebra_fill_role: Optional[ColorRole] = None
    rule_role: ColorRole = ColorRole.RULE
    rule_width_pt: float = 0.5
    cell_padding_pt: float = 4.0


class FigureStyle(RootedModel):
    caption_position: str = "below"  # below | above | side
    border_role: Optional[ColorRole] = None
    series_roles: List[ColorRole] = Field(
        default_factory=lambda: [ColorRole.PRIMARY, ColorRole.ACCENT, ColorRole.SECONDARY, ColorRole.ACCENT_2]
    )
    gridlines: bool = True


class SpacingScale(RootedModel):
    paragraph_pt: float = 8.0
    section_pt: float = 28.0
    block_pt: float = 14.0  # around figures, callouts, tables


class FontFace(RootedModel):
    """A concrete font file to embed via @font-face."""

    family: str
    path: str  # absolute path or relative to work dir
    weight: int = 400
    italic: bool = False
    number: int = 0  # member index when ``path`` is a .ttc collection


class FontResolution(RootedModel):
    """How a reference font was mapped to something we can embed."""

    requested: str  # normalized source name, e.g. "Gotham"
    resolved_family: str  # e.g. "Montserrat"
    source: str  # embedded | system | google | bundled | generic
    match: str  # exact | metric_compatible | similar | generic
    faces: List[FontFace] = Field(default_factory=list)
    note: str = ""


class DesignSpec(RootedModel):
    """Complete visual system replicated from the reference PDF, with brief
    overrides applied. Artifact: ``design.json``."""

    schema_version: str = SCHEMA_VERSION
    source_pdf: str
    source_sha256: str
    page_size: PageSize
    margins: Margins
    grid: Grid = Field(default_factory=Grid)
    typography: List[TypeStyle]
    palette: List[PaletteColor]
    header: Optional[RunningElement] = None
    footer: Optional[RunningElement] = None
    templates: List[PageTemplate] = Field(default_factory=list)
    callout: BoxStyle = Field(default_factory=BoxStyle)
    pull_quote: BoxStyle = Field(
        default_factory=lambda: BoxStyle(fill_role=None, border_sides="left", border_width_pt=3, border_role=ColorRole.ACCENT)
    )
    stat_tile: BoxStyle = Field(default_factory=BoxStyle)
    table: TableStyle = Field(default_factory=TableStyle)
    figure: FigureStyle = Field(default_factory=FigureStyle)
    spacing: SpacingScale = Field(default_factory=SpacingScale)
    fonts: List[FontResolution] = Field(default_factory=list)
    motifs: List[str] = Field(default_factory=list)
    style_summary: str = ""
    overrides_applied: List[str] = Field(default_factory=list)  # human-readable log
    warnings: List[str] = Field(default_factory=list)

    def style(self, role: TypeRole) -> TypeStyle:
        for s in self.typography:
            if s.role == role:
                return s
        if role != TypeRole.BODY:
            return self.style(TypeRole.BODY)
        raise KeyError(role)

    def color(self, role: ColorRole) -> str:
        """Hex for a role. Missing roles fall back: accent/secondary -> primary,
        heading -> text, then neutral defaults."""
        by_role = {c.role: c.hex for c in self.palette}
        if role in by_role:
            return by_role[role]
        chain = {
            ColorRole.SECONDARY: ColorRole.PRIMARY,
            ColorRole.ACCENT: ColorRole.PRIMARY,
            ColorRole.ACCENT_2: ColorRole.ACCENT,
            ColorRole.HEADING: ColorRole.TEXT,
            ColorRole.PRIMARY: ColorRole.TEXT,
        }
        if role in chain:
            return self.color(chain[role])
        defaults = {
            ColorRole.TEXT: "#1A1A1A",
            ColorRole.TEXT_MUTED: "#666666",
            ColorRole.BACKGROUND: "#FFFFFF",
            ColorRole.SURFACE: "#F2F2F2",
            ColorRole.RULE: "#CCCCCC",
            ColorRole.ON_PRIMARY: "#FFFFFF",
        }
        return defaults[role]

    def template(self, page_type: PageType) -> Optional[PageTemplate]:
        for t in self.templates:
            if t.page_type == page_type:
                return t
        return None


# --------------------------------------------------------------------------
# Stage 2b: Content understanding   LLM output: ContentPlan
# --------------------------------------------------------------------------


class TextExcerpt(RootedModel):
    """A verbatim span of a source block. ``text`` MUST equal
    ``block.text[start:end]``; validators in rooted.content.validate enforce
    it and repair offsets when the text is found elsewhere in the block."""

    block_id: str
    start: int
    end: int
    text: str


class PlannedText(RootedModel):
    """Text that appears in the output but not as a normal flowing block
    (cover title, labels...). Provenance makes generated text auditable."""

    text: str
    provenance: ProvenanceKind = ProvenanceKind.VERBATIM
    source: Optional[TextExcerpt] = None  # required when provenance == verbatim


class ReportProfile(RootedModel):
    purpose: str
    audience: str
    report_type: str  # annual_report | white_paper | case_study | proposal | brochure | research_report | newsletter | other
    tone: str
    summary: str  # 1-2 sentences, internal use only (never printed)


class CoverPlan(RootedModel):
    title: PlannedText
    subtitle: Optional[PlannedText] = None
    eyebrow: Optional[PlannedText] = None  # e.g. "Annual Report 2025"
    byline: Optional[PlannedText] = None  # author / organization
    date: Optional[PlannedText] = None
    hero_image_block_id: Optional[str] = None  # user image to use as cover art
    consumed_block_ids: List[str] = Field(default_factory=list)  # blocks shown on cover and NOT repeated in body


class Callout(RootedModel):
    """Blocks *moved* (not copied) into a styled box."""

    id: str
    kind: str  # note | highlight | sidebar | warning | definition
    title: Optional[PlannedText] = None
    block_ids: List[str]


class PullQuote(RootedModel):
    """A duplicated excerpt displayed large. Body text stays in place."""

    id: str
    excerpt: TextExcerpt
    anchor_block_id: str  # render near this block


class KeyStat(RootedModel):
    id: str
    value: str  # verbatim number token from the text, e.g. "42%", "$3.1M"
    label: PlannedText  # what the number means (excerpt or short generated label)
    source: TextExcerpt  # sentence/clause containing the value
    anchor_block_id: str


class Section(RootedModel):
    id: str
    title: Optional[PlannedText] = None
    heading_block_id: Optional[str] = None
    block_ids: List[str]  # body blocks in reading order (incl. sub-headings)
    opener: bool = False  # gets a section_opener page/treatment
    page_type: PageType = PageType.BODY
    page_budget: float = 1.0  # pages allotted (may be fractional)
    callouts: List[Callout] = Field(default_factory=list)
    pull_quotes: List[PullQuote] = Field(default_factory=list)
    key_stats: List[KeyStat] = Field(default_factory=list)


class DroppedBlock(RootedModel):
    block_id: str
    reason: str


class Rewrite(RootedModel):
    block_id: str
    new_text: str
    reason: str


class ContentPlan(RootedModel):
    """**LLM output** (then validated/repaired in code). Organizes the user's
    blocks into sections and designates special elements. Artifact:
    ``content.json``.

    Invariant (checked by rooted.content.validate.check_coverage): every
    ContentDoc block id appears exactly once across cover.consumed_block_ids,
    section.block_ids, callout.block_ids and dropped_blocks; dropped_blocks and
    rewrites must be empty when edit_policy == verbatim (empty paragraphs
    excepted)."""

    schema_version: str = SCHEMA_VERSION
    profile: ReportProfile
    cover: Optional[CoverPlan] = None
    include_toc: bool = False
    sections: List[Section]
    back_cover_text: List[PlannedText] = Field(default_factory=list)
    dropped_blocks: List[DroppedBlock] = Field(default_factory=list)
    rewrites: List[Rewrite] = Field(default_factory=list)
    target_pages: Optional[int] = None
    notes: List[str] = Field(default_factory=list)


class PageCapacity(RootedModel):
    """Estimate derived from a DesignSpec, given to the content planner to
    budget text across pages (rooted.content.budget.estimate_capacity)."""

    words_per_body_page: float
    words_per_opener_page: float
    fixed_pages: int  # cover + back cover + toc


# --------------------------------------------------------------------------
# Stage 4: Visuals    LLM output: VisualPlan
# --------------------------------------------------------------------------


class VisualKind(StrEnum):
    CHART = "chart"
    DIAGRAM = "diagram"
    ICON = "icon"
    DECOR = "decor"
    COVER_ART = "cover_art"
    USER_IMAGE = "user_image"


class ChartType(StrEnum):
    BAR = "bar"  # horizontal
    COLUMN = "column"  # vertical
    STACKED_COLUMN = "stacked_column"
    LINE = "line"
    DONUT = "donut"
    STAT_TILES = "stat_tiles"  # big-number tiles, no axes


class DataPoint(RootedModel):
    """A number used in a chart, tied to where it appears in the source."""

    value: float
    raw: str  # as written: "3.1M", "42%"
    source: TextExcerpt


class ChartSeries(RootedModel):
    name: str
    points: List[DataPoint]


class ChartSpec(RootedModel):
    chart_type: ChartType
    title: Optional[PlannedText] = None
    categories: List[str]  # category labels, must come from the text
    series: List[ChartSeries]  # each series has len(categories) points
    unit: Optional[str] = None  # "%", "USD", "people"
    value_axis_label: Optional[str] = None
    highlight_category: Optional[int] = None


class DiagramNode(RootedModel):
    id: str
    label: PlannedText
    detail: Optional[PlannedText] = None


class DiagramEdge(RootedModel):
    source: str
    target: str
    label: Optional[str] = None


class DiagramSpec(RootedModel):
    kind: str  # process | cycle | timeline | hierarchy | comparison
    nodes: List[DiagramNode]
    edges: List[DiagramEdge] = Field(default_factory=list)


class DecorSpec(RootedModel):
    """Purely decorative, content-free shapes (bands, corner marks, patterns)."""

    motif: str  # band | corner | dots | waves | diagonal | grid
    color_roles: List[ColorRole]
    seed: int = 0


class VisualSpec(RootedModel):
    id: str
    kind: VisualKind
    section_id: Optional[str] = None  # None = cover / global
    anchor_block_id: Optional[str] = None
    placement: str = "inline"  # inline | full_width | full_page | sidebar | cover | opener | margin
    size: str = "medium"  # small | medium | large
    priority: str = "optional"  # required | optional (optional ones may be dropped by the fitter)
    chart: Optional[ChartSpec] = None
    diagram: Optional[DiagramSpec] = None
    decor: Optional[DecorSpec] = None
    icon_name: Optional[str] = None  # from rooted.visuals.icons.ICON_NAMES
    image_block_id: Optional[str] = None  # USER_IMAGE / photo COVER_ART
    caption: Optional[PlannedText] = None
    alt_text: str = ""


class VisualPlan(RootedModel):
    """**LLM output**: which visuals are warranted and their specs."""

    visuals: List[VisualSpec]
    rationale: str = ""


class RenderedVisual(RootedModel):
    visual_id: str
    path: str  # SVG (preferred) or PNG, relative to work dir
    width_pt: float
    height_pt: float


class RejectedVisual(RootedModel):
    visual_id: str
    reason: str  # e.g. "value 47 not found in block b0012"


class VisualSet(RootedModel):
    """Artifact: ``visuals.json``."""

    schema_version: str = SCHEMA_VERSION
    plan: VisualPlan
    rendered: List[RenderedVisual] = Field(default_factory=list)
    rejected: List[RejectedVisual] = Field(default_factory=list)

    def rendered_for(self, visual_id: str) -> Optional[RenderedVisual]:
        for r in self.rendered:
            if r.visual_id == visual_id:
                return r
        return None


# --------------------------------------------------------------------------
# Stage 5: Layout & render
# --------------------------------------------------------------------------


class FitParams(RootedModel):
    """Knobs the fitting loop turns. 1.0 = DesignSpec as extracted."""

    font_scale: float = 1.0
    spacing_scale: float = 1.0
    line_height_scale: float = 1.0
    visual_scale: float = 1.0
    margin_scale: float = 1.0
    opener_full_page: bool = True  # section openers as full pages vs. inline banners
    dropped_optional: List[str] = Field(default_factory=list)  # visual / pull-quote ids removed
    forced_breaks_before: List[str] = Field(default_factory=list)  # element ids


class FitBounds(RootedModel):
    """Hard limits so fitting never wrecks the design."""

    font_scale: List[float] = Field(default_factory=lambda: [0.9, 1.12])
    spacing_scale: List[float] = Field(default_factory=lambda: [0.6, 1.8])
    line_height_scale: List[float] = Field(default_factory=lambda: [0.92, 1.12])
    visual_scale: List[float] = Field(default_factory=lambda: [0.6, 1.5])
    margin_scale: List[float] = Field(default_factory=lambda: [0.85, 1.1])
    min_body_size_pt: float = 8.5


class LayoutElement(RootedModel):
    """One renderable thing in reading order."""

    id: str  # "e0001"
    kind: str  # heading | paragraph | list | table | image | visual | callout | pull_quote | key_stats | caption | spacer
    block_ids: List[str] = Field(default_factory=list)  # traceability
    visual_id: Optional[str] = None
    ref_id: Optional[str] = None  # callout / pull quote / stat id from ContentPlan
    keep_with_next: bool = False
    optional: bool = False  # fitter may drop


class LayoutUnit(RootedModel):
    """A run of pages sharing one page template. Fixed units (cover, opener,
    back cover) occupy exactly one page; ``flow`` units paginate naturally."""

    id: str  # "u01"
    page_type: PageType
    section_id: Optional[str] = None
    fixed: bool = False
    elements: List[LayoutElement] = Field(default_factory=list)


class LayoutPlan(RootedModel):
    """Artifact: ``layout.json``. Renderer-independent composition."""

    schema_version: str = SCHEMA_VERSION
    units: List[LayoutUnit]
    fit: FitParams = Field(default_factory=FitParams)
    target_pages: Optional[int] = None


class PageMapEntry(RootedModel):
    page_index: int  # 0-based
    unit_id: str
    element_ids: List[str]


class RenderResult(RootedModel):
    pdf_path: str
    html_path: str
    page_count: int
    page_map: List[PageMapEntry] = Field(default_factory=list)
    overflow_element_ids: List[str] = Field(default_factory=list)  # clipped/overflowing fixed regions
    warnings: List[str] = Field(default_factory=list)


class FitIteration(RootedModel):
    params: FitParams
    page_count: int
    action: str


class FitReport(RootedModel):
    target_pages: Optional[int]
    achieved_pages: int
    status: str  # exact | within_tolerance | over | under | no_target
    iterations: List[FitIteration] = Field(default_factory=list)
    final: FitParams


class TraceEntry(RootedModel):
    block_id: str
    status: str  # placed | cover | callout | dropped | rewritten
    element_ids: List[str] = Field(default_factory=list)
    pages: List[int] = Field(default_factory=list)  # 1-based page numbers


class BuildReport(RootedModel):
    """Artifact: ``render.json`` (and summary printed by the CLI)."""

    schema_version: str = SCHEMA_VERSION
    output_pdf: str
    render: RenderResult
    fit: FitReport
    trace: List[TraceEntry] = Field(default_factory=list)
    generated_text: List[PlannedText] = Field(default_factory=list)  # all non-verbatim printed text
    warnings: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Configuration & orchestration
# --------------------------------------------------------------------------


class LLMConfig(RootedModel):
    model: str = "claude-opus-5-5"
    max_tokens: int = 32000
    effort: str = "high"  # low | medium | high | xhigh | max (per-call override allowed)
    server_fallbacks: bool = True  # server-side refusal fallback ("default" chain)
    fallback_beta: str = "server-side-fallback-2026-07-01"
    timeout_s: float = 600.0
    max_retries: int = 3
    response_cache: bool = True  # disk cache of LLM responses in work dir


class BuildConfig(RootedModel):
    """Everything a build needs. Constructed by the CLI; passed to every stage
    entry point that needs settings."""

    content_path: Path
    reference_pdf: Path
    brief_text: str = ""
    output_path: Path = Path("out.pdf")
    work_dir: Path = Path(".rooted")
    stop_after: Optional[Stage] = None  # --stage
    from_stage: Optional[Stage] = None  # --from: force re-run from here
    force: bool = False
    dry_run: bool = False
    llm: LLMConfig = Field(default_factory=LLMConfig)
    fit_bounds: FitBounds = Field(default_factory=FitBounds)
    max_fit_iterations: int = 10
    renderer: str = "chromium"  # chromium (default) | weasyprint (optional extra)
    vision_max_pages: int = 8
    vision_dpi: int = 100
    reuse_embedded_fonts: bool = False  # licensing: off by default
    allow_font_download: bool = True
    font_cache_dir: Optional[Path] = None  # default: platformdirs user cache
    log_level: str = "INFO"
    seed: int = 0


class StageRecord(RootedModel):
    stage: Stage
    input_hash: str  # sha256 over inputs + upstream artifact hashes + config subset + stage version
    artifact: str  # filename in work dir
    artifact_sha256: str
    completed_at: str  # ISO 8601
    duration_s: float


class Manifest(RootedModel):
    """``<work_dir>/manifest.json``: what has been computed and from what."""

    schema_version: str = SCHEMA_VERSION
    records: List[StageRecord] = Field(default_factory=list)

    def get(self, stage: Stage) -> Optional[StageRecord]:
        for r in self.records:
            if r.stage == stage:
                return r
        return None


# Resolve forward references (RawDesignStats -> Margins).
RawDesignStats.model_rebuild()

# Models passed to LLMClient.structured(). Keep schema-friendly.
LLM_OUTPUT_MODELS = (Brief, ContentPlan, VisionDesignNotes, VisualPlan)

