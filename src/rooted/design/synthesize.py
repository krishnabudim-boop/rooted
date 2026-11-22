"""Merge deterministic stats + vision notes into a DesignSpec (WP3)."""

from __future__ import annotations

import logging
import re
import statistics
from collections import Counter
from typing import Optional

from rooted.design import color as C
from rooted.models import (
    BoxStyle,
    ColorRole,
    DesignSpec,
    FigureStyle,
    FontCluster,
    Grid,
    Margins,
    NormBBox,
    PageSize,
    PageTemplate,
    PageType,
    PaletteColor,
    RawDesignStats,
    Region,
    RunningElement,
    RunningSlot,
    SpacingScale,
    TableStyle,
    TypeRole,
    TypeStyle,
    VisionDesignNotes,
)

log = logging.getLogger(__name__)

SCALE = 1.25
LADDER = [TypeRole.BODY, TypeRole.H3, TypeRole.H2, TypeRole.H1, TypeRole.TITLE, TypeRole.DISPLAY]
KNOWN_SIZES = {
    "A3": (841.89, 1190.55), "A4": (595.28, 841.89), "A5": (419.53, 595.28), "Letter": (612.0, 792.0),
    "Legal": (612.0, 1008.0), "Tabloid": (792.0, 1224.0),
}
_TEXT_ROLE_ORDER = [ColorRole.TEXT, ColorRole.PRIMARY, ColorRole.ACCENT, ColorRole.TEXT_MUTED, ColorRole.ON_PRIMARY,
                    ColorRole.SECONDARY, ColorRole.ACCENT_2, ColorRole.HEADING]
_HEX_ROLE_ORDER = [ColorRole.PRIMARY, ColorRole.ACCENT, ColorRole.SECONDARY, ColorRole.ACCENT_2, ColorRole.SURFACE,
                   ColorRole.RULE, ColorRole.TEXT, ColorRole.TEXT_MUTED, ColorRole.ON_PRIMARY, ColorRole.BACKGROUND]
_WEIGHT_HINTS = [("extralight", 200), ("ultralight", 200), ("extrabold", 800), ("ultrabold", 800), ("semibold", 600),
                 ("demibold", 600), ("demi", 600), ("light", 300), ("thin", 100), ("medium", 500), ("black", 900),
                 ("heavy", 900), ("bold", 700)]
_STYLE_WORDS = re.compile(
    r"(?i)[\s,_-]*(bold|italic|oblique|regular|book|roman|light|medium|semibold|demibold|black|heavy|thin|extrabold|"
    r"extralight|condensed|narrow|mt|ps)+$")
_LINE_HEIGHT = {
    TypeRole.DISPLAY: 1.1, TypeRole.TITLE: 1.15, TypeRole.H1: 1.2, TypeRole.H2: 1.2, TypeRole.H3: 1.25,
    TypeRole.BODY: 1.4, TypeRole.LEAD: 1.4, TypeRole.PULL_QUOTE: 1.35, TypeRole.STAT_VALUE: 1.0,
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def family_from_font_name(name: str) -> str:
    """'Gotham-Bold' -> 'Gotham', 'TimesNewRomanPS-BoldMT' -> 'Times New Roman', 'ArialMT' -> 'Arial'."""
    base = re.split(r"[-,]", name, maxsplit=1)[0]
    base = _STYLE_WORDS.sub("", base) or base
    if " " not in base:
        base = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)
    return base.strip() or name


def weight_from_font(c: FontCluster) -> int:
    n = c.font_name.lower().replace(" ", "")
    for hint, w in _WEIGHT_HINTS:
        if hint in n:
            return w
    return 700 if c.bold else 400


def fallback_stack(c: FontCluster, classification: Optional[str]) -> list[str]:
    cls = (classification or "").lower()
    if c.monospace or cls == "mono":
        return ["Courier New", "monospace"]
    if c.serif or cls in {"serif", "slab"}:
        return ["Georgia", "Times New Roman", "serif"]
    return ["Arial", "Helvetica", "sans-serif"]


def _round_half(v: float) -> float:
    return round(v * 2) / 2


def _page_size(raw: RawDesignStats) -> PageSize:
    sizes = Counter((round(p.width_pt, 1), round(p.height_pt, 1)) for p in raw.pages)
    (w, h), _ = sizes.most_common(1)[0] if sizes else ((595.28, 841.89), 1)
    name = None
    for nm, (kw, kh) in KNOWN_SIZES.items():
        if abs(w - kw) <= 2 and abs(h - kh) <= 2:
            name = nm
        elif abs(w - kh) <= 2 and abs(h - kw) <= 2:
            name = f"{nm} landscape"
    return PageSize(width_pt=w, height_pt=h, name=name)


