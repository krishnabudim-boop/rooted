"""Regression tests for the verified review findings (margin boxes, spans, callout tables, lists, images...)."""

from __future__ import annotations

import re
from pathlib import Path

import pymupdf
import pytest

from rooted.fonts import system_index
from rooted.fonts.resolve import _stage_face
from rooted.layout.compose import compose
from rooted.layout.html import _css_str, build_css, build_html
from rooted.models import (
    Block,
    BlockType,
    Callout,
    FontResolution,
    ImageRef,
    InlineRun,
    RunningElement,
    RunningSlot,
    TypeRole,
)
from tests import factories
from tests.layout.helpers import norm, pdf_text


def _html(inputs, doc=None, plan=None, design=None):
    d0, p0, des0, visuals, brief, work = inputs
    doc, plan, design = doc or d0, plan or p0, design or des0
    layout = compose(doc, plan, design, visuals, brief)
    return build_html(doc, plan, design, visuals, layout, work), layout, design


def _with_blocks(doc, new_blocks):
    by = {b.id: b for b in new_blocks}
    return doc.model_copy(update={"blocks": [by.get(b.id, b) for b in doc.blocks]})


# ---- 1. footer rule without header rule ------------------------------------------------------------


def test_footer_rule_without_header_rule(inputs):
    doc, plan, design, visuals, brief, work = inputs
    design = design.model_copy(deep=True)
    design.header.rule = False
    design.footer.rule = True
    layout = compose(doc, plan, design, visuals, brief)
    css = build_css(design, layout)  # used to raise UnboundLocalError
    assert "@bottom-right" in css and "linear-gradient" in css


# ---- 3. H1 spans the columns -----------------------------------------------------------------------


def _with_extra_heading(inputs, level):
    doc, plan, *_ = inputs
    h = Block(id="b0098", type=BlockType.HEADING, level=level, runs=[InlineRun(text="Extra heading")])
    p = Block(id="b0099", type=BlockType.PARAGRAPH, runs=[InlineRun(text="Text after it.")])
    doc = doc.model_copy(update={"blocks": [*doc.blocks, h, p]})
    plan = plan.model_copy(deep=True)
    plan.sections[0].block_ids += ["b0098", "b0099"]  # section s01: two-column body
    return doc, plan


def _heading_classes(html, block_id):
    m = re.search(rf'<div class="el el-heading ([^"]*)"[^>]*data-blocks="{block_id}"', html)
    assert m, block_id
    return m.group(1)


def test_h1_spans_columns_h2_does_not(inputs):
    doc, plan = _with_extra_heading(inputs, 1)
    html, _, _ = _html(inputs, doc=doc, plan=plan)
    assert "span-all" in _heading_classes(html, "b0098")
    css = Path(__file__).parents[2].joinpath("src/rooted/layout/static/base.css").read_text()
    assert ".el-heading.span-all { column-span: all; }" in css
    doc, plan = _with_extra_heading(inputs, 2)  # b0003 etc. stay level 1 -> this one is an H2
    html, _, _ = _html(inputs, doc=doc, plan=plan)
    assert "span-all" not in _heading_classes(html, "b0098")
    assert '<h3 class="t-h2">Extra heading</h3>' in html or '<h3 class="t-h2"' in html


def test_heading_levels_normalised_to_h1(inputs):
    doc, plan = _with_extra_heading(inputs, 2)
    blocks = [b.model_copy(update={"level": 2}) if b.type == BlockType.HEADING and b.id != "b0001" else b
              for b in doc.blocks]  # a document whose top heading level is Heading 2
    html, _, _ = _html(inputs, doc=doc.model_copy(update={"blocks": blocks}), plan=plan)
    assert '<h2 class="t-h1">Extra heading</h2>' in html
    assert "span-all" in _heading_classes(html, "b0098")


# ---- 4. tables and captions inside callouts --------------------------------------------------------


def test_table_in_callout_is_rendered(inputs):
    doc, plan, *_ = inputs
    plan = plan.model_copy(deep=True)
    sec = plan.sections[1]
    sec.block_ids = [i for i in sec.block_ids if i != "b0010"]
    sec.callouts = [Callout(id="c02", kind="note", block_ids=["b0010", "b0011"])]
    html, _, _ = _html(inputs, plan=plan)
    call = html[html.index('class="el el-callout'):]
    call = call[: call.index('id="e0013"') if 'id="e0013"' in call else len(call)]
    for cell in ("Region", "Families", "North", "5,200", "South", "3,100"):
        assert f">{cell}<" in call, cell


