"""LayoutPlan -> HTML + CSS via Jinja2 templates (WP5).

Targets Chromium's paged-media support: named ``@page`` rules (one per page
type and section), margin boxes for running headers/footers, ``break-*`` and
multi-column flow. There is no ``string-set`` in Chromium, so running section
titles are written literally into the section's own named page.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from rooted.models import (
    Block,
    BlockType,
    ColorRole,
    ContentDoc,
    ContentPlan,
    DesignSpec,
    LayoutElement,
    LayoutPlan,
    LayoutUnit,
    Margins,
    NormBBox,
    PageType,
    PlannedText,
    Region,
    Section,
    TypeRole,
    TypeStyle,
    VisualSet,
)

_HERE = Path(__file__).parent
TEMPLATE_DIR = _HERE / "templates"
STATIC_DIR = _HERE / "static"

_GENERIC_FAMILIES = {
    "serif",
    "sans-serif",
    "monospace",
    "cursive",
    "fantasy",
    "system-ui",
    "ui-serif",
    "ui-sans-serif",
    "ui-monospace",
}
# roles whose size is driven by FitParams.font_scale (cover/opener titles and running text stay put)
_FIXED_SIZE_ROLES = {TypeRole.DISPLAY, TypeRole.TITLE, TypeRole.HEADER, TypeRole.FOOTER}
_PAGE_PREFIX = {
    PageType.COVER: "cover",
    PageType.BACK_COVER: "back",
    PageType.SECTION_OPENER: "opener",
    PageType.BODY: "body",
    PageType.DATA: "data",
    PageType.TOC: "toc",
    PageType.FULL_VISUAL: "visual",
}
_DARK_BG_ROLES = {ColorRole.PRIMARY, ColorRole.SECONDARY, ColorRole.ACCENT, ColorRole.ACCENT_2}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def page_name(unit: LayoutUnit) -> str:
    base = _PAGE_PREFIX.get(unit.page_type, "body")
    if unit.section_id:
        base += "-" + re.sub(r"[^A-Za-z0-9_-]", "-", unit.section_id)
    return base


def _css_str(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("<", "\\3C ")
    return '"' + s.replace("\n", "\\A ").replace("\r", "") + '"'


def _family_stack(style: TypeStyle) -> str:
    names = [style.family, *style.fallback_stack]
    out: list[str] = []
    for n in names:
        if not n or n in out:
            continue
        out.append(n)
    parts = [n if n in _GENERIC_FAMILIES else _css_str(n) for n in out]
    if not any(n in _GENERIC_FAMILIES for n in out):
        parts.append("sans-serif")
    return ", ".join(parts)


def _pt(v: float) -> str:
    return f"{v:.2f}pt"


def _scaled_margins(m: Margins, scale: float) -> Margins:
    return Margins(top=m.top * scale, right=m.right * scale, bottom=m.bottom * scale, left=m.left * scale)


def _margin_css(m: Margins) -> str:
    return f"{_pt(m.top)} {_pt(m.right)} {_pt(m.bottom)} {_pt(m.left)}"


def unit_margins(design: DesignSpec, layout: LayoutPlan, unit: LayoutUnit) -> Margins:
    """Page margins for a unit after FitParams.margin_scale. Cover and back
    cover are full bleed."""
    if unit.page_type in (PageType.COVER, PageType.BACK_COVER):
        return Margins(top=0, right=0, bottom=0, left=0)
    tpl = design.template(unit.page_type)
    base = tpl.margins if tpl is not None and tpl.margins is not None else design.margins
    return _scaled_margins(base, layout.fit.margin_scale)


def _eff_font_scale(design: DesignSpec, scale: float, min_body_size_pt: float) -> float:
    body = design.style(TypeRole.BODY).size_pt
    eff = max(body * scale, min(body, min_body_size_pt))
    return eff / body if body else scale


def _style_decls(style: TypeStyle, design: DesignSpec, *, size_scale: float = 1.0, lh_scale: float = 1.0) -> str:
    d = [
        f"font-family: {_family_stack(style)}",
        f"font-size: {_pt(style.size_pt * size_scale)}",
        f"font-weight: {style.weight}",
        f"font-style: {'italic' if style.italic else 'normal'}",
        f"line-height: {style.line_height * lh_scale:.3f}",
        f"color: {design.color(style.color_role)}",
    ]
    if style.letter_spacing_em:
        d.append(f"letter-spacing: {style.letter_spacing_em:.3f}em")
    if style.uppercase:
        d.append("text-transform: uppercase")
    return "; ".join(d)


def _box_vars(prefix: str, box, design: DesignSpec) -> list[str]:
    bw = box.border_width_pt
    border = "0 solid transparent"
    if box.border_role is not None and bw > 0 and box.border_sides != "none":
        border = f"{_pt(bw)} solid {design.color(box.border_role)}"
    fill = design.color(box.fill_role) if box.fill_role is not None else "transparent"
    return [
        f"--{prefix}-bg: {fill}",
        f"--{prefix}-border: {border}",
        f"--{prefix}-pad: {_pt(box.padding_pt)}",
        f"--{prefix}-radius: {_pt(box.radius_pt)}",
    ]


def _sides_class(box) -> str:
    return f"sides-{box.border_sides}" if box.border_sides in ("left", "top", "bottom") else ""


def _running_content(content: str, section_title: str, doc_title: str) -> str:
    """Slot content with tokens -> CSS ``content`` value."""
    parts: list[str] = []
    for tok in re.split(r"(\{page\}|\{pages\}|\{section\}|\{title\})", content):
        if not tok:
            continue
        if tok == "{page}":
            parts.append("counter(page)")
        elif tok == "{pages}":
            parts.append("counter(pages)")
        elif tok == "{section}":
            parts.append(_css_str(section_title))
        elif tok == "{title}":
            parts.append(_css_str(doc_title))
        else:
            parts.append(_css_str(tok))
    return " ".join(parts) or '""'


def _margin_boxes(
    design: DesignSpec, unit: LayoutUnit, section_title: str, doc_title: str, margins: Optional[Margins] = None
) -> str:
    """Margin-box rules for header and footer of one named page.

    When the design measured the running element's vertical centre
    (``y_center_pt``) the text is placed there with padding inside the margin
    box; otherwise it hugs the content edge (header bottom, footer top)."""
    tpl = design.template(unit.page_type)
    H = design.page_size.height_pt
    m = margins or design.margins
    css: list[str] = []
    for edge, running, show in (
        ("top", design.header, tpl.show_header if tpl else True),
        ("bottom", design.footer, tpl.show_footer if tpl else True),
    ):
        if running is None or not show or unit.page_type in running.hidden_on or not running.slots:
            continue
        style = design.style(running.style_role)
        decl = _style_decls(style, design)
        line_h = style.size_pt * style.line_height
        yc = running.y_center_pt
        box_h = m.top if edge == "top" else m.bottom
        if yc is not None:
            box_top = 0.0 if edge == "top" else H - m.bottom
            pad = min(max(0.0, yc - box_top - line_h / 2), max(0.0, box_h - line_h))
            valign = f"vertical-align: top; padding-top: {_pt(pad)};"
            hair = pad + line_h + 4 if edge == "top" else max(0.0, pad - 8)
        else:
            valign = "vertical-align: bottom;" if edge == "top" else "vertical-align: top;"
            hair = 0.0
        rule = ""
        if running.rule:
            col = design.color(running.rule_color_role)
            grad = f"linear-gradient({col}, {col}) no-repeat"
            if yc is not None:
                rule = f"background: {grad} left {_pt(hair)} / 100% 0.5pt;"
            elif edge == "top":
                rule = f"padding-bottom: 12pt; background: {grad} left bottom 8pt / 100% 0.5pt;"
            else:
                rule = f"padding-top: 12pt; background: {grad} left top 8pt / 100% 0.5pt;"
        base = f"{decl}; {valign} {rule}"
        # (selector suffix, box position) -> content; "outside"/"inside" flip between verso (:left) and recto (:right)
        placed: dict[tuple[str, str], str] = {}
        for s in running.slots:
            c = _running_content(s.content, section_title, doc_title)
            if s.position in ("left", "center", "right"):
                for sel in ("",):
                    placed[(sel, s.position)] = c
            elif s.position in ("outside", "inside"):
                outside = s.position == "outside"
                placed[(":left", "left" if outside else "right")] = c
                placed[(":right", "right" if outside else "left")] = c
        name = page_name(unit)
        by_sel: dict[str, list[str]] = {}
        for (sel, pos), c in placed.items():
            by_sel.setdefault(sel, []).append(f"@{edge}-{pos} {{ content: {c}; {base} }}")
        for sel, boxes in by_sel.items():
            css.append(f"@page {name}{sel} {{ " + " ".join(boxes) + " }")
        if running.rule and "" in by_sel:
            # make the hairline continuous across the margin by also defining empty neighbours
            present = {m.group(1) for m in re.finditer(rf"@{edge}-(left|center|right) ", " ".join(by_sel[""]))}
            extra = [
                f'@{edge}-{pos} {{ content: ""; {base} }}' for pos in ("left", "center", "right") if pos not in present
            ]
            if extra:
                css.append(f"@page {name} {{ " + " ".join(extra) + " }")
    return "\n".join(css)


def _font_faces(design: DesignSpec, work_dir: Optional[Path]) -> str:
    seen: set[tuple] = set()
    out: list[str] = []
    for res in design.fonts:
        for face in res.faces:
            p = Path(face.path)
            if not p.is_absolute() and work_dir is not None:
                p = work_dir / p
            if not p.exists():
                continue  # fall back to the generic stack
            key = (face.family, face.weight, face.italic, str(p))
            if key in seen:
                continue
            seen.add(key)
            fmt = {".ttf": "truetype", ".otf": "opentype", ".woff": "woff", ".woff2": "woff2"}.get(p.suffix.lower())
            src = f"url({_css_str(p.resolve().as_uri())})" + (f" format({_css_str(fmt)})" if fmt else "")
            out.append(
                f"@font-face {{ font-family: {_css_str(face.family)}; src: {src}; "
                f"font-weight: {face.weight}; font-style: {'italic' if face.italic else 'normal'}; }}"
            )
    return "\n".join(out)


def build_css(
    design: DesignSpec,
    layout: LayoutPlan,
    *,
    section_titles: Optional[dict[str, str]] = None,
    doc_title: str = "",
    work_dir: Optional[Path] = None,
    min_body_size_pt: float = 8.5,
) -> str:
    """CSS custom properties from DesignSpec * FitParams, @font-face rules
    from design.fonts, @page rules per named page (cover, opener, body,
    data, back), running header/footer margin boxes, columns."""
    fit = layout.fit
    section_titles = section_titles or {}
    W, H = design.page_size.width_pt, design.page_size.height_pt
    fs = _eff_font_scale(design, fit.font_scale, min_body_size_pt)
    sp = fit.spacing_scale
    body = design.style(TypeRole.BODY)
    para = (body.space_after_pt or design.spacing.paragraph_pt) * sp

    parts: list[str] = [_font_faces(design, work_dir)]

    # ---- custom properties
    v: list[str] = [
        f"--page-w: {_pt(W)}",
        f"--page-h: {_pt(H)}",
        f"--cols: {max(1, design.grid.columns)}",
        f"--gutter: {_pt(design.grid.gutter_pt)}",
        f"--space-paragraph: {_pt(para)}",
        f"--space-block: {_pt(design.spacing.block_pt * sp)}",
        f"--space-section: {_pt(design.spacing.section_pt * sp)}",
        f"--ff-body: {_family_stack(body)}",
        f"--content-h: {_pt(H - design.margins.top - design.margins.bottom)}",
    ]
    for role in ColorRole:
        v.append(f"--c-{role.value.replace('_', '-')}: {design.color(role)}")
    t = design.table
    v += [
        f"--tbl-head-bg: {design.color(t.header_fill_role) if t.header_fill_role else 'transparent'}",
        f"--tbl-head-fg: {design.color(t.header_text_role)}",
        f"--tbl-zebra: {design.color(t.zebra_fill_role) if t.zebra_fill_role else 'transparent'}",
        f"--tbl-rule: {_pt(t.rule_width_pt)} solid {design.color(t.rule_role)}",
        f"--tbl-pad: {_pt(t.cell_padding_pt * sp)}",
    ]
    v += _box_vars("callout", design.callout, design)
    v += _box_vars("pq", design.pull_quote, design)
    v += _box_vars("stat", design.stat_tile, design)
    parts.append(":root { " + "; ".join(v) + "; }")

    # ---- type roles
    for style in design.typography:
        scale = 1.0 if style.role in _FIXED_SIZE_ROLES else fs
        decl = _style_decls(style, design, size_scale=scale, lh_scale=fit.line_height_scale)
        mt, mb = style.space_before_pt * sp, style.space_after_pt * sp
        extra = ""
        if style.role in (TypeRole.H1, TypeRole.H2, TypeRole.H3):
            extra = f"; margin-top: {_pt(mt)}; margin-bottom: {_pt(mb)}"
        parts.append(f".t-{style.role.value.replace('_', '-')} {{ {decl}{extra} }}")
    # roles the spec lacks fall back to body
    body_decl = _style_decls(body, design, size_scale=fs, lh_scale=fit.line_height_scale)
    have = {s.role for s in design.typography}
    for role in TypeRole:
        if role not in have:
            parts.append(f".t-{role.value.replace('_', '-')} {{ {body_decl} }}")

    # ---- pages
    page_bg = design.color(ColorRole.BACKGROUND)
    parts.append(f"@page {{ size: {_pt(W)} {_pt(H)}; margin: {_margin_css(design.margins)}; background: {page_bg}; }}")
    seen: set[str] = set()
    for unit in layout.units:
        name = page_name(unit)
        if name in seen:
            continue
        seen.add(name)
        m = unit_margins(design, layout, unit)
        tpl = design.template(unit.page_type)
        bg_role = tpl.background_role if tpl is not None and tpl.background_role is not None else ColorRole.BACKGROUND
        bg = f" background: {design.color(bg_role)};"
        parts.append(f"@page {name} {{ size: {_pt(W)} {_pt(H)}; margin: {_margin_css(m)};{bg} }}")
        mb = _margin_boxes(design, unit, section_titles.get(unit.section_id or "", ""), doc_title, m)
        if mb:
            parts.append(mb)
        parts.append(f".pg-{name} {{ page: {name}; }}")

    parts.append((STATIC_DIR / "base.css").read_text("utf-8"))
    return "\n".join(p for p in parts if p)


# --------------------------------------------------------------------------
# text rendering
# --------------------------------------------------------------------------


def _safe_href(url: str) -> Optional[str]:
    u = url.strip()
    return u if re.match(r"^(https?://|mailto:|#)", u, re.I) else None


def render_runs(runs) -> Markup:
    """Inline runs -> escaped HTML with <strong>/<em>/... All text is escaped."""
    out: list[Markup] = []
    for r in runs:
        txt = escape(r.text).replace("\n", Markup("<br>"))
        h = Markup(txt)
        if r.code:
            h = Markup("<code>%s</code>") % h
        if r.superscript:
            h = Markup("<sup>%s</sup>") % h
        if r.subscript:
            h = Markup("<sub>%s</sub>") % h
        if r.bold:
            h = Markup("<strong>%s</strong>") % h
        if r.italic:
            h = Markup("<em>%s</em>") % h
        if r.underline:
            h = Markup("<u>%s</u>") % h
        if r.link and _safe_href(r.link):
            h = Markup('<a class="lnk" href="%s">%s</a>') % (_safe_href(r.link), h)
        out.append(h)
    return Markup("").join(out)


def _plain(text: str) -> Markup:
    return Markup(escape(text).replace("\n", Markup("<br>")))


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=select_autoescape(["html", "j2"], default=True, default_for_string=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    return env


_DEFAULT_COVER = {
    "eyebrow": ((0.1, 0.12, 0.9, 0.16), TypeRole.LABEL),
    "title": ((0.1, 0.18, 0.9, 0.40), TypeRole.DISPLAY),
    "subtitle": ((0.1, 0.42, 0.9, 0.50), TypeRole.LEAD),
    "byline": ((0.1, 0.90, 0.9, 0.95), TypeRole.STAT_LABEL),
    "date": ((0.1, 0.86, 0.9, 0.89), TypeRole.STAT_LABEL),
}
_DEFAULT_OPENER = {
    "number": ((0.1, 0.30, 0.5, 0.38), TypeRole.LABEL),
    "title": ((0.1, 0.38, 0.9, 0.55), TypeRole.TITLE),
}
_DEFAULT_BACK = {"text": ((0.1, 0.40, 0.9, 0.70), TypeRole.BODY)}
_BACK_TEXT_NAMES = {"text", "body", "contact", "tagline", "back_text"}


class _Builder:
    def __init__(self, doc, plan, design, visuals, layout, work_dir):
        self.doc, self.plan, self.design, self.visuals, self.layout = doc, plan, design, visuals, layout
        self.work_dir = Path(work_dir)
        self.rewrites = {r.block_id: r.new_text for r in plan.rewrites}
        self.sections: dict[str, Section] = {s.id: s for s in plan.sections}
        self.sec_index = {s.id: i + 1 for i, s in enumerate(plan.sections)}
        self.specs = {v.id: v for v in visuals.plan.visuals}
        self.callouts = {c.id: c for s in plan.sections for c in s.callouts}
        self.quotes = {q.id: q for s in plan.sections for q in s.pull_quotes}
        self.stats = {k.id: k for s in plan.sections for k in s.key_stats}
        consumed = set(plan.cover.consumed_block_ids) if plan.cover else set()
        levels = [b.level for b in doc.blocks if b.type == BlockType.HEADING and b.id not in consumed and b.level > 0]
        self.heading_base = min(levels) if levels else 1  # the highest heading level present maps to H1
        self.anchors: list[str] = []  # ids for the page map
        self.notes: list[str] = []

    # ---- text/blocks
    def block_html(self, b: Block) -> Markup:
        if b.id in self.rewrites:
            return _plain(self.rewrites[b.id])
        return render_runs(b.runs)

    def block_view(self, b: Block) -> dict:
        v: dict = {
            "id": b.id,
            "type": b.type.value,
            "level": b.level,
            "ordered": b.ordered,
            "label": b.list_label,
            "html": Markup(""),
        }
        if b.type == BlockType.TABLE and b.table is not None:
            t = b.table
            rows = []
            for ri, row in enumerate(t.rows):
                rows.append(
                    [
                        {
                            "html": render_runs(c.runs),
                            "colspan": c.colspan,
                            "rowspan": c.rowspan,
                            "head": ri < t.header_rows,
                        }
                        for c in row
                    ]
                )
            v["rows"] = rows
            v["header_rows"] = t.header_rows
            v["caption"] = t.caption
        elif b.type == BlockType.IMAGE and b.image is not None:
            v["src"] = self._uri(b.image.path)
            v["alt"] = b.image.alt_text or ""
            if b.image.width_pt and b.image.height_pt:
                v["w"], v["h"] = b.image.width_pt, b.image.height_pt
            else:
                v["w"], v["h"] = b.image.width_px * 0.75, b.image.height_px * 0.75
        else:
            v["html"] = self.block_html(b)
        return v

    def _uri(self, path: str) -> Optional[str]:
        p = Path(path)
        if not p.is_absolute():
            p = self.work_dir / p
        return p.resolve().as_uri() if p.exists() else None

    # ---- elements
    def element_view(self, e: LayoutElement, cols: int = 1) -> Optional[dict]:
        blocks = [self.doc.block(b) for b in e.block_ids if b in set(self.doc.block_ids)]
        v: dict = {
            "id": e.id,
            "kind": e.kind,
            "attr_blocks": " ".join(e.block_ids),
            "classes": [],
            "break_before": e.id in self.layout.fit.forced_breaks_before,
            "blocks": [self.block_view(b) for b in blocks],
        }
        k = e.kind
        if k == "heading":
            level = max(1, (blocks[0].level if blocks else 1) - self.heading_base + 1)
            v["role"] = {1: "h1", 2: "h2"}.get(level, "h3")
            if e.ref_id == "opener":
                v["role"] = "title"
                v["classes"].append("inline-opener")
            elif v["role"] == "h1" and cols > 1:
                v["classes"].append("span-all")
        elif k == "visual" or k == "decor":
            vis = self._visual(e.visual_id)
            if vis is None:
                return None
            v.update(vis)
        elif k == "callout":
            c = self.callouts.get(e.ref_id or "")
            if c is None:
                return None
            v["title"] = c.title.text if c.title else None
            v["kind_name"] = c.kind
            v["classes"].append(_sides_class(self.design.callout))
        elif k == "pull_quote":
            q = self.quotes.get(e.ref_id or "")
            if q is None:
                return None
            v["text"] = q.excerpt.text
            v["classes"].append(_sides_class(self.design.pull_quote))
        elif k == "key_stats":
            s = self.stats.get(e.ref_id or "")
            if s is None:
                return None
            v["value"], v["label"] = s.value, s.label.text
        elif k == "toc":
            v["entries"] = [
                (s.title.text if s.title else (self.doc.block(s.heading_block_id).text if s.heading_block_id else ""))
                for s in self.plan.sections
                if s.title or s.heading_block_id
            ]
        elif k == "list":
            v["ordered"] = bool(blocks and blocks[0].ordered)
        return v

    def _visual(self, vid: Optional[str]) -> Optional[dict]:
        spec = self.specs.get(vid or "")
        rv = self.visuals.rendered_for(vid or "")
        if spec is None or rv is None:
            return None
        src = self._uri(rv.path)
        if src is None:
            self.notes.append(f"visual {vid}: file {rv.path} missing, skipped")
            return None
        scale = self.layout.fit.visual_scale
        return {
            "src": src,
            "alt": spec.alt_text,
            "w": rv.width_pt * scale,
            "placement": spec.placement,
            "caption": spec.caption.text if spec.caption else None,
            "classes": [spec.placement if spec.placement in ("full_width", "full_page") else "inline"],
        }

    # ---- units
    def unit_view(self, u: LayoutUnit, index: int) -> dict:
        design = self.design
        W, H = design.page_size.width_pt, design.page_size.height_pt
        m = unit_margins(design, self.layout, u)
        name = page_name(u)
        view: dict = {
            "id": u.id,
            "name": name,
            "page_type": u.page_type.value,
            "fixed": u.fixed,
            "first": index == 0,
            "section_id": u.section_id,
        }
        tpl = design.template(u.page_type)
        els: list[dict] = []
        tpl_cols = tpl.columns if tpl is not None and tpl.columns else design.grid.columns
        for e in u.elements:
            ev = self.element_view(e, max(1, tpl_cols))
            if ev is not None:
                els.append(ev)
                self.anchors.append(e.id)
        view["elements"] = els
        if u.fixed:
            view["width"], view["height"] = W - m.left - m.right, H - m.top - m.bottom - 1.0
            view["regions"] = self._regions(u, els, tpl, W, H, m)
        else:
            cols = tpl.columns if tpl is not None and tpl.columns else design.grid.columns
            view["cols"] = max(1, cols)
        return view

    def _regions(self, u: LayoutUnit, els: list[dict], tpl, W: float, H: float, m: Margins) -> list[dict]:
        design = self.design
        order = {"shape": 0, "image": 1}
        regions: list[Region] = sorted(tpl.regions, key=lambda r: order.get(r.kind, 2)) if tpl is not None else []
        names = {r.name for r in regions}
        sec = self.sections.get(u.section_id or "")
        texts: dict[str, str] = {}
        hero = None
        owner = els[0]["id"] if els else u.id
        if u.page_type == PageType.COVER and self.plan.cover is not None:
            c = self.plan.cover
            for key in ("eyebrow", "title", "subtitle", "byline", "date"):
                pt: Optional[PlannedText] = getattr(c, key)
                if pt is not None:
                    texts[key] = pt.text
            defaults = _DEFAULT_COVER
        elif u.page_type == PageType.SECTION_OPENER and sec is not None:
            title = sec.title.text if sec.title else ""
            if sec.heading_block_id and sec.heading_block_id in set(self.doc.block_ids):
                hb = self.doc.block(sec.heading_block_id)
                title = self.rewrites.get(hb.id, hb.text)  # the heading block leaves the flow: show all of it
            texts["title"] = title
            texts["number"] = f"{self.sec_index.get(sec.id, 0):02d}"
            defaults = _DEFAULT_OPENER
        elif u.page_type == PageType.BACK_COVER:
            lines = [pt.text for pt in self.plan.back_cover_text]
            if lines:
                texts["text"] = "\n".join(lines)
            defaults = _DEFAULT_BACK
        else:
            defaults = {}
        for ev in els:
            if ev["kind"] == "visual":
                hero = ev
        if u.page_type == PageType.BACK_COVER:
            for r in regions:
                if r.kind == "text" and r.name in _BACK_TEXT_NAMES and "text" in texts:
                    texts[r.name] = texts["text"]
        for name, (bb, role) in defaults.items():
            if (
                name in texts
                and name not in names
                and not (u.page_type == PageType.BACK_COVER and names & _BACK_TEXT_NAMES)
            ):
                regions.append(
                    Region(
                        name=name, kind="text", bbox=NormBBox(x0=bb[0], y0=bb[1], x1=bb[2], y1=bb[3]), style_role=role
                    )
                )
                names.add(name)
        if hero is not None and not any(r.kind == "image" for r in regions):
            regions.append(
                Region(
                    name="hero",
                    kind="image",
                    bbox=NormBBox(x0=0, y0=0, x1=1, y1=0.45 if u.page_type == PageType.COVER else 0.3),
                )
            )

        bg_role = tpl.background_role if tpl is not None else None
        dark = bg_role in _DARK_BG_ROLES
        out: list[dict] = []
        for r in regions:
            bb = r.bbox
            x0, y0 = bb.x0 * W - m.left, bb.y0 * H - m.top
            w, h = (bb.x1 - bb.x0) * W, (bb.y1 - bb.y0) * H
            style = f"left:{x0:.2f}pt; top:{y0:.2f}pt; width:{w:.2f}pt; height:{h:.2f}pt;"
            d: dict = {
                "name": r.name,
                "kind": r.kind,
                "owner": owner,
                "classes": [],
                "text": None,
                "src": None,
                "fit": False,
            }
            if r.kind == "shape":
                if r.fill_role is not None:
                    style += f" background: {design.color(r.fill_role)};"
            elif r.kind == "image":
                if hero is not None:
                    d["src"], d["alt"] = hero["src"], hero.get("alt", "")
                else:
                    continue
            elif r.kind == "text":
                txt = texts.get(r.name)
                if txt is None:
                    continue
                role = r.style_role or TypeRole.BODY
                d["classes"].append(f"t-{role.value.replace('_', '-')}")
                if dark and design.style(role).color_role == ColorRole.TEXT:
                    style += f" color: {design.color(ColorRole.ON_PRIMARY)};"
                d["text"] = txt
                d["fit"] = True
            else:
                continue
            d["style"] = style
            out.append(d)
        return out


def build_html(
    doc: ContentDoc,
    plan: ContentPlan,
    design: DesignSpec,
    visuals: VisualSet,
    layout: LayoutPlan,
    work_dir: Path,
    *,
    min_body_size_pt: float = 8.5,
) -> str:
    """Render templates/document.html.j2. All user text is autoescaped; every
    element carries ``data-el="<element id>"`` and ``data-blocks="b0001 b0002"``
    so the renderer can build the page map."""
    b = _Builder(doc, plan, design, visuals, layout, work_dir)
    units = [b.unit_view(u, i) for i, u in enumerate(layout.units)]
    section_titles = {
        s.id: (s.title.text if s.title else (doc.block(s.heading_block_id).text if s.heading_block_id else ""))
        for s in plan.sections
    }
    title = (plan.cover.title.text if plan.cover else "") or doc.title or ""
    css = build_css(
        design,
        layout,
        section_titles=section_titles,
        doc_title=title,
        work_dir=Path(work_dir),
        min_body_size_pt=min_body_size_pt,
    )
    anchors: list[str] = []
    for u in units:
        anchors.append(u["id"])
    for eid in b.anchors:
        anchors.extend([eid, f"{eid}-end"])
    env = _env()
    env.globals["plain"] = _plain
    return env.get_template("document.html.j2").render(
        title=title,
        css=Markup(css),
        units=units,
        anchors=anchors,
        lang="en",
    )