def _role_for_hex(palette: dict[ColorRole, str], hexval: Optional[str], tol: float = 6.0) -> Optional[ColorRole]:
    if not hexval:
        return None
    for r in _HEX_ROLE_ORDER:
        if palette.get(r) == hexval:
            return r
    best = None
    for r in _HEX_ROLE_ORDER:
        if r in palette:
            d = C.delta_e(hexval, palette[r])
            if d <= tol and (best is None or d < best[0]):
                best = (d, r)
    return best[1] if best else None


def _text_role_for_hex(palette: dict[ColorRole, str], hexval: str) -> ColorRole:
    cands = [(C.delta_e(hexval, palette[r]), i, r) for i, r in enumerate(_TEXT_ROLE_ORDER) if r in palette]
    return min(cands)[2] if cands else ColorRole.TEXT


# --------------------------------------------------------------------------
# palette
# --------------------------------------------------------------------------


def _palette(raw: RawDesignStats, notes: Optional[VisionDesignNotes], body: Optional[FontCluster],
             h1: Optional[FontCluster], page_fill_bg: Optional[str]) -> list[PaletteColor]:
    weights: Counter = Counter()
    for c in raw.colors:
        weights[c.hex] += c.weight
    chosen: dict[ColorRole, tuple[str, str]] = {}  # role -> (hex, source)
    if notes:
        for cr in notes.color_roles:
            h = C.normalize_hex(cr.hex)
            if h and cr.role not in chosen:
                chosen[cr.role] = (h, "vision")

    def set_(role: ColorRole, hexval: str, source: str = "extracted") -> None:
        if role not in chosen:
            chosen[role] = (hexval, source)

    def hexes(usage: tuple[str, ...]) -> list[str]:
        return [c.hex for c in sorted(raw.colors, key=lambda c: (-c.weight, c.hex)) if c.usage in usage]

    text_hexes = hexes(("text",))
    # TEXT
    if body:
        set_(ColorRole.TEXT, body.color)
    elif text_hexes:
        set_(ColorRole.TEXT, text_hexes[0])
    else:
        set_(ColorRole.TEXT, "#1A1A1A", "default")
    text = chosen[ColorRole.TEXT][0]

    # BACKGROUND
    set_(ColorRole.BACKGROUND, page_fill_bg or "#FFFFFF", "extracted" if page_fill_bg else "default")
    bg = chosen[ColorRole.BACKGROUND][0]

    # PRIMARY: most common non-text, non-white fill, preferring non-light ones
    fills = hexes(("fill",))
    dark_fills = [h for h in fills if C.luminance(h) < 0.5 and h != bg]
    mid_fills = [h for h in fills if C.luminance(h) < 0.85 and h != bg]
    if ColorRole.PRIMARY not in chosen:
        if dark_fills:
            set_(ColorRole.PRIMARY, dark_fills[0])
        elif mid_fills:
            set_(ColorRole.PRIMARY, mid_fills[0])
        elif h1 and not C.is_neutral(h1.color):
            set_(ColorRole.PRIMARY, h1.color)
        else:
            sat = [h for h in hexes(("text", "stroke", "image")) if not C.is_neutral(h) and C.luminance(h) < 0.85]
            set_(ColorRole.PRIMARY, sat[0] if sat else "#1F3A5F", "extracted" if sat else "default")
    primary = chosen[ColorRole.PRIMARY][0]

    # ACCENT / SECONDARY / ACCENT_2: distinct saturated colors
    taken = [primary, text, bg]
    cands: list[str] = []
    for h in hexes(("fill", "stroke", "text", "image")):
        if (not C.is_neutral(h, 0.25) and 0.05 < C.luminance(h) < 0.9
                and all(C.delta_e(h, t) > 15 for t in taken + cands)):
            cands.append(h)
    for role in (ColorRole.ACCENT, ColorRole.SECONDARY, ColorRole.ACCENT_2):
        if role in chosen:
            continue
        if cands:
            set_(role, cands.pop(0))
        if role == ColorRole.ACCENT and role not in chosen:
            break

    # TEXT_MUTED
    muted = [h for h in text_hexes if h != text and C.delta_e(h, text) > 10 and C.luminance(h) < 0.8
             and C.is_neutral(h, 0.3)]
    if muted:
        set_(ColorRole.TEXT_MUTED, muted[0])
    else:
        set_(ColorRole.TEXT_MUTED, C.mix(text, bg, 0.4), "default")

    # SURFACE
    light = [h for h in fills if C.luminance(h) >= 0.85 and h != bg]
    if light:
        set_(ColorRole.SURFACE, light[0])
    else:
        set_(ColorRole.SURFACE, C.mix(primary, "#FFFFFF", 0.92), "default")

    # RULE
    rule_len: Counter = Counter()
    for r in raw.rules:
        rule_len[r.color] += max(r.bbox.width, r.bbox.height)
    rule_cands = [h for h, _ in sorted(rule_len.items(), key=lambda kv: (-kv[1], kv[0])) if C.delta_e(h, primary) > 10]
    if rule_cands:
        set_(ColorRole.RULE, rule_cands[0])
    else:
        set_(ColorRole.RULE, C.mix(text, bg, 0.8), "default")

    # ON_PRIMARY
    if ColorRole.ON_PRIMARY not in chosen:
        ok = [h for h in text_hexes if C.contrast(h, primary) >= 4.5]
        if ok:
            set_(ColorRole.ON_PRIMARY, ok[0])
        else:
            white = "#FFFFFF" if C.contrast("#FFFFFF", primary) >= C.contrast(text, primary) else text
            set_(ColorRole.ON_PRIMARY, white, "default")

    # HEADING
    if h1:
        set_(ColorRole.HEADING, h1.color)

    return [PaletteColor(role=r, hex=h, weight=round(min(weights.get(h, 0.0), 1.0), 4), source=src)
            for r, (h, src) in sorted(chosen.items(), key=lambda kv: list(ColorRole).index(kv[0]))]


