from __future__ import annotations

from rooted.layout.compose import compose
from rooted.layout.fit import optional_ids_by_priority
from rooted.models import FitParams, PageType


def _build(inputs, fit=None, brief=None):
    doc, plan, design, visuals, b, _ = inputs
    return compose(doc, plan, design, visuals, brief or b, fit)


def _kinds(unit):
    return [e.kind for e in unit.elements]


def test_unit_order(inputs):
    layout = _build(inputs)
    assert [u.page_type for u in layout.units] == [
        PageType.COVER,
        PageType.SECTION_OPENER,
        PageType.BODY,
        PageType.SECTION_OPENER,
        PageType.DATA,
        PageType.BACK_COVER,
    ]
    assert [u.fixed for u in layout.units] == [True, True, False, True, False, True]
    assert layout.target_pages == 8


def test_grouping_and_callout_replacement(inputs):
    layout = _build(inputs)
    body, data = layout.units[2], layout.units[4]
    lists = [e for e in body.elements if e.kind == "list"]
    assert len(lists) == 1 and lists[0].block_ids == ["b0006", "b0007"]
    callouts = [e for e in data.elements if e.kind == "callout"]
    assert len(callouts) == 1 and callouts[0].block_ids == ["b0011"] and callouts[0].ref_id == "c01"
    # the quote block is never a standalone element: it was moved into the callout
    assert not any(e.kind == "quote" for u in layout.units for e in u.elements)
    # the callout sits where its block was in the document: after the table
    kinds = _kinds(data)
    assert kinds.index("callout") == kinds.index("table") + 1


def test_extras_follow_their_anchor_and_are_optional(inputs):
    body = _build(inputs).units[2]
    ids = {e.id: e for e in body.elements}
    order = [(e.kind, e.block_ids[0] if e.block_ids else None) for e in body.elements]
    assert order.index(("paragraph", "b0004")) + 1 == [k for k, _ in order].index("key_stats")
    pq = [e for e in body.elements if e.kind == "pull_quote"][0]
    assert pq.optional and ids[pq.id].ref_id == "q01"
    assert body.elements.index(pq) == [e.block_ids[:1] for e in body.elements].index(["b0005"]) + 1


def test_visual_inserted_after_anchor_and_required(inputs):
    data = _build(inputs).units[4]
    kinds = _kinds(data)
    assert kinds[kinds.index("visual") - 1] == "paragraph"
    v = [e for e in data.elements if e.kind == "visual"][0]
    assert v.visual_id == "v01" and not v.optional


def test_headings_keep_with_next_and_ids_in_reading_order(inputs):
    layout = _build(inputs)
    h = [e for u in layout.units for e in u.elements if e.kind == "heading" and not u.fixed]
    assert h and all(e.keep_with_next for e in h)
    ids = [e.id for u in layout.units for e in u.elements]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_every_block_is_in_exactly_one_primary_element(inputs):
    doc = inputs[0]
    layout = _build(inputs)
    seen: dict[str, int] = {}
    for u in layout.units:
        for e in u.elements:
            if e.kind in ("pull_quote", "key_stats"):
                continue
            for b in e.block_ids:
                seen[b] = seen.get(b, 0) + 1
    assert sorted(seen) == sorted(doc.block_ids)
    assert set(seen.values()) == {1}


def test_dropped_optional_removes_only_those_and_keeps_ids_stable(inputs):
    full = _build(inputs)
    opt = optional_ids_by_priority(full)
    assert opt  # pull quote, key stat
    dropped = _build(inputs, FitParams(dropped_optional=opt[:1]))
    full_ids = {e.id for u in full.units for e in u.elements}
    left = {e.id for u in dropped.units for e in u.elements}
    assert full_ids - left == {opt[0]}


def test_optional_priority_order(inputs):
    full = _build(inputs)
    kinds = {e.id: e.kind for u in full.units for e in u.elements}
    order = [kinds[i] for i in optional_ids_by_priority(full)]
    assert order == ["pull_quote", "key_stats"]


def test_inline_openers_when_not_full_page(inputs):
    layout = _build(inputs, FitParams(opener_full_page=False))
    assert PageType.SECTION_OPENER not in [u.page_type for u in layout.units]
    first = layout.units[1].elements[0]
    assert first.kind == "heading" and first.ref_id == "opener" and first.block_ids == ["b0003"]


def test_no_cover_keeps_cover_blocks_as_front_matter(inputs):
    doc, plan, design, visuals, brief, _ = inputs
    layout = _build(inputs, brief=brief.model_copy(update={"include_cover": False}))
    assert layout.units[0].page_type == PageType.BODY
    placed = [b for u in layout.units for e in u.elements for b in e.block_ids]
    assert "b0001" in placed and "b0002" in placed


def test_no_back_cover_and_toc(inputs):
    brief = inputs[4].model_copy(update={"include_back_cover": False, "include_toc": True})
    layout = _build(inputs, brief=brief)
    assert layout.units[-1].page_type != PageType.BACK_COVER
    assert layout.units[1].page_type == PageType.TOC


def test_missing_rendered_visual_is_skipped(inputs):
    doc, plan, design, visuals, brief, _ = inputs
    visuals = visuals.model_copy(update={"rendered": []})
    layout = compose(doc, plan, design, visuals, brief)
    assert not any(e.kind in ("visual", "decor") for u in layout.units for e in u.elements)


def test_back_cover_only_when_design_has_template_or_brief_asks(inputs):
    doc, plan, design, visuals, brief, _ = inputs
    no_back = design.model_copy(update={"templates": [t for t in design.templates if t.page_type != PageType.BACK_COVER]})
    unset = brief.model_copy(update={"include_back_cover": None})
    assert PageType.BACK_COVER not in [u.page_type for u in compose(doc, plan, no_back, visuals, unset).units]
    assert PageType.BACK_COVER in [u.page_type for u in compose(doc, plan, no_back, visuals, unset.model_copy(
        update={"include_back_cover": True})).units]


def test_flowed_user_image_is_not_appended_a_second_time(inputs):
    from rooted.models import Block, BlockType, ImageRef, RenderedVisual, VisualKind, VisualPlan, VisualSpec

    doc, plan, design, visuals, brief, _ = inputs
    img = Block(id="b9999", type=BlockType.IMAGE,
                image=ImageRef(path="assets/x.png", mime="image/png", width_px=100, height_px=50))
    doc = doc.model_copy(update={"blocks": [*doc.blocks, img]})
    plan = plan.model_copy(deep=True)
    plan.sections[-1].block_ids.append(img.id)
    spec = VisualSpec(id="img_x", kind=VisualKind.USER_IMAGE, image_block_id=img.id, anchor_block_id=img.id)
    vs = visuals.model_copy(update={
        "plan": VisualPlan(visuals=[*visuals.plan.visuals, spec]),
        "rendered": [*visuals.rendered, RenderedVisual(visual_id="img_x", path="assets/x.png", width_pt=100, height_pt=50)],
    })
    layout = compose(doc, plan, design, vs, brief)
    elements = [e for u in layout.units for e in u.elements]
    assert not any(e.visual_id == "img_x" for e in elements)
    assert sum(img.id in e.block_ids for e in elements) == 1