def test_caption_in_callout_is_rendered(inputs):
    doc, plan, *_ = inputs
    doc = _with_blocks(doc, [Block(id="b0009", type=BlockType.CAPTION, runs=[InlineRun(text="Figure 9. Reach")])])
    plan = plan.model_copy(deep=True)
    plan.sections[1].callouts = [Callout(id="c02", kind="note", block_ids=["b0009"])]
    plan.sections[1].block_ids = [i for i in plan.sections[1].block_ids if i != "b0009"]
    html, _, _ = _html(inputs, doc=doc, plan=plan)
    assert '<p class="t-caption">Figure 9. Reach</p>' in html


# ---- 5b. opener title shows the whole heading block ------------------------------------------------


def test_opener_title_is_full_heading_text(inputs):
    doc, plan, *_ = inputs
    plan = plan.model_copy(deep=True)
    sec = plan.sections[1]  # heading b0008 "Results"
    sec.title = factories.verbatim(doc, "b0008", "Res")  # an excerpt, not the whole heading
    html, _, _ = _html(inputs, plan=plan)
    opener = html[html.index('<section class="unit fixed pg-opener-s02"'):]
    assert "<p>Results</p>" in opener.split("</section>")[0]


# ---- 6. running header/footer sit where the reference measured them -------------------------------


@pytest.mark.render
def test_running_boxes_positioned_from_measured_centres(inputs, chromium):
    doc, plan, design, visuals, brief, work = inputs
    design = design.model_copy(deep=True)
    design.header = RunningElement(slots=[RunningSlot(position="left", content="RUNHEAD")], style_role=TypeRole.HEADER,
                                   rule=True, y_center_pt=33.0)
    design.footer = RunningElement(slots=[RunningSlot(position="center", content="RUNFOOT")],
                                   style_role=TypeRole.FOOTER, y_center_pt=808.0)
    layout = compose(doc, plan, design, visuals, brief)
    html = build_html(doc, plan, design, visuals, layout, work)
    chromium.render(html, base_url=work, pdf_path=work / "m.pdf", html_path=work / "m.html")
    with pymupdf.open(str(work / "m.pdf")) as d:
        page = d[2]
        head = page.search_for("RUNHEAD")
        foot = page.search_for("RUNFOOT")
    assert head and foot
    assert (head[0].y0 + head[0].y1) / 2 == pytest.approx(33.0, abs=3)
    assert (foot[0].y0 + foot[0].y1) / 2 == pytest.approx(808.0, abs=3)


# ---- 7. lists -------------------------------------------------------------------------------------


def _list_doc(inputs, specs):
    doc, plan, *_ = inputs
    items = [
        Block(id=f"l{i:03d}", type=BlockType.LIST_ITEM, level=lvl, ordered=ordered, list_id=lid, list_label=label,
              runs=[InlineRun(text=f"item {i}")])
        for i, (lid, ordered, label, lvl) in enumerate(specs)
    ]
    blocks = list(doc.blocks) + items
    doc = doc.model_copy(update={"blocks": blocks})
    plan = plan.model_copy(deep=True)
    plan.sections[1].block_ids += [i.id for i in items]
    return doc, plan, items


def test_adjacent_lists_split_on_list_id_and_kind(inputs):
    doc, plan, items = _list_doc(
        inputs, [("1", False, None, 0), ("2", True, "1.", 0), ("2", True, "2.", 0), ("3", True, "1.", 0),
                 ("3", True, "a)", 1)])
    _, layout, _ = _html(inputs, doc=doc, plan=plan)
    groups = [e.block_ids for u in layout.units for e in u.elements if e.kind == "list" and e.block_ids[0] in
              {i.id for i in items}]
    assert groups == [["l000"], ["l001", "l002"], ["l003", "l004"]]


def test_stored_list_labels_are_rendered(inputs):
    doc, plan, items = _list_doc(inputs, [("2", True, "3.", 0), ("2", True, "b)", 1)])
    html, _, _ = _html(inputs, doc=doc, plan=plan)
    assert '<span class="lbl">3.</span>item 0' in html
    assert '<span class="lbl">b)</span>item 1' in html