# --------------------------------------------------------------------------
# type roles
# --------------------------------------------------------------------------


def _hf_priority(items: list, page_h: float) -> list:
    """Running-text candidates, best first: page-number patterns, then closest to the page edge."""
    return sorted(items, key=lambda r: ("#" not in r.pattern, min(r.y_center_pt, page_h - r.y_center_pt)))


def _assign_clusters(raw: RawDesignStats, notes: Optional[VisionDesignNotes], body: Optional[FontCluster]
                     ) -> dict[TypeRole, FontCluster]:
    by_id = {c.id: c for c in raw.font_clusters}
    assigned: dict[TypeRole, FontCluster] = {}
    if body:
        assigned[TypeRole.BODY] = body
    if notes:
        def _chars(r) -> int:  # type: ignore[no-untyped-def]
            return -by_id[r.cluster_id].char_count if r.cluster_id in by_id else 0

        for ra in sorted(notes.role_assignments, key=_chars):
            c = by_id.get(ra.cluster_id)
            if c is None or ra.role == TypeRole.BODY or ra.role in assigned:
                continue
            assigned[ra.role] = c
    # running header/footer from measured repeated text
    page_h = raw.pages[0].height_pt if raw.pages else 842
    for rt in _hf_priority(raw.repeated_texts, page_h):
        c = by_id.get(rt.cluster_id or "")
        role = TypeRole.HEADER if rt.y_center_pt < page_h / 2 else TypeRole.FOOTER
        if c and role not in assigned and (body is None or c.id != body.id):
            assigned[role] = c
    # heuristics for what is still unlabeled
    used = {c.id for c in assigned.values()}
    hf_ids = {rt.cluster_id for rt in raw.repeated_texts}
    if body:
        bsize = body.size_pt
        free = [c for c in raw.font_clusters if c.id not in used and c.id not in hf_ids]
        bigger: dict[float, FontCluster] = {}
        for c in sorted(free, key=lambda c: -c.char_count):
            if c.size_pt >= bsize * 1.08 and (c.bold or c.size_pt >= bsize * 1.3):
                bigger.setdefault(c.size_pt, c)
        sizes = sorted(bigger, reverse=True)
        if (not notes and TypeRole.DISPLAY not in assigned and len(sizes) >= 2
                and set(bigger[sizes[0]].page_indices) <= {0}):
            assigned[TypeRole.DISPLAY] = bigger[sizes.pop(0)]
        for role in (TypeRole.H1, TypeRole.H2, TypeRole.H3):
            if role not in assigned and sizes:
                assigned[role] = bigger[sizes.pop(0)]
        if TypeRole.CAPTION not in assigned:
            small = [c for c in free if c.size_pt < bsize * 0.95 and c.id not in {x.id for x in assigned.values()}]
            if small:
                assigned[TypeRole.CAPTION] = max(small, key=lambda c: c.char_count)
    return assigned


def _ladder_sizes(measured: dict[TypeRole, float]) -> dict[TypeRole, float]:
    sizes: dict[TypeRole, float] = {}
    for i, role in enumerate(LADDER):
        if role in measured:
            sizes[role] = measured[role]
            continue
        lo_i = max(j for j in range(i) if LADDER[j] in sizes)
        lo = sizes[LADDER[lo_i]]
        hi = next(((j, measured[LADDER[j]]) for j in range(i + 1, len(LADDER)) if LADDER[j] in measured), None)
        if hi and hi[1] > lo:
            sizes[role] = _round_half(lo * (hi[1] / lo) ** ((i - lo_i) / (hi[0] - lo_i)))
        else:
            sizes[role] = _round_half(lo * SCALE ** (i - lo_i))
    return sizes


