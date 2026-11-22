"""ContentPlan + VisualSet -> LayoutPlan (WP5). Pure, no rendering."""

from __future__ import annotations

from rooted.models import (
    Block,
    BlockType,
    Brief,
    ContentDoc,
    ContentPlan,
    DesignSpec,
    FitParams,
    LayoutElement,
    LayoutPlan,
    LayoutUnit,
    PageType,
    Section,
    VisualKind,
    VisualSet,
)

# Page types a flow unit may legitimately use.
_FLOW_TYPES = {PageType.BODY, PageType.DATA, PageType.FULL_VISUAL, PageType.TOC}


class _Counter:
    """Hands out element/unit ids. Ids are assigned *before* optional elements
    are dropped so they stay stable across fitting iterations."""

    def __init__(self) -> None:
        self.e = 0
        self.u = 0

    def element(self, kind: str, **kw) -> LayoutElement:
        return LayoutElement(id="", kind=kind, **kw)  # numbered in reading order by renumber()

    def unit(self, page_type: PageType, **kw) -> LayoutUnit:
        self.u += 1
        return LayoutUnit(id=f"u{self.u:02d}", page_type=page_type, **kw)


def _block_kind(block: Block) -> str:
    return {
        BlockType.HEADING: "heading",
        BlockType.PARAGRAPH: "paragraph",
        BlockType.LIST_ITEM: "list",
        BlockType.TABLE: "table",
        BlockType.IMAGE: "image",
        BlockType.CAPTION: "caption",
        BlockType.QUOTE: "quote",
        BlockType.PAGE_BREAK: "spacer",
    }[block.type]


def compose(
    doc: ContentDoc,
    plan: ContentPlan,
    design: DesignSpec,
    visuals: VisualSet,
    brief: Brief,
    fit: FitParams | None = None,
) -> LayoutPlan:
    """Units in order: cover (fixed) -> toc? -> per section [opener (fixed if
    fit.opener_full_page) -> flow] -> back cover (fixed). Elements: one per
    block (consecutive list items grouped into one ``list`` element), callouts
    replace their blocks, pull quotes / key stats / visuals inserted after
    their anchor block. Elements in ``fit.dropped_optional`` are omitted.

    Cover and back-cover text come from the plan; if the brief disables the
    cover, the cover's consumed blocks are flowed as ordinary front matter so
    no user text is lost."""
    fit = fit or FitParams()
    ids = _Counter()
    order = {b.id: i for i, b in enumerate(doc.blocks)}
    units: list[LayoutUnit] = []
    spec_by_id = {v.id: v for v in visuals.plan.visuals}

    def usable_visual(vid: str) -> bool:
        return visuals.rendered_for(vid) is not None

    # ---- cover / front matter
    cover = plan.cover
    use_cover = cover is not None and brief.include_cover is not False
    front_blocks: list[str] = []
    if use_cover:
        cu = ids.unit(PageType.COVER, fixed=True)
        cu.elements.append(ids.element("cover", block_ids=list(cover.consumed_block_ids)))
        for v in visuals.plan.visuals:
            if (v.kind == VisualKind.COVER_ART or v.placement == "cover") and usable_visual(v.id):
                cu.elements.append(ids.element("visual", visual_id=v.id))
        units.append(cu)
    elif cover is not None:
        front_blocks = list(cover.consumed_block_ids)

    # ---- toc
    want_toc = plan.include_toc if brief.include_toc is None else brief.include_toc
    if want_toc and any(s.title or s.heading_block_id for s in plan.sections):
        tu = ids.unit(PageType.TOC)
        tu.elements.append(ids.element("toc"))
        units.append(tu)

    if front_blocks:
        fu = ids.unit(PageType.BODY)
        fu.elements.extend(_block_elements(doc, front_blocks, ids))
        units.append(fu)

    # ---- sections
    placed_visual_ids: set[str] = set()
    for sec in plan.sections:
        units.extend(
            _section_units(doc, sec, design, visuals, spec_by_id, usable_visual, order, ids, fit, placed_visual_ids)
        )

    # visuals that belong to no section (and are not cover art) go at the end of the last flow unit.
    # A user image whose block is already flowed (or shown on the cover) must not be printed twice.
    flowed_blocks = {b for s in plan.sections for b in s.block_ids} | (
        set(cover.consumed_block_ids) if cover is not None else set()
    )
    leftovers = [
        v
        for v in visuals.plan.visuals
        if v.id not in placed_visual_ids
        and v.section_id is None
        and not (v.kind == VisualKind.USER_IMAGE and v.image_block_id in flowed_blocks)
        and v.kind != VisualKind.COVER_ART
        and v.placement != "cover"
        and usable_visual(v.id)
    ]
    if leftovers:
        flow = [u for u in units if not u.fixed and u.page_type != PageType.TOC]
        if flow:
            for v in leftovers:
                flow[-1].elements.append(_visual_element(ids, v))

    # ---- back cover
    # Same rule as content.planner._fixed_pages: an unset brief means "only if the reference has a back cover".
    if brief.include_back_cover is None:
        want_back = design.template(PageType.BACK_COVER) is not None and cover is not None
    else:
        want_back = brief.include_back_cover
    if want_back:
        bu = ids.unit(PageType.BACK_COVER, fixed=True)
        for i, pt in enumerate(plan.back_cover_text):
            bids = [pt.source.block_id] if pt.source is not None else []
            bu.elements.append(ids.element("back_text", ref_id=str(i), block_ids=bids))
        units.append(bu)

    n = 0
    for u in units:
        for e in u.elements:
            n += 1
            e.id = f"e{n:04d}"
    dropped = set(fit.dropped_optional)
    for u in units:
        u.elements = [e for e in u.elements if not (e.optional and e.id in dropped)]
    return LayoutPlan(units=units, fit=fit, target_pages=brief.target_pages or plan.target_pages)


