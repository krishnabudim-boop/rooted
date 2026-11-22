from __future__ import annotations

import re

from rooted.layout.compose import compose
from rooted.layout.html import build_css, build_html
from rooted.models import FitParams, FontFace, FontResolution, InlineRun, PageType


def _html(inputs, fit=None, design=None):
    doc, plan, d, visuals, brief, work = inputs
    design = design or d
    layout = compose(doc, plan, design, visuals, brief, fit)
    return build_html(doc, plan, design, visuals, layout, work), layout, design


def test_user_text_is_escaped(inputs):
    doc, plan, design, visuals, brief, work = inputs
    evil = '<script>alert("x")</script> & <b>bold</b>'
    blocks = [b.model_copy(update={"runs": [InlineRun(text=evil)]}) if b.id == "b0005" else b for b in doc.blocks]
    doc = doc.model_copy(update={"blocks": blocks})
    layout = compose(doc, plan, design, visuals, brief)
    html = build_html(doc, plan, design, visuals, layout, work)
    assert "<script>" not in html
    assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt; &amp; &lt;b&gt;bold&lt;/b&gt;" in html


def test_unsafe_link_scheme_dropped(inputs):
    doc, plan, design, visuals, brief, work = inputs
    runs = [
        InlineRun(text="bad", link="javascript:alert(1)"),
        InlineRun(text="good", link="https://example.com/a?b=1&c=2"),
    ]
    blocks = [b.model_copy(update={"runs": runs}) if b.id == "b0005" else b for b in doc.blocks]
    doc = doc.model_copy(update={"blocks": blocks})
    layout = compose(doc, plan, design, visuals, brief)
    html = build_html(doc, plan, design, visuals, layout, work)
    assert "javascript:" not in html
    assert 'href="https://example.com/a?b=1&amp;c=2"' in html


def test_elements_carry_markers(inputs):
    html, layout, _ = _html(inputs)
    for u in layout.units:
        assert f'data-unit="{u.id}"' in html
        for e in u.elements:
            assert f'data-el="{e.id}"' in html
            assert f'href="#{e.id}-end"' in html
    assert 'data-blocks="b0006 b0007"' in html


def test_css_has_named_pages_and_running_boxes(inputs):
    html, layout, design = _html(inputs)
    css = build_css(design, layout, section_titles={"s01": "Introduction"}, doc_title='Annual "Report"')
    for name in ("cover", "opener-s01", "body-s01", "opener-s02", "data-s02", "back"):
        assert re.search(rf"@page {name} \{{", css), name
    assert "background: #1F2A44" in css  # navy cover
    assert "@top-left" in css and '"Introduction"' in css and "counter(page)" in css
    assert 'Annual \\"Report\\"' in css
    assert "@page cover :" not in css
    # cover is full bleed: no margin boxes
    cover_rule = re.search(r"@page cover \{[^}]*\}", css).group(0)
    assert "margin: 0.00pt 0.00pt 0.00pt 0.00pt" in cover_rule
    assert "@top-left" not in css.split("@page cover {")[1].split("@page")[0]


def test_css_variables_follow_fit_params(inputs):
    doc, plan, design, visuals, brief, _ = inputs
    base = build_css(design, compose(doc, plan, design, visuals, brief))
    fit = FitParams(font_scale=1.1, margin_scale=0.9, line_height_scale=0.95)
    tuned = build_css(design, compose(doc, plan, design, visuals, brief, fit))
    assert ".t-body { font-family" in base and "font-size: 10.00pt" in base
    assert "font-size: 11.00pt" in tuned
    assert "margin: 57.60pt 50.40pt 57.60pt 50.40pt" in tuned
    assert re.search(r"line-height: 1\.37[78]", tuned)


def test_min_body_size_clamps_font_scale(inputs):
    doc, plan, design, visuals, brief, _ = inputs
    layout = compose(doc, plan, design, visuals, brief, FitParams(font_scale=0.5))
    css = build_css(design, layout, min_body_size_pt=8.5)
    assert "font-size: 8.50pt" in css


def test_font_faces_from_existing_paths_only(inputs, tmp_path):
    html, layout, design = _html(inputs)
    font = tmp_path / "Montserrat-Regular.ttf"
    font.write_bytes(b"not a real font")
    design = design.model_copy(
        update={
            "fonts": [
                FontResolution(
                    requested="Gotham",
                    resolved_family="Montserrat",
                    source="google",
                    match="similar",
                    faces=[
                        FontFace(family="Montserrat", path=str(font), weight=400),
                        FontFace(family="Montserrat", path=str(tmp_path / "missing.ttf"), weight=700),
                    ],
                )
            ]
        }
    )
    css = build_css(design, layout)
    assert css.count("@font-face") == 1
    assert font.as_uri() in css and "missing.ttf" not in css
    assert '"Montserrat", "Arial", sans-serif' in css  # generic fallback always present


def test_no_fonts_still_has_generic_fallback(inputs):
    html, layout, design = _html(inputs)
    assert "@font-face" not in build_css(design, layout)
    assert "sans-serif" in build_css(design, layout)


def test_inline_opener_markup(inputs):
    html, _, _ = _html(inputs, FitParams(opener_full_page=False))
    assert "inline-opener" in html and 'data-page-type="section_opener"' not in html


def test_forced_break_class(inputs):
    doc, plan, design, visuals, brief, work = inputs
    layout = compose(doc, plan, design, visuals, brief, FitParams(forced_breaks_before=["e0013"]))
    html = build_html(doc, plan, design, visuals, layout, work)
    assert re.search(r'class="el el-\w+[^"]*break-before" id="e0013"', html)


def test_page_types_in_html(inputs):
    html, layout, _ = _html(inputs)
    for pt in (PageType.COVER, PageType.SECTION_OPENER, PageType.BODY, PageType.DATA, PageType.BACK_COVER):
        assert f'data-page-type="{pt.value}"' in html