_HEADING_LIKE = {TypeRole.H1, TypeRole.H2, TypeRole.H3, TypeRole.TITLE, TypeRole.DISPLAY, TypeRole.LABEL,
                 TypeRole.STAT_VALUE, TypeRole.STAT_LABEL, TypeRole.TABLE_HEADER}
_HEADING_WEIGHT_ROLES = (TypeRole.H1, TypeRole.H2, TypeRole.H3, TypeRole.TITLE, TypeRole.DISPLAY)


def _typography(raw: RawDesignStats, notes: Optional[VisionDesignNotes], assigned: dict[TypeRole, FontCluster],
                body: Optional[FontCluster], palette: dict[ColorRole, str], table: TableStyle) -> list[TypeStyle]:
    chars = {fc.cluster_id: fc.classification for fc in (notes.font_characters if notes else [])}
    base = body or FontCluster(id="f00", font_name="Helvetica", size_pt=10.0, bold=False, italic=False, serif=False,
                               monospace=False, color=palette[ColorRole.TEXT], char_count=0, page_indices=[],
                               sample_text="")
    bsize = base.size_pt
    sizes = _ladder_sizes({**{r: assigned[r].size_pt for r in LADDER if r in assigned}, TypeRole.BODY: bsize})
    cap_size = assigned[TypeRole.CAPTION].size_pt if TypeRole.CAPTION in assigned else _round_half(bsize * 0.8)
    accent_role = ColorRole.ACCENT if ColorRole.ACCENT in palette else ColorRole.PRIMARY
    heading_role = ColorRole.PRIMARY
    if TypeRole.H1 in assigned:
        heading_role = _text_role_for_hex(palette, assigned[TypeRole.H1].color)
    # (size, weight, color_role, italic, uppercase, letter_spacing) defaults for derived roles
    derived: dict[TypeRole, dict] = {
        TypeRole.H3: dict(weight=700, color_role=heading_role),
        TypeRole.H2: dict(weight=700, color_role=heading_role),
        TypeRole.H1: dict(weight=700, color_role=heading_role),
        TypeRole.TITLE: dict(weight=700, color_role=heading_role),
        TypeRole.DISPLAY: dict(weight=700, color_role=ColorRole.ON_PRIMARY),
        TypeRole.LEAD: dict(size=_round_half(bsize * SCALE), weight=400, color_role=ColorRole.TEXT),
        TypeRole.CAPTION: dict(size=cap_size, weight=400, color_role=ColorRole.TEXT_MUTED),
        TypeRole.HEADER: dict(size=cap_size, weight=400, color_role=ColorRole.TEXT_MUTED),
        TypeRole.FOOTER: dict(size=cap_size, weight=400, color_role=ColorRole.TEXT_MUTED),
        TypeRole.PULL_QUOTE: dict(size=_round_half(bsize * 1.6), weight=400, italic=True, color_role=accent_role),
        TypeRole.STAT_VALUE: dict(size=_round_half(bsize * 3.2), weight=700, color_role=accent_role),
        TypeRole.STAT_LABEL: dict(size=cap_size, weight=500, color_role=ColorRole.TEXT),
        TypeRole.TABLE_HEADER: dict(size=_round_half(bsize * 0.9), weight=600,
                                    color_role=table.header_text_role if table.header_fill_role else ColorRole.TEXT),
        TypeRole.TABLE_BODY: dict(size=_round_half(bsize * 0.9), weight=400, color_role=ColorRole.TEXT),
        TypeRole.LABEL: dict(size=cap_size, weight=700, color_role=accent_role, uppercase=True, letter_spacing_em=0.08),
    }
    heading_src = next((assigned[r] for r in (TypeRole.H1, TypeRole.DISPLAY, TypeRole.TITLE, TypeRole.H2,
                                              TypeRole.H3) if r in assigned), None)
    if heading_src is not None:
        for r in _HEADING_WEIGHT_ROLES:
            derived[r]["weight"] = weight_from_font(heading_src)
    out: list[TypeStyle] = []
    for role in TypeRole:
        c = assigned.get(role)
        kw = dict(derived.get(role, {}))
        src = c or (heading_src if role in _HEADING_LIKE and heading_src is not None else base)
        upper = bool(c and role != TypeRole.BODY and c.sample_text.isupper()
                     and sum(ch.isalpha() for ch in c.sample_text) >= 3)
        if c:
            size = c.size_pt
            kw.update(weight=weight_from_font(c), italic=c.italic, color_role=_text_role_for_hex(palette, c.color))
            if upper:
                kw.update(uppercase=True, letter_spacing_em=kw.get("letter_spacing_em", 0.05))
            elif role == TypeRole.LABEL:
                kw.update(uppercase=False, letter_spacing_em=0.0)
        elif role == TypeRole.BODY:
            size, kw = bsize, dict(weight=weight_from_font(base), italic=base.italic, color_role=ColorRole.TEXT)
        elif role in sizes:
            size = sizes[role]
        else:
            size = kw.pop("size")
        kw.pop("size", None)
        line_height = _LINE_HEIGHT.get(role, 1.3)
        if role == TypeRole.BODY:
            if raw.body_line_height_pt:  # measured pitch beats the heuristic
                line_height = min(max(round(raw.body_line_height_pt / size, 3), 1.0), 2.2)
            space_before = 0.0
            space_after = (_round_half(raw.paragraph_spacing_pt) if raw.paragraph_spacing_pt is not None
                           else _round_half(bsize * 0.6))
        elif role in (TypeRole.H1, TypeRole.H2, TypeRole.H3):
            mult = {TypeRole.H1: (1.0, 0.5), TypeRole.H2: (0.9, 0.45), TypeRole.H3: (0.8, 0.35)}[role]
            space_before, space_after = _round_half(size * mult[0]), _round_half(size * mult[1])
        else:
            space_before, space_after = 0.0, 0.0
        out.append(TypeStyle(
            role=role, source_font=src.font_name, family=family_from_font_name(src.font_name),
            fallback_stack=fallback_stack(src, chars.get(src.id)), size_pt=size,
            line_height=line_height,
            space_before_pt=space_before, space_after_pt=space_after, **kw))
    return out


