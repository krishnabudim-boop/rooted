"""Build the traceability report (WP5)."""

from __future__ import annotations

from rooted.models import (
    ContentDoc,
    ContentPlan,
    LayoutPlan,
    PageType,
    PlannedText,
    ProvenanceKind,
    RenderResult,
    TraceEntry,
    VisualSet,
)


def build_trace(doc: ContentDoc, plan: ContentPlan, layout: LayoutPlan, result: RenderResult) -> list[TraceEntry]:
    """One TraceEntry per ContentDoc block: status + element ids + 1-based pages.

    Status: ``cover`` (shown on the cover), ``callout``, ``placed``,
    ``rewritten`` (plan rewrote it), ``dropped`` (plan dropped it), or
    ``missing`` (neither dropped nor present in the layout: a bug)."""
    dropped = {d.block_id for d in plan.dropped_blocks}
    rewritten = {r.block_id for r in plan.rewrites}
    callout_blocks = {b for s in plan.sections for c in s.callouts for b in c.block_ids}
    pages_of: dict[str, set[int]] = {}
    for entry in result.page_map:
        for eid in entry.element_ids:
            pages_of.setdefault(eid, set()).add(entry.page_index + 1)

    # block -> [(element, unit page type)], restricted to elements that really own the block
    owners: dict[str, list[tuple[str, PageType, str]]] = {}
    for u in layout.units:
        for e in u.elements:
            if e.kind in ("pull_quote", "key_stats"):
                continue  # duplicated excerpts, not the block's own placement
            for b in e.block_ids:
                owners.setdefault(b, []).append((e.id, u.page_type, e.kind))

    out: list[TraceEntry] = []
    for b in doc.blocks:
        els = owners.get(b.id, [])
        eids = [e for e, _, _ in els]
        pages = sorted({p for e in eids for p in pages_of.get(e, set())})
        if b.id in dropped:
            status = "dropped"
        elif not els:
            status = "missing"
        elif b.id in rewritten:
            status = "rewritten"
        elif any(pt == PageType.COVER for _, pt, _ in els):
            status = "cover"
        elif b.id in callout_blocks or any(k == "callout" for _, _, k in els):
            status = "callout"
        else:
            status = "placed"
        out.append(TraceEntry(block_id=b.id, status=status, element_ids=eids, pages=pages))
    return out


def _printed(plan: ContentPlan, visuals: VisualSet) -> list[PlannedText]:
    texts: list[PlannedText | None] = []
    c = plan.cover
    if c is not None:
        texts += [c.title, c.subtitle, c.eyebrow, c.byline, c.date]
    for s in plan.sections:
        texts.append(s.title)
        texts += [cal.title for cal in s.callouts]
        texts += [k.label for k in s.key_stats]
    texts += list(plan.back_cover_text)
    rendered = {r.visual_id for r in visuals.rendered}
    for v in visuals.plan.visuals:
        if v.id not in rendered:
            continue
        texts.append(v.caption)
        if v.chart is not None:
            texts.append(v.chart.title)
        if v.diagram is not None:
            for n in v.diagram.nodes:
                texts += [n.label, n.detail]
    return [t for t in texts if t is not None]


def collect_generated_text(plan: ContentPlan, visuals: VisualSet) -> list[PlannedText]:
    """Every printed PlannedText whose provenance != VERBATIM."""
    return [t for t in _printed(plan, visuals) if t.provenance != ProvenanceKind.VERBATIM]
