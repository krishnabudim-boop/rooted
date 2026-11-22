"""Traceability guards for ContentPlan (WP2). Pure functions, no LLM."""

from __future__ import annotations

from typing import Callable, Optional

from rooted.models import (
    Block,
    BlockType,
    Brief,
    ContentDoc,
    ContentPlan,
    EditPolicy,
    PlannedText,
    ProvenanceKind,
    Section,
    TextExcerpt,
)

MAX_LABEL_WORDS = 8

_QUOTES = {
    "‘": "'",
    "’": "'",
    "‚": "'",
    "‛": "'",
    "′": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "‟": '"',
    "″": '"',
    " ": " ",
    " ": " ",
    " ": " ",
    "​": "",
}


# --------------------------------------------------------------------------
# Text matching
# --------------------------------------------------------------------------


def _normalize_with_map(s: str) -> tuple[str, list[int]]:
    """Normalize quotes and collapse whitespace; ``map[i]`` is the original
    index of normalized char ``i``. Leading/trailing whitespace is stripped."""
    chars: list[str] = []
    idx: list[int] = []
    prev_space = True
    for i, ch in enumerate(s):
        ch = _QUOTES.get(ch, ch)
        if ch == "":
            continue
        if ch.isspace():
            if prev_space:
                continue
            ch = " "
            prev_space = True
        else:
            prev_space = False
        chars.append(ch)
        idx.append(i)
    while chars and chars[-1] == " ":
        chars.pop()
        idx.pop()
    return "".join(chars), idx


def _norm(s: str) -> str:
    return _normalize_with_map(s)[0]


def _locate(text: str, haystack: str) -> Optional[tuple[int, int]]:
    """Exact (start, end) of ``text`` in ``haystack``, trying exact then
    whitespace/quote-normalized matching."""
    if not text.strip():
        return None
    i = haystack.find(text)
    if i >= 0:
        return i, i + len(text)
    nh, hmap = _normalize_with_map(haystack)
    nt = _norm(text)
    if not nt:
        return None
    j = nh.find(nt)
    if j < 0:
        return None
    return hmap[j], hmap[j + len(nt) - 1] + 1


def _is_exact(ex: TextExcerpt, doc: ContentDoc) -> bool:
    try:
        full = doc.block(ex.block_id).text
    except KeyError:
        return False
    return 0 <= ex.start < ex.end <= len(full) and full[ex.start : ex.end] == ex.text and bool(ex.text.strip())


def _find_excerpt(ex: TextExcerpt, doc: ContentDoc) -> tuple[Optional[TextExcerpt], Optional[str]]:
    """Repaired excerpt (or None) plus a warning describing what was done."""
    if _is_exact(ex, doc):
        return ex, None
    ids = [b.id for b in doc.blocks]
    ordered = ([ex.block_id] if ex.block_id in ids else []) + [i for i in ids if i != ex.block_id]
    for bid in ordered:
        text = doc.block(bid).text
        hit = _locate(ex.text, text)
        if hit:
            fixed = TextExcerpt(block_id=bid, start=hit[0], end=hit[1], text=text[hit[0] : hit[1]])
            where = "offsets fixed" if bid == ex.block_id else f"moved from {ex.block_id} to {bid}"
            return fixed, f"excerpt {ex.text[:40]!r}: {where}"
    return None, f"excerpt {ex.text[:40]!r} (block {ex.block_id}) not found in the document"


def _n_words(s: str) -> int:
    return len(s.split())


# --------------------------------------------------------------------------
# repair_excerpts
# --------------------------------------------------------------------------