# --------------------------------------------------------------------------
# running header/footer, templates, box styles
# --------------------------------------------------------------------------


def _slot_content(pattern: str) -> str:
    if "#" not in pattern:
        return "{title}"
    n = 0

    def repl(_m: re.Match) -> str:
        nonlocal n
        n += 1
        return "{page}" if n == 1 else "{pages}"

    return re.sub(r"#+", repl, pattern)


def _running(raw: RawDesignStats, top: bool, role: TypeRole) -> Optional[RunningElement]:
    page_h = raw.pages[0].height_pt if raw.pages else 842
    items = [r for r in raw.repeated_texts if (r.y_center_pt < page_h / 2) == top
             and r.cluster_id != raw.body_cluster_id]
    if not items:
        return None
    ycs = [r.y_center_pt for r in items]
    pages = {i for r in items for i in r.page_indices}
    has_rule = False
    for rl in raw.rules:
        wide = rl.bbox.width >= 0.4 * raw.pages[0].width_pt if raw.pages else False
        if (wide and rl.page_index in pages and min(abs((rl.bbox.y0 + rl.bbox.y1) / 2 - y) for y in ycs) <= 20
                and (rl.bbox.y0 > min(ycs)) == top):
            has_rule = True
            break
    slots = [RunningSlot(position=r.x_align, content=_slot_content(r.pattern)) for r in items]
    return RunningElement(slots=slots, style_role=role, rule=has_rule, y_center_pt=sum(ycs) / len(ycs))


_MEASURED_TYPES = (PageType.COVER, PageType.SECTION_OPENER, PageType.BACK_COVER)


