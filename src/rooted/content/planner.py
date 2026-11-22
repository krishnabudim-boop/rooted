"""Content understanding (WP2): ContentDoc + Brief -> ContentPlan."""

from __future__ import annotations

import json
import logging
import math

from rooted.content._llm import structured_with_retry
from rooted.content.budget import assign_page_budgets, estimate_capacity
from rooted.content.prompts import CONTENT_PLAN_SYSTEM
from rooted.content.validate import check_coverage, repair_coverage, repair_excerpts
from rooted.errors import LLMError, StageError, TraceabilityError
from rooted.llm import TASK_CONTENT_PLAN, LLMClient
from rooted.models import (
    Block,
    BlockType,
    Brief,
    ContentDoc,
    ContentPlan,
    DesignSpec,
    EditPolicy,
    PageCapacity,
    PageType,
)

log = logging.getLogger("rooted.content.planner")

_EDIT_POLICY_TEXT = {
    EditPolicy.VERBATIM: "verbatim: do not drop, rewrite or reorder anything. "
    "dropped_blocks and rewrites must be empty.",
    EditPolicy.LIGHT_EDIT: "light_edit: rewrites allowed only for typos, whitespace and heading shortening; "
    "dropped_blocks must be empty.",
    EditPolicy.CONDENSE: "condense: you may drop or rewrite to fit the page target; list every change with a reason.",
}
_TABLE_PREVIEW_ROWS = 3


def _block_line(b: Block) -> str:
    words = len(b.text.split())
    kind = b.type.value + (str(b.level) if b.type == BlockType.HEADING else "")
    if b.type == BlockType.TABLE and b.table is not None:
        rows = b.table.rows
        shown = [" | ".join(c.text for c in r) for r in rows[:_TABLE_PREVIEW_ROWS]]
        more = f" ... (+{len(rows) - _TABLE_PREVIEW_ROWS} more rows)" if len(rows) > _TABLE_PREVIEW_ROWS else ""
        body = f"{len(rows)} rows x {len(rows[0]) if rows else 0} cols: " + " // ".join(shown) + more
    elif b.type == BlockType.IMAGE:
        alt = b.image.alt_text if b.image and b.image.alt_text else ""
        dims = f"{b.image.width_px}x{b.image.height_px}px" if b.image else ""
        body = f"(image {dims}) {alt}".strip()
    elif b.type == BlockType.PAGE_BREAK:
        body = "(page break)"
    else:
        body = " ".join(b.text.split())
    return f"[{b.id}|{kind}|{words}w] {body}"


def _design_summary(design: DesignSpec) -> dict:
    return {
        "page_size": design.page_size.name or f"{design.page_size.width_pt:.0f}x{design.page_size.height_pt:.0f}pt",
        "columns": design.grid.columns,
        "page_types_available": sorted({t.page_type.value for t in design.templates} | {PageType.BODY.value}),
        "templates": {t.page_type.value: t.description for t in design.templates if t.description},
    }


def build_prompt(doc: ContentDoc, brief: Brief, design: DesignSpec, capacity: PageCapacity) -> str:
    """The user prompt for TASK_CONTENT_PLAN (deterministic, sorted JSON)."""
    brief_json = brief.model_dump(mode="json")
    target = brief.target_pages
    ctx = {
        "design": _design_summary(design),
        "capacity": capacity.model_dump(mode="json"),
        "target_pages": target,
        "body_pages_available": None if target is None else max(target - capacity.fixed_pages, 0),
        "document_word_count": sum(len(b.text.split()) for b in doc.blocks),
        "document_title_property": doc.title,
        "document_author_property": doc.author,
    }
    lines = "\n".join(_block_line(b) for b in doc.blocks)
    return (
        f"Edit policy: {_EDIT_POLICY_TEXT[brief.edit_policy]}\n\n"
        f"Brief (JSON):\n{json.dumps(brief_json, sort_keys=True, indent=1)}\n\n"
        f"Context (JSON):\n{json.dumps(ctx, sort_keys=True, indent=1)}\n\n"
        f"Blocks, one per line as [id|type|words] text:\n{lines}\n\n"
        "Return the content plan. Every block id above must appear exactly once."
    )


def _fixed_pages(plan: ContentPlan, brief: Brief, design: DesignSpec, base: PageCapacity) -> PageCapacity:
    """Recount fixed pages now that the plan decided cover/back cover/TOC."""
    cover = int(plan.cover is not None and brief.include_cover is not False)
    if brief.include_back_cover is None:
        back = int(design.template(PageType.BACK_COVER) is not None and plan.cover is not None)
    else:
        back = int(brief.include_back_cover)
    toc = int(plan.include_toc)
    return base.model_copy(update={"fixed_pages": cover + back + toc})


