"""Apply Brief overrides to a DesignSpec (WP3). Pure function."""

from __future__ import annotations

import logging
import re
from typing import Optional

from rooted.design import color as C
from rooted.design.synthesize import KNOWN_SIZES
from rooted.models import Brief, ColorRole, DesignSpec, Margins, PageSize, PaletteColor, TypeRole

log = logging.getLogger(__name__)

MIN_CONTRAST = 4.5
_DIM = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)\s*(in|inch|inches|mm|cm|pt)?\s*$", re.I)
_UNIT_PT = {"in": 72.0, "inch": 72.0, "inches": 72.0, "mm": 72 / 25.4, "cm": 72 / 2.54, "pt": 1.0}


def parse_page_size(text: str) -> Optional[PageSize]:
    """'A4', 'Letter', 'A4 landscape', '6x9in', '210x297mm' -> PageSize (None if unparseable)."""
    t = text.strip()
    landscape = bool(re.search(r"landscape", t, re.I))
    key = re.sub(r"(?i)\s*(landscape|portrait)\s*", "", t).strip().lower()
    for name, (w, h) in KNOWN_SIZES.items():
        if name.lower() == key:
            return PageSize(width_pt=h if landscape else w, height_pt=w if landscape else h,
                            name=f"{name} landscape" if landscape else name)
    m = _DIM.match(key)
    if m:
        unit = _UNIT_PT[(m.group(3) or "in").lower()]
        w, h = float(m.group(1)) * unit, float(m.group(2)) * unit
        if landscape and w < h:
            w, h = h, w
        return PageSize(width_pt=round(w, 2), height_pt=round(h, 2), name=None)
    return None


def _scale_margins(m: Margins, sx: float, sy: float) -> Margins:
    return Margins(top=round(m.top * sy, 1), right=round(m.right * sx, 1), bottom=round(m.bottom * sy, 1),
                   left=round(m.left * sx, 1))


def _set_color(spec: DesignSpec, role: ColorRole, hexval: str, source: str) -> Optional[str]:
    """Replace or add a palette entry; returns the previous hex (None if added)."""
    for i, pc in enumerate(spec.palette):
        if pc.role == role:
            spec.palette[i] = pc.model_copy(update={"hex": hexval, "source": source})
            return pc.hex
    spec.palette.append(PaletteColor(role=role, hex=hexval, weight=0.0, source=source))
    return None


def _fix_on_primary(spec: DesignSpec, explicit: bool, log_lines: list[str]) -> None:
    primary = spec.color(ColorRole.PRIMARY)
    current = spec.color(ColorRole.ON_PRIMARY)
    if C.contrast(current, primary) >= MIN_CONTRAST:
        return
    text = spec.color(ColorRole.TEXT)
    cands = ["#FFFFFF", text, "#111111", "#000000"]
    ok = [c for c in cands if C.contrast(c, primary) >= MIN_CONTRAST]
    pick = ok[0] if ok else max(["#FFFFFF", "#000000"], key=lambda c: C.contrast(c, primary))
    _set_color(spec, ColorRole.ON_PRIMARY, pick, "brief" if explicit else "default")
    ratio = C.contrast(pick, primary)
    log_lines.append(f"palette: on_primary {current} -> {pick} (contrast {ratio:.1f}:1 on primary {primary})")


def apply_brief(spec: DesignSpec, brief: Brief) -> DesignSpec:
    """Brief beats reference: palette_overrides replace roles (and
    ON_PRIMARY is recomputed for WCAG contrast >= 4.5), font_overrides set
    families, page_size rescales margins/regions proportionally. Each change
    is logged in ``overrides_applied``."""
    out = spec.model_copy(deep=True)
    lines: list[str] = []

    # ---- palette ----
    applied: dict[ColorRole, str] = {}
    for ov in brief.palette_overrides:
        h = C.normalize_hex(ov.hex)
        if h is None:
            out.warnings.append(f"Brief color {ov.hex!r} for {ov.role.value} is not a valid hex; ignored.")
            continue
        old = _set_color(out, ov.role, h, "brief")
        applied[ov.role] = h
        label = f" ({ov.name})" if ov.name else ""
        lines.append(f"palette: {ov.role.value} {old or 'unset'} -> {h}{label}")
    if applied:
        if ColorRole.PRIMARY in applied and len(applied) == 1 and not any(
                pc.role == ColorRole.SECONDARY and pc.source == "brief" for pc in out.palette):
            tint = C.mix(applied[ColorRole.PRIMARY], "#FFFFFF", 0.35)
            old = _set_color(out, ColorRole.SECONDARY, tint, "brief")
            lines.append(f"palette: secondary {old or 'unset'} -> {tint} (tint of brief primary)")
        if ColorRole.PRIMARY in applied:
            # headings carrying a leftover chromatic reference color (e.g. an extracted secondary) must follow the
            # brief's primary, otherwise "navy and gold" renders with the reference's own heading hue
            for st in out.typography:
                if st.role in (TypeRole.TITLE, TypeRole.H1, TypeRole.H2, TypeRole.H3) and st.color_role in (
                    ColorRole.SECONDARY, ColorRole.ACCENT_2, ColorRole.HEADING
                ) and st.color_role not in applied and not C.is_neutral(out.color(st.color_role)):
                    lines.append(f"type: {st.role.value} color {st.color_role.value} -> primary (follows brief primary)")
                    st.color_role = ColorRole.PRIMARY
        if ColorRole.PRIMARY in applied or ColorRole.ON_PRIMARY in applied:
            _fix_on_primary(out, ColorRole.ON_PRIMARY in applied, lines)

    # ---- fonts ----
    for fo in brief.font_overrides:
        targets = [s for s in out.typography if fo.role is None or s.role == fo.role]
        for s in targets:
            if s.family != fo.family:
                s.family = fo.family
        if targets:
            lines.append(f"font: {fo.role.value if fo.role else 'all roles'} -> {fo.family}")

    # ---- page size ----
    if brief.page_size:
        ps = parse_page_size(brief.page_size)
        if ps is None:
            out.warnings.append(f"Brief page size {brief.page_size!r} not understood; reference page size kept.")
        elif abs(ps.width_pt - out.page_size.width_pt) > 1 or abs(ps.height_pt - out.page_size.height_pt) > 1:
            sx, sy = ps.width_pt / out.page_size.width_pt, ps.height_pt / out.page_size.height_pt
            old = out.page_size
            out.page_size = ps
            out.margins = _scale_margins(out.margins, sx, sy)
            out.grid = out.grid.model_copy(update={"gutter_pt": round(out.grid.gutter_pt * sx, 1)})
            for t in out.templates:
                if t.margins is not None:
                    t.margins = _scale_margins(t.margins, sx, sy)
            lines.append(f"page_size: {old.width_pt:g}x{old.height_pt:g} pt -> {ps.width_pt:g}x{ps.height_pt:g} pt "
                         f"({ps.name or brief.page_size}); margins rescaled")
    out.overrides_applied = list(out.overrides_applied) + lines
    return out