def _measured_regions(raw: RawDesignStats, palette: dict[ColorRole, str], idx: int, page_type: PageType
                      ) -> list[Region]:
    """Cover / opener / back-cover regions from measured geometry: filled boxes become shapes; the largest
    text cluster becomes the title, the next text below it the subtitle (eyebrow / number above it)."""
    page = next((p for p in raw.pages if p.index == idx), None)
    if page is None:
        return []
    W, H = page.width_pt, page.height_pt

    def nb(x0: float, y0: float, x1: float, y1: float) -> NormBBox:
        c = lambda v, hi: min(1.0, max(0.0, v / hi))  # noqa: E731
        return NormBBox(x0=c(x0, W), y0=c(y0, H), x1=c(x1, W), y1=c(y1, H))

    regions: list[Region] = []
    shapes = sorted((b for b in raw.filled_boxes if b.page_index == idx),
                    key=lambda b: -(b.bbox.width * b.bbox.height))[:6]
    for b in sorted(shapes, key=lambda b: (b.bbox.y0, b.bbox.x0)):
        role = _role_for_hex(palette, b.fill)
        if role is not None:
            bb = b.bbox
            regions.append(Region(name="shape", kind="shape", bbox=nb(bb.x0, bb.y0, bb.x1, bb.y1), fill_role=role))
    size = {c.id: c.size_pt for c in raw.font_clusters}
    lines = sorted((ln for ln in raw.text_lines if ln.page_index == idx), key=lambda ln: (ln.bbox.y0, ln.bbox.x0))
    if not lines:
        return regions

    def block(first: int) -> tuple[list, int]:
        grp = [lines[first]]
        j = first + 1
        while (j < len(lines) and lines[j].cluster_id == grp[0].cluster_id
               and lines[j].bbox.y0 - grp[-1].bbox.y1 <= grp[-1].bbox.height):
            grp.append(lines[j])
            j += 1
        return grp, j

    def box(grp: list, y1_floor: Optional[float], min_lines: float) -> tuple[float, float, float, float]:
        x0, x1 = min(g.bbox.x0 for g in grp), max(g.bbox.x1 for g in grp)
        y0, y1 = grp[0].bbox.y0, grp[-1].bbox.y1
        if abs((x0 + x1) / 2 - W / 2) <= 0.05 * W:  # centered: stay symmetric, use the full measure
            half = max((x1 - x0) / 2, W / 2 - min(x0, W - x1))
            x0, x1 = W / 2 - half, W / 2 + half
        else:
            x1 = max(x1, W - x0)
        y1 = max(y1, y0 + min_lines * grp[0].bbox.height)
        if y1_floor is not None:
            y1 = max(min(y1, y1_floor - 4), grp[-1].bbox.y1)
        return x0, y0, x1, min(y1, H)

    if page_type == PageType.BACK_COVER:
        grp = lines
        regions.append(Region(name="text", kind="text", bbox=nb(min(g.bbox.x0 for g in grp), grp[0].bbox.y0,
                                                                  max(g.bbox.x1 for g in grp), grp[-1].bbox.y1),
                              style_role=TypeRole.BODY))
        return regions
    ti = max(range(len(lines)), key=lambda i: (size.get(lines[i].cluster_id, 0.0), -i))
    tgrp, after = block(ti)
    sub = lines[after] if after < len(lines) else None
    title_role = TypeRole.DISPLAY if page_type == PageType.COVER else TypeRole.TITLE
    regions.append(Region(name="title", kind="text", bbox=nb(*box(tgrp, sub.bbox.y0 if sub else None, 3.0)),
                          style_role=title_role))
    if sub is not None and page_type == PageType.COVER:
        sgrp, _ = block(after)
        regions.append(Region(name="subtitle", kind="text", bbox=nb(*box(sgrp, None, 2.5)), style_role=TypeRole.LEAD))
    above = [ln for ln in lines if ln.bbox.y1 <= tgrp[0].bbox.y0 and ln not in tgrp]
    if above:
        ln = above[-1]
        name, role = ("eyebrow", TypeRole.LABEL) if page_type == PageType.COVER else ("number", TypeRole.LABEL)
        regions.append(Region(name=name, kind="text", bbox=nb(*box([ln], tgrp[0].bbox.y0, 1.0)), style_role=role))
    return regions


def _templates(raw: RawDesignStats, notes: Optional[VisionDesignNotes], palette: dict[ColorRole, str], grid: Grid,
               header: Optional[RunningElement], footer: Optional[RunningElement]) -> list[PageTemplate]:
    by_idx = {p.index: p for p in raw.pages}
    half = raw.pages[0].height_pt / 2 if raw.pages else 421
    h_pages = {i for r in raw.repeated_texts if header and r.y_center_pt < half for i in r.page_indices}
    f_pages = {i for r in raw.repeated_texts if footer and r.y_center_pt >= half for i in r.page_indices}

    def bg_role(idx: int, notes_hex: Optional[str]) -> Optional[ColorRole]:
        p = by_idx.get(idx)
        hexv = p.dominant_fill if p and p.dominant_fill and p.fill_area_ratio >= 0.6 else notes_hex
        role = _role_for_hex(palette, hexv)
        return None if role in (None, ColorRole.BACKGROUND) else role

    templates: list[PageTemplate] = []
    seen: set[PageType] = set()
    if notes:
        for t in notes.templates:
            if t.page_type in seen:
                continue
            seen.add(t.page_type)
            p = by_idx.get(t.example_page_index)
            regions = [Region(name=r.name, kind=r.kind, bbox=r.bbox, style_role=r.style_role,
                              fill_role=_role_for_hex(palette, r.fill_hex)) for r in t.regions]
            if not regions and t.page_type in _MEASURED_TYPES:
                regions = _measured_regions(raw, palette, t.example_page_index, t.page_type)
            templates.append(PageTemplate(
                page_type=t.page_type, columns=p.column_estimate if p else t.columns,
                background_role=bg_role(t.example_page_index, t.background_hex), regions=regions,
                show_header=bool(header) and (t.example_page_index in h_pages),
                show_footer=bool(footer) and (t.example_page_index in f_pages),
                description=""))
    counts = [p.char_count for p in raw.pages if p.text_bbox is not None] or [p.char_count for p in raw.pages]
    med = statistics.median(counts) if counts else 0

    def cover_like(p) -> bool:  # type: ignore[no-untyped-def]
        return p.fill_area_ratio >= 0.5 or p.image_area_ratio >= 0.5 or p.char_count < 0.25 * med

    n = len(raw.pages)
    if PageType.COVER not in seen and n and cover_like(raw.pages[0]):
        templates.append(PageTemplate(page_type=PageType.COVER, columns=1, background_role=bg_role(0, None),
                                      regions=_measured_regions(raw, palette, 0, PageType.COVER),
                                      show_header=False, show_footer=False,
                                      description="Cover page (heuristic: full-bleed or sparse first page)."))
        seen.add(PageType.COVER)
    if PageType.BACK_COVER not in seen and n > 2 and cover_like(raw.pages[-1]):
        templates.append(PageTemplate(page_type=PageType.BACK_COVER, columns=1,
                                      background_role=bg_role(n - 1, None),
                                      regions=_measured_regions(raw, palette, n - 1, PageType.BACK_COVER),
                                      show_header=False, show_footer=False,
                                      description="Back cover (heuristic)."))
        seen.add(PageType.BACK_COVER)
    if PageType.BODY not in seen:
        templates.append(PageTemplate(page_type=PageType.BODY, columns=grid.columns, show_header=bool(header),
                                      show_footer=bool(footer)))
    order = list(PageType)
    templates.sort(key=lambda t: order.index(t.page_type))
    return templates