def _visual_element(ids: _Counter, v) -> LayoutElement:
    decor = v.kind == VisualKind.DECOR
    return ids.element("decor" if decor else "visual", visual_id=v.id, optional=decor or v.priority != "required")


def _block_elements(doc: ContentDoc, block_ids: list[str], ids: _Counter) -> list[LayoutElement]:
    """One element per block; consecutive list items of the same list share one ``list`` element."""
    out: list[LayoutElement] = []
    i = 0
    while i < len(block_ids):
        b = doc.block(block_ids[i])
        if b.type == BlockType.LIST_ITEM:
            group = [b.id]
            key = (b.list_id, b.ordered)  # a new list (numId) or a bullet/number switch starts a new element
            while i + 1 < len(block_ids):
                nxt = doc.block(block_ids[i + 1])
                if nxt.type != BlockType.LIST_ITEM or (nxt.list_id, nxt.ordered) != key:
                    break
                i += 1
                group.append(block_ids[i])
            out.append(ids.element("list", block_ids=group))
        else:
            out.append(ids.element(_block_kind(b), block_ids=[b.id], keep_with_next=b.type == BlockType.HEADING))
        i += 1
    return out


def _section_units(doc, sec: Section, design, visuals, spec_by_id, usable_visual, order, ids, fit, placed_visual_ids):
    units: list[LayoutUnit] = []
    page_type = sec.page_type if sec.page_type in _FLOW_TYPES else PageType.BODY
    has_opener_heading = bool(sec.title or sec.heading_block_id)
    heading_id = sec.heading_block_id
    opener_vis = [
        v for v in visuals.plan.visuals if v.section_id == sec.id and v.placement == "opener" and usable_visual(v.id)
    ]

    flow_ids = (
        [b for b in sec.block_ids if b != heading_id] if (sec.opener and has_opener_heading) else list(sec.block_ids)
    )
    if sec.opener and has_opener_heading and fit.opener_full_page:
        ou = ids.unit(PageType.SECTION_OPENER, section_id=sec.id, fixed=True)
        ou.elements.append(ids.element("heading", block_ids=[heading_id] if heading_id else [], ref_id="opener"))
        for v in opener_vis:
            ou.elements.append(ids.element("visual", visual_id=v.id))
            placed_visual_ids.add(v.id)
        units.append(ou)
        opener_el = None
    elif sec.opener and has_opener_heading:
        # inline banner: the heading leads the flow, styled as an opener
        opener_el = ids.element(
            "heading", block_ids=[heading_id] if heading_id else [], ref_id="opener", keep_with_next=True
        )
    else:
        opener_el = None

    fu = ids.unit(page_type, section_id=sec.id)
    if opener_el is not None:
        fu.elements.append(opener_el)

    # extras keyed by the block they follow
    callout_ids = {b for c in sec.callouts for b in c.block_ids}
    after: dict[str, list[LayoutElement]] = {}
    leading: list[LayoutElement] = []
    tail: list[LayoutElement] = []
    in_flow = set(flow_ids)

    def attach(anchor: str | None, el: LayoutElement) -> None:
        if anchor is None:
            leading.append(el)
        elif anchor in in_flow:
            after.setdefault(anchor, []).append(el)
        else:
            tail.append(el)

    for c in sec.callouts:
        first = min(order.get(b, 10**9) for b in c.block_ids)
        prior = [b for b in flow_ids if order.get(b, -1) < first]
        attach(prior[-1] if prior else None, ids.element("callout", ref_id=c.id, block_ids=list(c.block_ids)))
    for q in sec.pull_quotes:
        attach(q.anchor_block_id, ids.element("pull_quote", ref_id=q.id, block_ids=[q.excerpt.block_id], optional=True))
    for k in sec.key_stats:
        attach(k.anchor_block_id, ids.element("key_stats", ref_id=k.id, block_ids=[k.source.block_id], optional=True))
    for v in visuals.plan.visuals:
        if v.section_id != sec.id or v.id in placed_visual_ids or not usable_visual(v.id):
            continue
        if v.kind == VisualKind.COVER_ART or v.placement in ("cover", "opener"):
            continue
        if v.kind == VisualKind.USER_IMAGE and v.image_block_id and v.image_block_id in in_flow:
            continue  # the image block itself is already flowed
        placed_visual_ids.add(v.id)
        attach(v.anchor_block_id, _visual_element(ids, v))

    fu.elements.extend(leading)
    plain = [b for b in flow_ids if b not in callout_ids]
    i = 0
    while i < len(plain):
        block = doc.block(plain[i])
        j = i + 1
        if block.type == BlockType.LIST_ITEM:
            while j < len(plain) and doc.block(plain[j]).type == BlockType.LIST_ITEM:
                j += 1
        group = plain[i:j]
        fu.elements.extend(_block_elements(doc, group, ids))
        for b in group:
            fu.elements.extend(after.get(b, []))
        i = j
    fu.elements.extend(tail)
    if fu.elements:
        units.append(fu)
    return units