def test_callout_list_items_keep_labels_and_bullets(inputs):
    doc, plan, items = _list_doc(inputs, [("2", True, "4.", 0), ("1", False, None, 0)])
    plan.sections[1].block_ids = [i for i in plan.sections[1].block_ids if i not in {x.id for x in items}]
    plan.sections[1].callouts = [Callout(id="c02", kind="note", block_ids=[i.id for i in items])]
    html, _, _ = _html(inputs, doc=doc, plan=plan)
    assert '<span class="lbl">4.</span> item 0' in html
    assert '<span class="lbl">&bull;</span> item 1' in html


# ---- 8. image sizing ------------------------------------------------------------------------------


def test_image_uses_docx_display_size_and_is_height_capped(inputs):
    doc, plan, design, visuals, brief, work = inputs
    (work / "assets").mkdir()
    (work / "assets" / "i.png").write_bytes(b"x")
    img = Block(id="b0099", type=BlockType.IMAGE, image=ImageRef(
        path="assets/i.png", mime="image/png", width_px=3000, height_px=2000, width_pt=200.0, height_pt=100.0))
    doc = doc.model_copy(update={"blocks": [*doc.blocks, img]})
    plan = plan.model_copy(deep=True)
    plan.sections[0].block_ids.append("b0099")
    html, _, _ = _html(inputs, doc=doc, plan=plan)
    assert 'style="width: 200.0pt"' in html  # not 3000px * 0.75
    css = Path(__file__).parents[2].joinpath("src/rooted/layout/static/base.css").read_text()
    assert "max-height: calc(var(--content-h) - 2em)" in css and "object-fit: contain" in css


# ---- 9. misc --------------------------------------------------------------------------------------


def test_css_str_escapes_lt():
    s = _css_str("a</style><b>")
    assert "<" not in s and "\\3C " in s


def test_cover_title_preferred_over_stale_docx_title(inputs):
    doc, *_ = inputs
    doc = doc.model_copy(update={"title": "Stale Word Title"})
    html, _, _ = _html(inputs, doc=doc)
    assert "<title>Annual Impact Report 2025</title>" in html and "Stale Word Title" not in html


def test_cover_shape_regions_are_filled_boxes(inputs):
    html, _, design = _html(inputs)
    m = re.search(r'class="region shape[^"]*" style="([^"]*)"', html)
    assert m and "background: #C9A227" in m.group(1)


# ---- 2. bold survives for macOS .ttc families -----------------------------------------------------


@pytest.mark.render
def test_system_helvetica_ttc_renders_bold(inputs, chromium):
    if not Path("/System/Library/Fonts/Helvetica.ttc").exists():
        pytest.skip("no system Helvetica.ttc")
    doc, plan, design, visuals, brief, work = inputs
    entries = system_index.system_index().find("Helvetica")
    faces = system_index.select_faces(entries, [400, 700], False)
    assert {f.weight for f in faces} >= {400, 700} and any(f.number for f in faces)
    fonts_dir = work / "fonts"
    fonts_dir.mkdir()
    staged = [_stage_face(f, fonts_dir) for f in faces]
    design = design.model_copy(deep=True)
    design.typography = [t.model_copy(update={"family": "Helvetica", "fallback_stack": ["sans-serif"]})
                         for t in design.typography]
    design.fonts = [FontResolution(requested="Helvetica", resolved_family="Helvetica", source="system",
                                   match="exact", faces=staged)]
    layout = compose(doc, plan, design, visuals, brief)
    html = build_html(doc, plan, design, visuals, layout, work)
    chromium.render(html, base_url=work, pdf_path=work / "h.pdf", html_path=work / "h.html")
    with pymupdf.open(str(work / "h.pdf")) as d:
        spans = [s for p in d for b in p.get_text("dict")["blocks"] for ln in b.get("lines", [])
                 for s in ln["spans"] if "12,400" in s["text"]]
        text = norm(pdf_text(work / "h.pdf"))
    assert spans and ("bold" in spans[0]["font"].lower() or spans[0]["flags"] & 16), spans
    assert "Annual Impact Report 2025" in text