def _callout_from_boxes(raw: RawDesignStats, palette: dict[ColorRole, str]) -> Optional[BoxStyle]:
    """Measured callout look: the most common panel-sized filled box gives the fill; a narrow bar flush with
    that panel's left edge gives a left border. None when the reference has no such box."""
    page_w = raw.pages[0].width_pt if raw.pages else 595.0
    panels = [b for b in raw.filled_boxes
              if b.bbox.width >= 0.3 * page_w and 24 <= b.bbox.height <= 0.4 * (raw.pages[0].height_pt if raw.pages
                                                                                else 842)]
    if not panels:
        return None
    fill_hex = Counter(b.fill for b in panels).most_common(1)[0][0]
    panel = next(b for b in panels if b.fill == fill_hex)
    # a table header band looks like a panel but is not a callout: it is dark and flush with the text edge
    if C.luminance(fill_hex) < 0.35 and len([b for b in panels if b.fill == fill_hex]) == 1 and panel.bbox.height < 40:
        return None
    box = BoxStyle(fill_role=_role_for_hex(palette, fill_hex) or ColorRole.SURFACE)
    for bar in raw.filled_boxes:
        bb, pb = bar.bbox, panel.bbox
        if (bar.page_index == panel.page_index and 1 <= bb.width <= 10 and abs(bb.x0 - pb.x0) <= 2
                and bb.height >= 0.6 * pb.height and bb.y0 <= pb.y1 and bb.y1 >= pb.y0):
            box = box.model_copy(update=dict(border_sides="left", border_width_pt=round(bb.width, 1),
                                             border_role=_role_for_hex(palette, bar.fill) or ColorRole.ACCENT))
            break
    return box


def _has(text: Optional[str], *words: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in words)


