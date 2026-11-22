"""Never invent data (WP4).

Every number in a chart must be traceable to a number token present in the
user's own text. Visuals that fail any check are rejected, never repaired.
"""


from __future__ import annotations

import math
import re
from typing import Optional

from rooted.models import (
    BlockType,
    ChartSpec,
    ChartType,
    ContentDoc,
    DataPoint,
    DiagramSpec,
    PlannedText,
    ProvenanceKind,
    RejectedVisual,
    VisualKind,
    VisualPlan,
    VisualSpec,
)
from rooted.visuals.icons import ICON_NAMES

DECOR_MOTIFS = ("band", "corner", "dots", "waves", "diagonal", "grid")
MAX_LABEL_WORDS = 8

_CURRENCY = "$€£¥"
_SUFFIX = {
    "k": 1e3, "thousand": 1e3,
    "m": 1e6, "mm": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
    "t": 1e12, "trillion": 1e12,
}
_NUM_RE = re.compile(
    rf"^\s*[-+−]?\s*[{_CURRENCY}]?\s*(?P<num>\d[\d,]*(?:\.\d+)?|\.\d+)\s*(?P<suffix>%|[A-Za-z]+)?\s*$"
)
_DIGIT_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?")


def parse_number(raw: str) -> Optional[float]:
    """Parse "5,200", "42%", "$3.1M", "2.5 billion" -> float. Percent is NOT
    scaled ("42%" -> 42.0); k/M/B are ("3.1M" -> 3_100_000.0). None if the
    string is not a single number."""
    m = _NUM_RE.match(raw or "")
    if not m:
        return None
    try:
        value = float(m.group("num").replace(",", ""))
    except ValueError:
        return None
    suffix = (m.group("suffix") or "").lower()
    if suffix and suffix != "%":
        if suffix not in _SUFFIX:
            return None
        value *= _SUFFIX[suffix]
    if re.match(r"^\s*[-−]", raw):
        value = -value
    return value


def _close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=1e-6, abs_tol=1e-6)


def _token_in(raw: str, text: str) -> bool:
    """``raw`` occurs in ``text`` as a whole token (so "5" does not match the
    "5" inside "5,200" and "47" does not match "1947")."""
    raw = raw.strip()
    if not raw:
        return False
    pat = rf"(?<!\d)(?<!\d[.,]){re.escape(raw)}(?!\d)(?![.,]\d)"
    return re.search(pat, text) is not None


def _doc_text(doc: ContentDoc) -> str:
    return "\n".join(b.text for b in doc.blocks)


def _digits_ok(text: str, doc_text: str) -> bool:
    """Every digit token in a generated label must appear in the user's text."""
    return all(_token_in(t.rstrip(".,"), doc_text) for t in _DIGIT_TOKEN.findall(text))


def _check_point(p: DataPoint, doc: ContentDoc) -> Optional[str]:
    if not math.isfinite(p.value):
        return f"value {p.value} is not finite"
    try:
        block = doc.block(p.source.block_id)
    except KeyError:
        return f"source block {p.source.block_id} does not exist"
    btxt = block.text
    ex = p.source
    if btxt[ex.start:ex.end] != ex.text and ex.text not in btxt:
        return f"excerpt not found in block {block.id}"
    if p.raw not in ex.text:
        return f"raw {p.raw!r} not in its excerpt (block {block.id})"
    if not _token_in(p.raw, btxt):
        return f"value {p.raw} not found in block {block.id}"
    parsed = parse_number(p.raw)
    if parsed is None:
        return f"raw {p.raw!r} is not a parseable number"
    if not _close(parsed, p.value):
        return f"raw {p.raw!r} parses to {parsed:g}, not {p.value:g}"
    return None


def _check_planned(t: Optional[PlannedText], doc: ContentDoc, doc_text: str, what: str) -> Optional[str]:
    if t is None:
        return None
    if t.provenance == ProvenanceKind.VERBATIM:
        if t.text not in doc_text:
            return f"{what} {t.text!r} is marked verbatim but is not in the source text"
    elif t.provenance == ProvenanceKind.GENERATED_LABEL:
        if len(t.text.split()) > MAX_LABEL_WORDS:
            return f"{what} {t.text!r} is longer than {MAX_LABEL_WORDS} words"
        if not _digits_ok(t.text, doc_text):
            return f"{what} {t.text!r} contains a number not in the source text"
    else:
        return f"{what} {t.text!r} is rewritten text, not allowed in visuals"
    return None