def _repair_planned(
    pt: Optional[PlannedText], doc: ContentDoc, warnings: list[str], what: str
) -> Optional[PlannedText]:
    """Repair a PlannedText. Verbatim text with an unfindable source is
    downgraded to a generated_label when short enough (honest provenance),
    otherwise dropped (None)."""
    if pt is None:
        return None
    if pt.provenance == ProvenanceKind.VERBATIM:
        src = pt.source
        if src is None:
            src = TextExcerpt(block_id="", start=0, end=0, text=pt.text)
        fixed, warn = _find_excerpt(src, doc)
        if fixed is not None:
            if warn:
                warnings.append(f"{what}: {warn}")
            return PlannedText(text=fixed.text, provenance=ProvenanceKind.VERBATIM, source=fixed)
        if _n_words(pt.text) <= MAX_LABEL_WORDS and pt.text.strip():
            warnings.append(f"{what}: {warn}; downgraded to generated_label")
            return PlannedText(text=pt.text, provenance=ProvenanceKind.GENERATED_LABEL, source=None)
        warnings.append(f"{what}: {warn}; dropped")
        return None
    if pt.provenance == ProvenanceKind.GENERATED_LABEL:
        if _n_words(pt.text) > MAX_LABEL_WORDS or not pt.text.strip():
            warnings.append(f"{what}: generated label longer than {MAX_LABEL_WORDS} words; dropped")
            return None
        if pt.source is not None:
            return PlannedText(text=pt.text, provenance=pt.provenance, source=None)
    return pt


def _map_planned(plan: ContentPlan, fn: Callable[[Optional[PlannedText], str], Optional[PlannedText]]) -> None:
    """Apply ``fn(planned_text, label)`` to every PlannedText slot in ``plan``
    (in place). ``cover.title`` is required: a None result leaves it to the caller."""
    cov = plan.cover
    if cov is not None:
        for fld in ("subtitle", "eyebrow", "byline", "date"):
            setattr(cov, fld, fn(getattr(cov, fld), f"cover.{fld}"))
    plan.back_cover_text = [
        r for i, t in enumerate(plan.back_cover_text) if (r := fn(t, f"back_cover_text[{i}]")) is not None
    ]
    for s in plan.sections:
        s.title = fn(s.title, f"{s.id}.title")
        for c in s.callouts:
            c.title = fn(c.title, f"{c.id}.title")


def _first_heading_text(doc: ContentDoc) -> Optional[tuple[str, TextExcerpt]]:
    for b in doc.blocks:
        if b.type == BlockType.HEADING and b.text.strip():
            t = b.text
            return t, TextExcerpt(block_id=b.id, start=0, end=len(t), text=t)
    return None


def repair_excerpts(plan: ContentPlan, doc: ContentDoc) -> tuple[ContentPlan, list[str]]:
    """Make every TextExcerpt exact: if ``text`` is found in the block (after
    whitespace/quote normalization) fix ``start``/``end``; if not found
    anywhere, drop the element (pull quote / stat / label). Returns the
    repaired plan and human-readable warnings."""
    out = plan.model_copy(deep=True)
    warnings: list[str] = []
    ids = set(doc.block_ids)

    if out.cover is not None:
        title = _repair_planned(out.cover.title, doc, warnings, "cover.title")
        if title is None:
            fb = _first_heading_text(doc)
            if fb is not None:
                title = PlannedText(text=fb[0], provenance=ProvenanceKind.VERBATIM, source=fb[1])
                warnings.append("cover.title: replaced by the first heading of the document")
        if title is None:
            warnings.append("cover.title unusable; cover removed")
            out.cover = None
        else:
            out.cover.title = title
    _map_planned(out, lambda pt, what: _repair_planned(pt, doc, warnings, what))

    for s in out.sections:
        pqs = []
        for q in s.pull_quotes:
            fixed, warn = _find_excerpt(q.excerpt, doc)
            if fixed is None:
                warnings.append(f"{q.id}: {warn}; pull quote dropped")
                continue
            if warn:
                warnings.append(f"{q.id}: {warn}")
            q.excerpt = fixed
            if q.anchor_block_id not in ids:
                q.anchor_block_id = fixed.block_id
            pqs.append(q)
        s.pull_quotes = pqs

        stats = []
        for k in s.key_stats:
            src, warn = _find_excerpt(k.source, doc)
            if src is None:
                warnings.append(f"{k.id}: {warn}; key stat dropped")
                continue
            if warn:
                warnings.append(f"{k.id}: {warn}")
            if k.value.strip() not in src.text:
                hit = _locate(k.value, src.text)
                if hit is None:
                    warnings.append(f"{k.id}: value {k.value!r} not in its source; key stat dropped")
                    continue
                k.value = src.text[hit[0] : hit[1]]
            label = _repair_planned(k.label, doc, warnings, f"{k.id}.label")
            if label is None:
                warnings.append(f"{k.id}: label unusable; key stat dropped")
                continue
            k.source, k.label = src, label
            if k.anchor_block_id not in ids:
                k.anchor_block_id = src.block_id
            stats.append(k)
        s.key_stats = stats
    return out, warnings