def _box_styles(notes: Optional[VisionDesignNotes], palette: dict[ColorRole, str]
                ) -> tuple[BoxStyle, TableStyle, FigureStyle]:
    callout, table, figure = BoxStyle(), TableStyle(), FigureStyle()
    if not notes:
        return callout, table, figure
    d = notes.callout_description
    if d:
        accent = ColorRole.ACCENT if ColorRole.ACCENT in palette else ColorRole.PRIMARY
        if (_has(d, "side bar", "sidebar", "vertical bar", "accent bar")
                or re.search(r"left.{0,20}(bar|border|edge|rule|stripe)|(bar|border|stripe|rule).{0,25}left",
                             d.lower())):
            callout = callout.model_copy(update=dict(border_sides="left", border_width_pt=3.0, border_role=accent))
        elif _has(d, "border", "outline", "stroke"):
            callout = callout.model_copy(
                update=dict(border_sides="all", border_width_pt=1.0, border_role=ColorRole.RULE))
        if _has(d, "rounded", "radius"):
            callout = callout.model_copy(update=dict(radius_pt=6.0))
        if _has(d, "no fill", "unfilled", "transparent", "no background"):
            callout = callout.model_copy(update=dict(fill_role=None))
        elif _has(d, "primary", "dark fill", "dark background", "navy"):
            callout = callout.model_copy(update=dict(fill_role=ColorRole.PRIMARY))
    d = notes.table_description
    if d:
        upd: dict = {}
        if _has(d, "no header fill", "unfilled header", "no fill", "plain header", "bold header", "underlined header"):
            upd.update(header_fill_role=None, header_text_role=ColorRole.TEXT)
        if _has(d, "zebra", "striped", "alternating", "banded"):
            upd.update(zebra_fill_role=ColorRole.SURFACE)
        table = table.model_copy(update=upd)
    d = notes.figure_description
    if d:
        upd = {}
        if _has(d, "no gridlines", "without gridlines", "no grid"):
            upd.update(gridlines=False)
        if _has(d, "caption above", "caption on top", "caption at top"):
            upd.update(caption_position="above")
        figure = figure.model_copy(update=upd)
    return callout, table, figure


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def synthesize_design(raw: RawDesignStats, notes: Optional[VisionDesignNotes]) -> DesignSpec:
    """Numbers (sizes, margins, page size, colors) always come from ``raw``;
    ``notes`` supplies role labels, page templates, box/table styles.
    Without notes (dry run / vision failure) use heuristics: biggest cluster =
    body, larger sizes ranked to h3/h2/h1/title/display, most common non-text
    non-white fill = primary. Every TypeRole must get a TypeStyle (derive
    missing ones from body with a modular scale 1.25)."""
    warnings: list[str] = []
    if notes is None:
        warnings.append("Vision analysis unavailable: design derived from measured data with heuristics only.")
    by_id = {c.id: c for c in raw.font_clusters}
    body = by_id.get(raw.body_cluster_id or "") or (max(raw.font_clusters, key=lambda c: c.char_count)
                                                    if raw.font_clusters else None)
    if body is None:
        warnings.append("Reference PDF has no extractable text; typography uses defaults.")
    if notes:
        for ra in notes.role_assignments:
            if ra.role == TypeRole.BODY and body and ra.cluster_id != body.id:
                warnings.append(f"Vision labeled {ra.cluster_id} as body; measured body cluster {body.id} kept.")

    # grid from body pages
    body_pages = [p for p in raw.pages if p.text_bbox is not None]
    med = statistics.median([p.char_count for p in body_pages]) if body_pages else 0
    body_pages = [p for p in body_pages if p.char_count >= 0.5 * med]
    col_chars: Counter = Counter()
    for p in body_pages:
        col_chars[p.column_estimate] += p.char_count
    columns = col_chars.most_common(1)[0][0] if col_chars else 1
    gutters = [p.column_gutter_pt for p in body_pages if p.column_estimate == columns and p.column_gutter_pt]
    grid = Grid(columns=columns, gutter_pt=round(statistics.median(gutters), 1) if gutters else 18.0)

    # page background if most body pages are filled edge to edge
    bg_fill = None
    if body_pages:
        doms = Counter(p.dominant_fill for p in body_pages if p.dominant_fill and p.fill_area_ratio >= 0.9)
        if doms and sum(doms.values()) > len(body_pages) / 2:
            bg_fill = doms.most_common(1)[0][0]

    assigned = _assign_clusters(raw, notes, body)
    pal_list = _palette(raw, notes, body, assigned.get(TypeRole.H1), bg_fill)
    palette = {p.role: p.hex for p in pal_list}
    callout, table, figure = _box_styles(notes, palette)
    measured_callout = _callout_from_boxes(raw, palette)
    if measured_callout is not None:  # measured geometry beats the prose description; keep radius/padding cues
        callout = callout.model_copy(update=dict(
            fill_role=measured_callout.fill_role, border_sides=measured_callout.border_sides,
            border_width_pt=measured_callout.border_width_pt, border_role=measured_callout.border_role))
    typography = _typography(raw, notes, assigned, body, palette, table)
    header = _running(raw, True, TypeRole.HEADER)
    footer = _running(raw, False, TypeRole.FOOTER)
    templates = _templates(raw, notes, palette, grid, header, footer)

    bsize = body.size_pt if body else 10.0
    h1 = next(t.size_pt for t in typography if t.role == TypeRole.H1)
    para = _round_half(raw.paragraph_spacing_pt) if raw.paragraph_spacing_pt is not None else _round_half(bsize * 0.8)
    spacing = SpacingScale(paragraph_pt=para, section_pt=_round_half(max(h1 * 1.4, bsize * 2.8)),
                           block_pt=_round_half(bsize * 1.4))
    body_family = family_from_font_name(body.font_name) if body else "default"
    summary = notes.overall_style if notes else (
        f"{raw.page_count}-page reference, {columns}-column body in {body_family} "
        f"{bsize:g} pt, primary {palette[ColorRole.PRIMARY]}.")
    return DesignSpec(
        source_pdf=raw.source_pdf, source_sha256=raw.source_sha256, page_size=_page_size(raw),
        margins=Margins(**raw.margins_estimate.model_dump()), grid=grid, typography=typography, palette=pal_list,
        header=header, footer=footer, templates=templates, callout=callout, table=table, figure=figure,
        spacing=spacing, motifs=list(notes.motifs) if notes else [], style_summary=summary, warnings=warnings)
