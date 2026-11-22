"""Decide which visuals are warranted (WP4)."""


from __future__ import annotations

import logging
import re

from rooted.errors import LLMRefusalError, LLMTruncatedError, LLMValidationError
from rooted.llm import TASK_VISUAL_PLAN, LLMClient
from rooted.models import (
    Block,
    BlockType,
    Brief,
    ContentDoc,
    ContentPlan,
    DesignSpec,
    VisualKind,
    VisualPlan,
    VisualSpec,
    VisualsPolicy,
)
from rooted.visuals.icons import ICON_NAMES
from rooted.visuals.prompts import VISUAL_PLAN_SYSTEM, build_visual_prompt

log = logging.getLogger(__name__)

_NUMBER = re.compile(
    r"(?<![\w.,])(?P<cur>[$€£¥])?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?P<unit>\s?%|\s?(?:thousand|million|billion|trillion)\b|[kKmMbB]\b)?"
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
MAX_CANDIDATES = 150
MAX_RETRY_TOKENS = 64000


def _sentence_around(text: str, start: int, end: int) -> str:
    s = max(text.rfind(c, 0, start) for c in ".!?\n") + 1
    m = _SENTENCE_END.search(text, end)
    e = m.start() if m else len(text)
    return text[s:e].strip()[:240]


def _kind(m: re.Match) -> str:
    unit = (m.group("unit") or "").strip()
    if unit == "%":
        return "percent"
    if m.group("cur"):
        return "currency"
    num = m.group("num")
    if re.fullmatch(r"(19|20)\d\d", num) and not unit:
        return "year"
    return "scaled" if unit else "count"


def find_numeric_candidates(doc: ContentDoc) -> list[dict]:
    """Regex scan for numbers with context (percentages, currency, years,
    counts, tables with numeric columns). Returned list is shown to the LLM
    so it charts only real numbers. Each item: ``block_id``, ``raw`` (token as
    written), ``kind`` (percent | currency | year | scaled | count |
    table_cell), ``context`` (sentence or table row), ``start``, ``end``."""
    out: list[dict] = []
    for b in doc.blocks:
        if b.type in (BlockType.IMAGE, BlockType.PAGE_BREAK):
            continue
        if b.type == BlockType.TABLE and b.table:
            header = [c.text.strip() for c in b.table.rows[0]] if b.table.rows else []
            text = b.text
            for ri, row in enumerate(b.table.rows):
                label = row[0].text.strip() if row else ""
                for ci, cell in enumerate(row):
                    ctext = cell.text.strip()
                    m = _NUMBER.fullmatch(ctext)
                    if not m or ri < b.table.header_rows:
                        continue
                    col = header[ci] if ci < len(header) and ri >= b.table.header_rows else ""
                    start = text.find(ctext)
                    out.append({"block_id": b.id, "raw": ctext, "kind": "table_cell",
                                "context": f"row {label!r}, column {col!r}", "start": start,
                                "end": start + len(ctext) if start >= 0 else -1})
            continue
        text = b.text
        for m in _NUMBER.finditer(text):
            raw = m.group(0).strip()
            out.append({"block_id": b.id, "raw": raw, "kind": _kind(m),
                        "context": _sentence_around(text, m.start(), m.end()),
                        "start": m.start(), "end": m.start() + len(raw)})
    return out


def _user_images(doc: ContentDoc) -> list[VisualSpec]:
    specs = []
    for b in doc.blocks:
        if b.type == BlockType.IMAGE and b.image is not None:
            specs.append(VisualSpec(
                id=f"img_{b.id}", kind=VisualKind.USER_IMAGE, anchor_block_id=b.id, image_block_id=b.id,
                placement="inline", size="large" if b.image.width_px >= 900 else "medium",
                priority="required", alt_text=b.image.alt_text or "",
            ))
    return specs


def _section_of(plan: ContentPlan) -> dict[str, str]:
    return {bid: s.id for s in plan.sections for bid in s.block_ids}


def _block_line(b: Block) -> str:
    text = " ".join(b.text.split())
    limit = 300 if b.type == BlockType.TABLE else 140
    if len(text) > limit:
        text = text[:limit] + "..."
    return f"[{b.id}|{b.type.value}|{len(b.text.split())}w] {text}"


def _prompt(doc: ContentDoc, plan: ContentPlan, design: DesignSpec, brief: Brief, cands: list[dict]) -> str:
    sections = []
    for s in plan.sections:
        title = s.title.text if s.title else s.id
        lines = "\n".join("  " + _block_line(doc.block(bid)) for bid in s.block_ids if bid in set(doc.block_ids))
        sections.append(f"## {s.id}: {title} (page budget {s.page_budget:g})\n{lines}")
    cand_lines = "\n".join(
        f"{c['block_id']} | {c['raw']} | {c['kind']} | {c['context']}" for c in cands[:MAX_CANDIDATES]
    )
    images = ", ".join(f"{b.id} ({b.image.alt_text or 'no alt'})" for b in doc.blocks if b.image)
    cover = "include a cover_art visual" if (plan.cover is not None) else "no cover"
    return build_visual_prompt(
        policy=brief.visuals_policy.value,
        tone=brief.tone or plan.profile.tone,
        section_lines="\n".join(sections),
        candidate_lines=cand_lines,
        images=images,
        motifs="; ".join(design.motifs),
        palette_roles=", ".join(c.role.value for c in design.palette),
        icon_names=", ".join(ICON_NAMES),
        cover_hint=cover,
    )


def _apply_budgets(visuals: list[VisualSpec], plan: ContentPlan, policy: VisualsPolicy) -> list[VisualSpec]:
    """At most one chart/diagram per page of section budget (min 1); minimal
    policy keeps 2 non-image visuals, charts and diagrams only."""
    budget = {s.id: max(1, int(s.page_budget)) for s in plan.sections}
    used: dict[str | None, int] = {}
    kept: list[VisualSpec] = []
    # required first so they win slots, then original order; restore order at the end
    order = sorted(range(len(visuals)), key=lambda i: (visuals[i].priority != "required", i))
    keep_idx: set[int] = set()
    extras = 0
    for i in order:
        v = visuals[i]
        if v.kind == VisualKind.USER_IMAGE:
            keep_idx.add(i)
            continue
        if policy == VisualsPolicy.MINIMAL:
            if v.kind not in (VisualKind.CHART, VisualKind.DIAGRAM) or extras >= 2:
                continue
        if v.kind in (VisualKind.CHART, VisualKind.DIAGRAM):
            cap = budget.get(v.section_id or "", 1)
            if used.get(v.section_id, 0) >= cap:
                log.info("dropping %s: section %s over its visual budget", v.id, v.section_id)
                continue
            used[v.section_id] = used.get(v.section_id, 0) + 1
        extras += 1
        keep_idx.add(i)
    for i, v in enumerate(visuals):
        if i in keep_idx:
            kept.append(v)
    return kept


def plan_visuals(doc: ContentDoc, plan: ContentPlan, design: DesignSpec, brief: Brief, llm: LLMClient) -> VisualPlan:
    """``llm.structured(task=TASK_VISUAL_PLAN, output_model=VisualPlan)``.
    VisualsPolicy.NONE -> skip the LLM, return only USER_IMAGE visuals for
    docx images. Respect per-section budget: <= 1 chart per page_budget.
    A refusal degrades to user images only. The LLM's output is NOT yet
    validated; run :func:`rooted.visuals.provenance.validate_visual_plan`."""
    images = _user_images(doc)
    if brief.visuals_policy == VisualsPolicy.NONE:
        return VisualPlan(visuals=images, rationale="visuals policy is none; user images only")

    prompt = _prompt(doc, plan, design, brief, find_numeric_candidates(doc))
    kwargs = dict(task=TASK_VISUAL_PLAN, system=VISUAL_PLAN_SYSTEM, output_model=VisualPlan, effort="medium")
    try:
        try:
            result = llm.structured(prompt=prompt, **kwargs)
        except LLMValidationError as e:
            result = llm.structured(
                prompt=f"{prompt}\nYour previous output failed validation: {e}", **kwargs)
        except LLMTruncatedError:
            result = llm.structured(prompt=prompt, max_tokens=MAX_RETRY_TOKENS, **kwargs)
    except LLMRefusalError as e:
        log.warning("visual planning refused (%s); using user images only", e)
        return VisualPlan(visuals=images, rationale=f"visual planning refused; user images only ({e})")

    llm_visuals = [v for v in result.visuals if v.kind != VisualKind.USER_IMAGE]
    sec_of = _section_of(plan)
    for v in llm_visuals:
        if v.section_id is None and v.anchor_block_id in sec_of and v.kind not in (
                VisualKind.COVER_ART,):
            v.section_id = sec_of[v.anchor_block_id]
    llm_visuals = _apply_budgets(llm_visuals, plan, brief.visuals_policy)
    taken = {v.id for v in llm_visuals}
    covered = {v.image_block_id for v in llm_visuals if v.image_block_id}
    extra = [im for im in images if im.image_block_id not in covered and im.id not in taken]
    return VisualPlan(visuals=llm_visuals + extra, rationale=result.rationale)