# --------------------------------------------------------------------------
# Coverage
# --------------------------------------------------------------------------


def _is_empty_paragraph(b: Block) -> bool:
    return b.type == BlockType.PARAGRAPH and b.table is None and b.image is None and not b.text.strip()


def _placements(plan: ContentPlan) -> list[tuple[str, str]]:
    """Every (block_id, where) placement claimed by the plan."""
    out: list[tuple[str, str]] = []
    if plan.cover is not None:
        out += [(i, "cover") for i in plan.cover.consumed_block_ids]
    for s in plan.sections:
        out += [(i, f"section {s.id}") for i in s.block_ids]
        for c in s.callouts:
            out += [(i, f"callout {c.id}") for i in c.block_ids]
    out += [(d.block_id, "dropped_blocks") for d in plan.dropped_blocks]
    return out


def _planned_texts(plan: ContentPlan) -> list[tuple[str, PlannedText]]:
    items: list[tuple[str, PlannedText]] = []
    if plan.cover is not None:
        items.append(("cover.title", plan.cover.title))
        for fld in ("subtitle", "eyebrow", "byline", "date"):
            v = getattr(plan.cover, fld)
            if v is not None:
                items.append((f"cover.{fld}", v))
    items += [(f"back_cover_text[{i}]", t) for i, t in enumerate(plan.back_cover_text)]
    for s in plan.sections:
        if s.title is not None:
            items.append((f"{s.id}.title", s.title))
        items += [(f"{c.id}.title", c.title) for c in s.callouts if c.title is not None]
        items += [(f"{k.id}.label", k.label) for k in s.key_stats]
    return items


def _fully_shown_on_cover(plan: ContentPlan, doc: ContentDoc, block_id: str) -> bool:
    """True when the cover shows all of the block's text: a verbatim cover field whose source is
    this block and whose text equals the whole block (or the block is the hero image)."""
    if plan.cover is None:
        return False
    b = next((x for x in doc.blocks if x.id == block_id), None)
    if b is None:
        return False
    if plan.cover.hero_image_block_id == block_id and b.type == BlockType.IMAGE:
        return True
    if b.type == BlockType.IMAGE:
        return False
    full = b.text.strip()
    for fld in ("title", "subtitle", "eyebrow", "byline", "date"):
        pt = getattr(plan.cover, fld)
        if (
            pt is not None
            and pt.provenance == ProvenanceKind.VERBATIM
            and pt.source is not None
            and pt.source.block_id == block_id
            and pt.text.strip() == full
        ):
            return True
    return False


