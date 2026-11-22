"""Deterministic diagram SVGs: process, cycle, timeline, hierarchy, comparison (WP4)."""


from __future__ import annotations

import math
from dataclasses import dataclass, field

from rooted.models import ColorRole, DiagramSpec
from rooted.visuals.svg import Theme, esc, f, svg_doc, wrap_text

LABEL_PT = 10.0
DETAIL_PT = 8.0
PAD = 8.0
GAP = 18.0


@dataclass
class _Node:
    id: str
    label: list[str]
    detail: list[str]
    w: float = 0.0
    h: float = 0.0
    cx: float = 0.0
    cy: float = 0.0
    level: int = 0
    children: list[str] = field(default_factory=list)


def _build_nodes(spec: DiagramSpec, node_w: float) -> list[_Node]:
    inner = node_w - 2 * PAD
    nodes = []
    for n in spec.nodes:
        nodes.append(_Node(
            id=n.id,
            label=wrap_text(n.label.text, inner, LABEL_PT, 0.58),
            detail=wrap_text(n.detail.text, inner, DETAIL_PT) if n.detail else [],
        ))
    h = max(2 * PAD + len(n.label) * LABEL_PT * 1.25 + (len(n.detail) * DETAIL_PT * 1.3 + 4 if n.detail else 0)
            for n in nodes)
    for n in nodes:
        n.w, n.h = node_w, max(h, 34.0)
    return nodes


def _edges(spec: DiagramSpec, nodes: list[_Node], cyclic: bool) -> list[tuple[str, str, str | None]]:
    if spec.edges:
        return [(e.source, e.target, e.label) for e in spec.edges]
    ids = [n.id for n in nodes]
    pairs = list(zip(ids, ids[1:], strict=False))
    if cyclic and len(ids) > 2:
        pairs.append((ids[-1], ids[0]))
    return [(a, b, None) for a, b in pairs]


def _clip(n: _Node, tx: float, ty: float) -> tuple[float, float]:
    """Point on node border toward (tx, ty)."""
    dx, dy = tx - n.cx, ty - n.cy
    if dx == 0 and dy == 0:
        return n.cx, n.cy
    sx = (n.w / 2) / abs(dx) if dx else math.inf
    sy = (n.h / 2) / abs(dy) if dy else math.inf
    s = min(sx, sy)
    return n.cx + dx * s, n.cy + dy * s


def _box(n: _Node, theme: Theme, *, header: bool = False) -> str:
    x, y = n.cx - n.w / 2, n.cy - n.h / 2
    primary, surf = theme.color(ColorRole.PRIMARY), theme.color(ColorRole.SURFACE)
    text, muted = theme.color(ColorRole.TEXT), theme.color(ColorRole.TEXT_MUTED)
    fam = esc(theme.family)
    if header:
        parts = [f'<rect x="{f(x)}" y="{f(y)}" width="{f(n.w)}" height="{f(n.h)}" rx="4" fill="{surf}" '
                 f'stroke="{theme.color(ColorRole.RULE)}" stroke-width="0.75"/>']
        label_h = 2 * PAD + len(n.label) * LABEL_PT * 1.25
        parts.append(f'<rect x="{f(x)}" y="{f(y)}" width="{f(n.w)}" height="{f(label_h)}" rx="4" fill="{primary}"/>')
        label_fill = theme.color(ColorRole.ON_PRIMARY)
        ty = y + PAD + LABEL_PT
    else:
        parts = [f'<rect x="{f(x)}" y="{f(y)}" width="{f(n.w)}" height="{f(n.h)}" rx="4" fill="{surf}" '
                 f'stroke="{primary}" stroke-width="1"/>']
        label_fill = primary
        block = len(n.label) * LABEL_PT * 1.25 + (len(n.detail) * DETAIL_PT * 1.3 + 4 if n.detail else 0)
        ty = n.cy - block / 2 + LABEL_PT * 0.95
    anchor_x = n.cx if not header else n.cx
    for i, line in enumerate(n.label):
        parts.append(f'<text x="{f(anchor_x)}" y="{f(ty + i * LABEL_PT * 1.25)}" text-anchor="middle" '
                     f'font-family="{fam}" font-size="{f(LABEL_PT)}" font-weight="600" fill="{label_fill}">{esc(line)}</text>')
    dy0 = ty + len(n.label) * LABEL_PT * 1.25 + (DETAIL_PT * 0.5 if not header else PAD)
    for i, line in enumerate(n.detail):
        parts.append(f'<text x="{f(anchor_x)}" y="{f(dy0 + i * DETAIL_PT * 1.3)}" text-anchor="middle" '
                     f'font-family="{fam}" font-size="{f(DETAIL_PT)}" fill="{muted if not header else text}">{esc(line)}</text>')
    return "".join(parts)