def _postprocess_structure(plan: ContentPlan, brief: Brief, design: DesignSpec) -> None:
    """In-place conformance of the plan to brief and available templates."""
    if brief.include_cover is False and plan.cover is not None:
        plan.cover = None
        plan.notes.append("cover removed: brief says no cover")
    if brief.include_toc is not None and plan.include_toc != brief.include_toc:
        plan.include_toc = brief.include_toc
        plan.notes.append(f"include_toc forced to {brief.include_toc} by the brief")
    available = {t.page_type for t in design.templates} | {PageType.BODY}
    has_opener = PageType.SECTION_OPENER in available
    for s in plan.sections:
        if s.page_type not in available:
            plan.notes.append(f"{s.id}: page type {s.page_type.value} not in design; using body")
            s.page_type = PageType.BODY
        if s.opener and not has_opener:
            s.opener = False
            plan.notes.append(f"{s.id}: opener cleared, design has no section_opener template")
    plan.target_pages = brief.target_pages


def _cap_pull_quotes(plan: ContentPlan) -> None:
    body_pages = sum(s.page_budget for s in plan.sections)
    limit = max(1, math.ceil(body_pages / 2)) if body_pages > 0 else 1
    count = 0
    for s in plan.sections:
        kept = []
        for q in s.pull_quotes:
            count += 1
            if count <= limit:
                kept.append(q)
            else:
                plan.notes.append(f"{q.id}: pull quote dropped (limit of {limit} for ~{body_pages:.1f} body pages)")
        s.pull_quotes = kept


def plan_content(doc: ContentDoc, brief: Brief, design: DesignSpec, llm: LLMClient) -> ContentPlan:
    """One ``llm.structured(task=TASK_CONTENT_PLAN, output_model=ContentPlan)``
    call, then ``repair_excerpts`` -> ``repair_coverage`` -> ``assign_page_budgets``.

    The prompt shows blocks as ``[b0007|paragraph] text`` lines (tables
    abbreviated to first 3 rows), the Brief, the available page templates in
    ``design``, and the PageCapacity. Raises TraceabilityError if invariants
    still fail after repair.
    """
    if not doc.blocks:
        raise StageError("content", "the document has no content blocks")
    capacity = estimate_capacity(design, brief)
    prompt = build_prompt(doc, brief, design, capacity)
    try:
        plan = structured_with_retry(
            llm,
            task=TASK_CONTENT_PLAN,
            system=CONTENT_PLAN_SYSTEM,
            prompt=prompt,
            output_model=ContentPlan,
            effort="high",
        )
    except LLMError as e:
        raise StageError("content", f"content planning cannot proceed without the LLM: {e}") from e

    raw_violations = check_coverage(plan, doc, brief)
    plan, excerpt_warnings = repair_excerpts(plan, doc)
    plan = repair_coverage(plan, doc, brief)
    _postprocess_structure(plan, brief, design)
    # Cover/section changes may leave orphans (e.g. cover removed): repair again.
    plan = repair_coverage(plan, doc, brief)

    violations = check_coverage(plan, doc, brief)
    if violations:
        raise TraceabilityError(
            "content plan violates traceability invariants after repair: " + "; ".join(violations[:10])
        )

    for w in excerpt_warnings:
        log.warning("content: %s", w)
    plan.notes.extend(f"repaired: {w}" for w in excerpt_warnings)
    plan.notes.extend(f"repaired: {v}" for v in raw_violations)

    capacity = _fixed_pages(plan, brief, design, capacity)
    plan = assign_page_budgets(plan, doc, capacity, brief.target_pages)
    _cap_pull_quotes(plan)

    if brief.target_pages is not None:
        total_words = sum(len(b.text.split()) for b in doc.blocks)
        room = max(brief.target_pages - capacity.fixed_pages, 0) * capacity.words_per_body_page
        if total_words > room * 1.1:
            if brief.edit_policy == EditPolicy.CONDENSE:
                plan.notes.append(
                    f"over budget: {total_words} words vs about {room:.0f} that fit; "
                    "condensing is not implemented, text kept as is"
                )
            else:
                plan.notes.append(
                    f"over budget: {total_words} words vs about {room:.0f} that fit; "
                    "layout must reach the target with spacing and type knobs only"
                )
    return plan