def check_coverage(plan: ContentPlan, doc: ContentDoc, brief: Brief) -> list[str]:
    """Return violations of the coverage invariant (see ContentPlan docstring).
    Empty list = OK."""
    v: list[str] = []
    ids = set(doc.block_ids)
    by_id = {b.id: b for b in doc.blocks}

    seen: dict[str, str] = {}
    for bid, where in _placements(plan):
        if bid not in ids:
            v.append(f"unknown block id {bid} in {where}")
        elif bid in seen:
            v.append(f"block {bid} placed twice ({seen[bid]} and {where})")
        else:
            seen[bid] = where
    for b in doc.blocks:
        if b.id not in seen and not _is_empty_paragraph(b):
            v.append(f"block {b.id} is not placed anywhere (dropped without being listed)")
    if plan.cover is not None:
        for bid in plan.cover.consumed_block_ids:
            if bid in ids and not _fully_shown_on_cover(plan, doc, bid):
                v.append(f"cover consumes block {bid} but no verbatim cover field shows all of its text")

    if brief.edit_policy != EditPolicy.CONDENSE:
        for d in plan.dropped_blocks:
            if d.block_id in by_id and _is_empty_paragraph(by_id[d.block_id]):
                continue
            v.append(f"block {d.block_id} dropped but edit_policy={brief.edit_policy.value}")
    if brief.edit_policy == EditPolicy.VERBATIM:
        for r in plan.rewrites:
            v.append(f"rewrite of {r.block_id} but edit_policy=verbatim")
    for r in plan.rewrites:
        if r.block_id not in ids:
            v.append(f"rewrite refers to unknown block {r.block_id}")

    for s in plan.sections:
        if s.heading_block_id is not None and s.heading_block_id not in ids:
            v.append(f"section {s.id}: unknown heading_block_id {s.heading_block_id}")
        order = [doc.block_ids.index(i) for i in s.block_ids if i in ids]
        if order != sorted(order):
            v.append(f"section {s.id}: blocks are out of document order")
        for q in s.pull_quotes:
            if not _is_exact(q.excerpt, doc):
                v.append(f"pull quote {q.id}: excerpt is not an exact substring of {q.excerpt.block_id}")
            if q.anchor_block_id not in ids:
                v.append(f"pull quote {q.id}: unknown anchor {q.anchor_block_id}")
        for k in s.key_stats:
            if not _is_exact(k.source, doc):
                v.append(f"key stat {k.id}: source is not an exact substring of {k.source.block_id}")
            elif k.value not in k.source.text:
                v.append(f"key stat {k.id}: value {k.value!r} not in its source")
            if k.anchor_block_id not in ids:
                v.append(f"key stat {k.id}: unknown anchor {k.anchor_block_id}")
    if plan.cover is not None:
        h = plan.cover.hero_image_block_id
        if h is not None and (h not in ids or by_id[h].type != BlockType.IMAGE):
            v.append(f"cover hero_image_block_id {h} is not an image block of the document")

    for what, pt in _planned_texts(plan):
        if pt.provenance == ProvenanceKind.VERBATIM:
            if pt.source is None:
                v.append(f"{what}: verbatim text has no source excerpt")
            elif not _is_exact(pt.source, doc) or pt.source.text != pt.text:
                v.append(f"{what}: text {pt.text[:40]!r} is not an exact excerpt of {pt.source.block_id}")
        elif pt.provenance == ProvenanceKind.GENERATED_LABEL:
            if _n_words(pt.text) > MAX_LABEL_WORDS:
                v.append(f"{what}: generated label exceeds {MAX_LABEL_WORDS} words")
        elif brief.edit_policy == EditPolicy.VERBATIM:
            v.append(f"{what}: rewritten text but edit_policy=verbatim")
    return v