def _arrow_defs(theme: Theme) -> str:
    c = theme.color(ColorRole.ACCENT)
    return (f'<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
            f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" fill="{c}"/></marker></defs>')


def _edge_svg(a: _Node, b: _Node, label: str | None, theme: Theme) -> str:
    x1, y1 = _clip(a, b.cx, b.cy)
    x2, y2 = _clip(b, a.cx, a.cy)
    c = theme.color(ColorRole.ACCENT)
    out = (f'<line x1="{f(x1)}" y1="{f(y1)}" x2="{f(x2)}" y2="{f(y2)}" stroke="{c}" stroke-width="1.5" '
           f'marker-end="url(#arrow)"/>')
    if label:
        out += (f'<text x="{f((x1 + x2) / 2)}" y="{f((y1 + y2) / 2 - 3)}" text-anchor="middle" '
                f'font-family="{esc(theme.family)}" font-size="{f(DETAIL_PT)}" '
                f'fill="{theme.color(ColorRole.TEXT_MUTED)}">{esc(label)}</text>')
    return out


def _layout_process(nodes: list[_Node], width: float) -> float:
    per_row = max(1, min(len(nodes), int((width + GAP) // (nodes[0].w + GAP)), 4))
    rows = math.ceil(len(nodes) / per_row)
    used = per_row * nodes[0].w + (per_row - 1) * GAP
    x0 = (width - used) / 2
    h = nodes[0].h
    for i, n in enumerate(nodes):
        r, c = divmod(i, per_row)
        if r % 2 == 1:
            c = per_row - 1 - c  # snake so arrows stay short
        n.cx = x0 + n.w / 2 + c * (n.w + GAP)
        n.cy = h / 2 + r * (h + GAP * 1.4)
    return rows * h + (rows - 1) * GAP * 1.4


def _layout_cycle(nodes: list[_Node], width: float) -> float:
    n_ = len(nodes)
    w, h = nodes[0].w, nodes[0].h
    rx = max(w * 0.6, (width - w) / 2)
    ry = max(h * 1.4, rx * 0.62)
    if n_ > 6:
        ry = max(ry, h * n_ / 3)
    cx, cy = width / 2, ry + h / 2
    for i, n in enumerate(nodes):
        ang = -math.pi / 2 + 2 * math.pi * i / n_
        n.cx, n.cy = cx + rx * math.cos(ang), cy + ry * math.sin(ang)
    return 2 * ry + h


def _layout_timeline(nodes: list[_Node], width: float) -> tuple[float, float]:
    n_ = len(nodes)
    step = (width - nodes[0].w) / max(1, n_ - 1) if n_ > 1 else 0
    h = nodes[0].h
    axis = h + 14
    for i, n in enumerate(nodes):
        n.cx = nodes[0].w / 2 + i * step if n_ > 1 else width / 2
        n.cy = h / 2 if i % 2 == 0 else axis + 14 + h / 2
    return axis, axis + 14 + h


def _layout_hierarchy(nodes: list[_Node], spec: DiagramSpec, width: float) -> float:
    by_id = {n.id: n for n in nodes}
    incoming = {e.target for e in spec.edges}
    for e in spec.edges:
        by_id[e.source].children.append(e.target)
    roots = [n.id for n in nodes if n.id not in incoming] or [nodes[0].id]
    if not spec.edges:
        roots = [nodes[0].id]
        by_id[roots[0]].children = [n.id for n in nodes[1:]]
    levels: dict[int, list[_Node]] = {}
    seen: set[str] = set()
    frontier = [(r, 0) for r in roots]
    while frontier:
        nid, lv = frontier.pop(0)
        if nid in seen:
            continue
        seen.add(nid)
        by_id[nid].level = lv
        levels.setdefault(lv, []).append(by_id[nid])
        frontier += [(c, lv + 1) for c in by_id[nid].children]
    for n in nodes:  # unreachable (cycles): put on the last level
        if n.id not in seen:
            lv = max(levels) + 1 if levels else 0
            levels.setdefault(lv, []).append(n)
    h = nodes[0].h
    for lv, row in levels.items():
        slot = width / len(row)
        for i, n in enumerate(row):
            n.cx, n.cy = slot * (i + 0.5), h / 2 + lv * (h + GAP * 1.6)
    return len(levels) * h + (len(levels) - 1) * GAP * 1.6


def diagram_svg(spec: DiagramSpec, theme: Theme, width: float, title: str = "") -> tuple[str, float]:
    """Return ``(svg, height_pt)`` for a diagram laid out in ``width`` pt."""
    kind = spec.kind if spec.kind in ("process", "cycle", "timeline", "hierarchy", "comparison") else "process"
    n_ = len(spec.nodes)
    if kind == "comparison":
        gap = 10.0
        node_w = (width - gap * (n_ - 1)) / n_
    elif kind in ("process", "hierarchy"):
        node_w = min(150.0, max(80.0, (width - GAP * 3) / min(4, n_)))
    elif kind == "cycle":
        node_w = min(130.0, width / 3.2)
    else:
        node_w = min(130.0, max(70.0, width / max(2.0, n_ / 1.6)))
    nodes = _build_nodes(spec, node_w)
    by_id = {n.id: n for n in nodes}
    body = ""
    if kind == "comparison":
        h = max(n.h for n in nodes)
        for i, n in enumerate(nodes):
            n.h = h
            n.cx, n.cy = node_w / 2 + i * (node_w + 10.0), h / 2
        body = "".join(_box(n, theme, header=True) for n in nodes)
        height = h
    elif kind == "timeline":
        axis, height = _layout_timeline(nodes, width)
        line = theme.color(ColorRole.RULE)
        body = f'<line x1="0" y1="{f(axis)}" x2="{f(width)}" y2="{f(axis)}" stroke="{line}" stroke-width="2"/>'
        for n in nodes:
            above = n.cy < axis
            ey = n.cy + n.h / 2 if above else n.cy - n.h / 2
            body += (f'<line x1="{f(n.cx)}" y1="{f(ey)}" x2="{f(n.cx)}" y2="{f(axis)}" '
                     f'stroke="{line}" stroke-width="1"/>'
                     f'<circle cx="{f(n.cx)}" cy="{f(axis)}" r="4.5" fill="{theme.color(ColorRole.ACCENT)}"/>')
            body += _box(n, theme)
    else:
        if kind == "process":
            height = _layout_process(nodes, width)
        elif kind == "cycle":
            height = _layout_cycle(nodes, width)
        else:
            height = _layout_hierarchy(nodes, spec, width)
        edges = "".join(
            _edge_svg(by_id[s], by_id[t], lbl, theme)
            for s, t, lbl in _edges(spec, nodes, cyclic=(kind == "cycle")) if s in by_id and t in by_id
        )
        body = _arrow_defs(theme) + edges + "".join(_box(n, theme) for n in nodes)
    height = max(height, 20.0) + 2
    return svg_doc(width, height, body, title=title), height