def check_chart(chart: ChartSpec, doc: ContentDoc) -> Optional[str]:
    """Return a rejection reason, or None if the chart is traceable."""
    doc_text = _doc_text(doc)
    if not chart.categories:
        return "chart has no categories"
    if not chart.series:
        return "chart has no series"
    if chart.chart_type != ChartType.STAT_TILES and len(chart.categories) < 2:
        return "chart needs at least 2 data points"
    cited: set[str] = set()
    for s in chart.series:
        if len(s.points) != len(chart.categories):
            return (f"series {s.name!r} has {len(s.points)} points for "
                    f"{len(chart.categories)} categories")
        for cat, p in zip(chart.categories, s.points, strict=True):
            why = _check_point(p, doc)
            if why:
                return f"{cat}: {why}"
            if chart.chart_type == ChartType.DONUT and p.value < 0:
                return "donut charts cannot show negative values"
            cited.add(p.source.block_id)
    cited_text = "\n".join(doc.block(b).text for b in sorted(cited)).lower()
    for cat in chart.categories:
        if cat.strip().lower() not in cited_text:
            return f"category {cat!r} not present in the cited blocks"
    return _check_planned(chart.title, doc, doc_text, "chart title")


def check_diagram(diagram: DiagramSpec, doc: ContentDoc) -> Optional[str]:
    doc_text = _doc_text(doc)
    if not diagram.nodes:
        return "diagram has no nodes"
    ids = [n.id for n in diagram.nodes]
    if len(set(ids)) != len(ids):
        return "duplicate diagram node ids"
    for n in diagram.nodes:
        for what, t in (("node label", n.label), ("node detail", n.detail)):
            why = _check_planned(t, doc, doc_text, what)
            if why:
                return why
    for e in diagram.edges:
        if e.source not in ids or e.target not in ids:
            return f"edge {e.source}->{e.target} references an unknown node"
        if e.label and not _digits_ok(e.label, doc_text):
            return f"edge label {e.label!r} contains a number not in the source text"
    return None


def _check_visual(v: VisualSpec, doc: ContentDoc) -> Optional[str]:
    doc_text = _doc_text(doc)
    if v.anchor_block_id:
        try:
            doc.block(v.anchor_block_id)
        except KeyError:
            return f"anchor block {v.anchor_block_id} does not exist"
    why = _check_planned(v.caption, doc, doc_text, "caption")
    if why:
        return why
    if v.kind == VisualKind.CHART:
        return check_chart(v.chart, doc) if v.chart else "chart visual has no chart spec"
    if v.kind == VisualKind.DIAGRAM:
        return check_diagram(v.diagram, doc) if v.diagram else "diagram visual has no diagram spec"
    if v.kind == VisualKind.ICON:
        if v.icon_name not in ICON_NAMES:
            return f"unknown icon {v.icon_name!r}"
        return None
    if v.kind == VisualKind.DECOR:
        if v.decor is None:
            return "decor visual has no decor spec"
        if v.decor.motif not in DECOR_MOTIFS:
            return f"unknown decor motif {v.decor.motif!r}"
        return None
    if v.kind in (VisualKind.USER_IMAGE, VisualKind.COVER_ART):
        if v.kind == VisualKind.COVER_ART and v.image_block_id is None:
            if v.decor is None:
                return "cover art needs a decor spec or an image_block_id"
            if v.decor.motif not in DECOR_MOTIFS:
                return f"unknown decor motif {v.decor.motif!r}"
            return None
        try:
            b = doc.block(v.image_block_id or "")
        except KeyError:
            return f"image block {v.image_block_id} does not exist"
        if b.type != BlockType.IMAGE or b.image is None:
            return f"block {b.id} is not an image"
    return None


def validate_visual_plan(plan: VisualPlan, doc: ContentDoc) -> tuple[VisualPlan, list[RejectedVisual]]:
    """Reject any chart whose DataPoint.raw is not found in its source block
    text, whose parsed ``raw`` != ``value`` (tolerance 1e-6 after unit
    scaling: %, k, M, B), or whose categories are not present in the text.
    Diagram node labels with provenance VERBATIM must be exact substrings.
    Returns the surviving plan and the rejected visuals with reasons."""
    kept: list[VisualSpec] = []
    rejected: list[RejectedVisual] = []
    seen: set[str] = set()
    for v in plan.visuals:
        if v.id in seen:
            rejected.append(RejectedVisual(visual_id=v.id, reason="duplicate visual id"))
            continue
        seen.add(v.id)
        why = _check_visual(v, doc)
        if why:
            rejected.append(RejectedVisual(visual_id=v.id, reason=why))
        else:
            kept.append(v)
    return plan.model_copy(update={"visuals": kept}), rejected