def repair_coverage(plan: ContentPlan, doc: ContentDoc, brief: Brief) -> ContentPlan:
    """Deterministic fix-up: orphaned blocks are inserted into the section of
    their nearest preceding placed block; duplicates keep the first
    occurrence; unpermitted drops/rewrites are reverted."""
    out = plan.model_copy(deep=True)
    ids = set(doc.block_ids)
    pos = {bid: i for i, bid in enumerate(doc.block_ids)}
    by_id = {b.id: b for b in doc.blocks}

    # 1. Revert unpermitted drops and rewrites.
    if brief.edit_policy != EditPolicy.CONDENSE:
        out.dropped_blocks = [
            d for d in out.dropped_blocks if d.block_id in by_id and _is_empty_paragraph(by_id[d.block_id])
        ]
    if brief.edit_policy == EditPolicy.VERBATIM:
        out.rewrites = []
    out.rewrites = [r for r in out.rewrites if r.block_id in ids]
    out.dropped_blocks = [d for d in out.dropped_blocks if d.block_id in ids]
    if brief.edit_policy == EditPolicy.VERBATIM:
        _revert_rewritten_text(out, doc)

    # 2. Remove invented ids and duplicates (first placement wins; within a
    #    section a callout beats the section list, since it was a deliberate move).
    placed: set[str] = set()

    def take(seq: list[str]) -> list[str]:
        keep = []
        for i in seq:
            if i in ids and i not in placed:
                placed.add(i)
                keep.append(i)
        return keep

    if out.cover is not None:
        # a block may leave the body only if the cover shows ALL of it; otherwise it is re-flowed as an orphan
        out.cover.consumed_block_ids = take(
            [i for i in out.cover.consumed_block_ids if i not in ids or _fully_shown_on_cover(out, doc, i)]
        )
        h = out.cover.hero_image_block_id
        if h is not None and (h not in ids or by_id[h].type != BlockType.IMAGE):
            out.cover.hero_image_block_id = None
    for s in out.sections:
        for c in s.callouts:
            c.block_ids = take(c.block_ids)
        s.callouts = [c for c in s.callouts if c.block_ids]
        s.block_ids = take(s.block_ids)
        if s.heading_block_id is not None and s.heading_block_id not in s.block_ids:
            s.heading_block_id = None
    out.dropped_blocks = [d for d in out.dropped_blocks if d.block_id not in placed]
    dropped = {d.block_id for d in out.dropped_blocks}
    seen_drop: set[str] = set()
    uniq = []
    for d in out.dropped_blocks:
        if d.block_id not in seen_drop:
            seen_drop.add(d.block_id)
            uniq.append(d)
    out.dropped_blocks = uniq

    # 3. Anchors of pull quotes and key stats must point at real blocks.
    for s in out.sections:
        for q in s.pull_quotes:
            if q.anchor_block_id not in ids:
                q.anchor_block_id = (
                    q.excerpt.block_id if q.excerpt.block_id in ids else (s.block_ids[0] if s.block_ids else "")
                )
        for k in s.key_stats:
            if k.anchor_block_id not in ids:
                k.anchor_block_id = (
                    k.source.block_id if k.source.block_id in ids else (s.block_ids[0] if s.block_ids else "")
                )
        s.pull_quotes = [q for q in s.pull_quotes if q.anchor_block_id in ids]
        s.key_stats = [k for k in s.key_stats if k.anchor_block_id in ids]

    # 4. Sort each section's blocks into document order.
    for s in out.sections:
        s.block_ids = sorted(s.block_ids, key=lambda i: pos[i])

    # 5. Re-insert orphans after their nearest preceding placed block.
    owner: dict[str, Optional[int]] = {}  # block id -> section index (None = cover)
    if out.cover is not None:
        owner.update({i: None for i in out.cover.consumed_block_ids})
    for si, s in enumerate(out.sections):
        owner.update({i: si for i in s.block_ids})
        owner.update({i: si for c in s.callouts for i in c.block_ids})
    for b in doc.blocks:
        if b.id in owner or b.id in dropped or _is_empty_paragraph(b):
            continue
        if not out.sections:
            out.sections.append(Section(id="s01", block_ids=[]))
        target = 0
        for prev in reversed(doc.blocks[: pos[b.id]]):
            if prev.id in owner and owner[prev.id] is not None:
                target = owner[prev.id]  # type: ignore[assignment]
                break
        sec = out.sections[target]
        at = 0
        for n, i in enumerate(sec.block_ids):
            if pos[i] < pos[b.id]:
                at = n + 1
        sec.block_ids.insert(at, b.id)
        owner[b.id] = target

    # 6. Drop sections left with nothing to show.
    out.sections = [s for s in out.sections if s.block_ids or s.callouts]
    return out


def _revert_rewritten_text(plan: ContentPlan, doc: ContentDoc) -> None:
    """Under edit_policy=verbatim, 'rewritten' planned text is not allowed:
    use its source excerpt verbatim, else mark it a (short) generated label,
    else drop it."""

    def fix(pt: Optional[PlannedText], _what: str) -> Optional[PlannedText]:
        if pt is None or pt.provenance != ProvenanceKind.REWRITTEN:
            return pt
        if pt.source is not None:
            fixed, _ = _find_excerpt(pt.source, doc)
            if fixed is not None:
                return PlannedText(text=fixed.text, provenance=ProvenanceKind.VERBATIM, source=fixed)
        if _n_words(pt.text) <= MAX_LABEL_WORDS and pt.text.strip():
            return PlannedText(text=pt.text, provenance=ProvenanceKind.GENERATED_LABEL, source=None)
        return None

    _map_planned(plan, fix)
    for s in plan.sections:
        for k in s.key_stats:
            lab = fix(k.label, "")
            if lab is None:
                lab = PlannedText(text=k.source.text, provenance=ProvenanceKind.VERBATIM, source=k.source)
            k.label = lab
    if plan.cover is not None and plan.cover.title.provenance == ProvenanceKind.REWRITTEN:
        t = fix(plan.cover.title, "cover.title")
        if t is None:
            fb = _first_heading_text(doc)
            t = PlannedText(text=fb[0], provenance=ProvenanceKind.VERBATIM, source=fb[1]) if fb else None
        if t is None:
            plan.cover = None
        else:
            plan.cover.title = t
