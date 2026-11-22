from __future__ import annotations

import pytest

from rooted.design import analyze_pages, render_thumbnails, synthesize_design
from rooted.llm import TASK_DESIGN_VISION
from rooted.models import (
    ColorRole,
    DesignSpec,
    PageClassification,
    PageType,
    RoleAssignment,
    TypeRole,
    VisionDesignNotes,
)
from tests import factories
from tests.conftest import FakeLLMClient
from tests.design import reference_pdf as ref


def _check_complete(spec: DesignSpec):
    assert {t.role for t in spec.typography} == set(TypeRole)
    assert len(spec.typography) == len(TypeRole)
    for r in (ColorRole.PRIMARY, ColorRole.TEXT, ColorRole.BACKGROUND, ColorRole.SURFACE, ColorRole.RULE,
              ColorRole.ON_PRIMARY):
        assert any(p.role == r for p in spec.palette)
    assert DesignSpec.model_validate_json(spec.model_dump_json()) == spec
    assert spec.template(PageType.BODY) is not None


def test_heuristic_synthesis_without_notes(raw):
    spec = synthesize_design(raw, None)
    _check_complete(spec)
    assert any("Vision analysis unavailable" in w for w in spec.warnings)
    assert spec.style(TypeRole.BODY).size_pt == 10.0
    assert spec.style(TypeRole.BODY).family == "Helvetica"
    assert spec.style(TypeRole.H1).size_pt == 20.0 and spec.style(TypeRole.H1).weight == 700
    assert spec.style(TypeRole.H2).size_pt == 14.0
    assert spec.style(TypeRole.DISPLAY).size_pt == 40.0
    assert spec.style(TypeRole.DISPLAY).color_role == ColorRole.ON_PRIMARY
    assert spec.style(TypeRole.H1).color_role == ColorRole.PRIMARY
    # sizes increase up the ladder
    sizes = [spec.style(r).size_pt for r in (TypeRole.BODY, TypeRole.H3, TypeRole.H2, TypeRole.H1, TypeRole.TITLE,
                                             TypeRole.DISPLAY)]
    assert sizes == sorted(sizes) and len(set(sizes)) == 6
    assert spec.style(TypeRole.CAPTION).size_pt < spec.style(TypeRole.BODY).size_pt


def test_measured_geometry_and_palette(raw):
    spec = synthesize_design(raw, None)
    assert spec.page_size.name == "A4"
    assert spec.margins.left == pytest.approx(ref.LEFT, abs=2) and spec.margins.top == pytest.approx(ref.TOP, abs=2)
    assert spec.grid.columns == 2 and spec.grid.gutter_pt == pytest.approx(ref.GUTTER, abs=2)
    assert spec.color(ColorRole.PRIMARY) == ref.NAVY
    assert spec.color(ColorRole.ACCENT) == ref.GOLD
    assert spec.color(ColorRole.TEXT) == ref.TEXT.upper()
    assert spec.color(ColorRole.TEXT_MUTED) == ref.GRAY
    assert spec.color(ColorRole.SURFACE) == ref.SURFACE
    assert spec.color(ColorRole.ON_PRIMARY) == "#FFFFFF"
    assert spec.color(ColorRole.BACKGROUND) == "#FFFFFF"


def test_running_header_footer_and_templates(raw):
    spec = synthesize_design(raw, None)
    assert [(s.position, s.content) for s in spec.header.slots] == [("left", "{title}")]
    assert spec.header.rule is True
    assert [(s.position, s.content) for s in spec.footer.slots] == [("right", "Page {page}")]
    cover = spec.template(PageType.COVER)
    assert cover.background_role == ColorRole.PRIMARY and not cover.show_header and not cover.show_footer
    assert spec.template(PageType.BODY).columns == 2


def _fake_notes(raw, **over):
    by = {(c.size_pt, c.bold): c.id for c in raw.font_clusters}
    data = dict(
        overall_style="Navy and gold.",
        page_classifications=[PageClassification(page_index=0, page_type=PageType.COVER, description="cover")],
        role_assignments=[RoleAssignment(cluster_id=by[(40.0, True)], role=TypeRole.TITLE),
                          RoleAssignment(cluster_id=by[(20.0, True)], role=TypeRole.H1)],
        color_roles=[], font_characters=[], templates=[],
        callout_description="Light box with a thick gold bar on the left; rounded corners.",
        table_description="Plain header, striped rows.", figure_description="No gridlines, caption above.",
        motifs=["gold bar"])
    data.update(over)
    return VisionDesignNotes(**data)


def test_notes_label_but_do_not_override_measurements(raw):
    notes = _fake_notes(raw)
    spec = synthesize_design(raw, notes)
    _check_complete(spec)
    assert spec.style(TypeRole.TITLE).size_pt == 40.0  # label from vision, number from raw
    assert spec.style(TypeRole.H1).size_pt == 20.0
    assert spec.style_summary == "Navy and gold." and spec.motifs == ["gold bar"]
    assert spec.callout.border_sides == "left" and spec.callout.radius_pt == 6.0
    assert spec.table.header_fill_role is None and spec.table.zebra_fill_role == ColorRole.SURFACE
    assert spec.figure.gridlines is False and spec.figure.caption_position == "above"
    assert not any("unavailable" in w for w in spec.warnings)


def test_notes_cannot_relabel_body_cluster(raw):
    h1 = next(c.id for c in raw.font_clusters if c.size_pt == 20.0)
    notes = _fake_notes(raw, role_assignments=[RoleAssignment(cluster_id=h1, role=TypeRole.BODY)])
    spec = synthesize_design(raw, notes)
    assert spec.style(TypeRole.BODY).size_pt == 10.0
    assert any("measured body cluster" in w for w in spec.warnings)


def test_vision_templates_use_measured_columns_and_background(raw, reference_pdf, tmp_path):
    paths = render_thumbnails(reference_pdf, tmp_path, [0, 2], dpi=40)
    payload = {
        "overall_style": "x", "page_classifications": [], "role_assignments": [], "font_characters": [],
        "color_roles": [{"hex": ref.NAVY, "role": "primary"}, {"hex": ref.GOLD, "role": "accent"}],
        "templates": [
            {"page_type": "cover", "example_page_index": 0, "columns": 3, "background_hex": ref.NAVY,
             "shows_header": True, "shows_footer": True,
             "regions": [{"name": "shape", "kind": "shape", "fill_hex": ref.GOLD,
                          "bbox": {"x0": 0, "y0": 0.83, "x1": 1, "y1": 0.85}}]},
            {"page_type": "body", "example_page_index": 2, "columns": 1},
        ],
    }
    notes = analyze_pages(raw, [(0, paths[0]), (2, paths[1])], FakeLLMClient({TASK_DESIGN_VISION: payload}))
    spec = synthesize_design(raw, notes)
    cover, body = spec.template(PageType.COVER), spec.template(PageType.BODY)
    assert cover.columns == 1 and cover.background_role == ColorRole.PRIMARY
    assert not cover.show_header and not cover.show_footer  # measured: no running text on page 0
    assert cover.regions[0].fill_role == ColorRole.ACCENT
    assert body.columns == 2 and body.show_header and body.show_footer
    assert spec.palette_source(ColorRole.PRIMARY) == "vision" if hasattr(spec, "palette_source") else True
    assert next(p for p in spec.palette if p.role == ColorRole.PRIMARY).source == "vision"


def test_synthesis_from_factory_stats_and_empty_text():
    raw = factories.sample_raw_stats()
    spec = synthesize_design(raw, None)
    _check_complete(spec)
    assert spec.style(TypeRole.H1).size_pt == 20.0 and spec.style(TypeRole.DISPLAY).size_pt == 40.0
    empty = raw.model_copy(update={"font_clusters": [], "body_cluster_id": None, "colors": []})
    spec = synthesize_design(empty, None)
    _check_complete(spec)
    assert any("no extractable text" in w for w in spec.warnings)


def test_measured_line_pitch_and_spacing_replace_heuristics(raw):
    absent = raw.model_copy(update={"body_line_height_pt": None, "paragraph_spacing_pt": None})
    base = synthesize_design(absent, None)
    assert base.style(TypeRole.BODY).line_height == 1.4  # heuristic when nothing measured
    assert base.spacing.paragraph_pt == 8.0
    measured = raw.model_copy(update={"body_line_height_pt": 15.0, "paragraph_spacing_pt": 7.0})
    spec = synthesize_design(measured, None)
    assert spec.style(TypeRole.BODY).line_height == pytest.approx(1.5)
    assert spec.style(TypeRole.BODY).space_after_pt == 7.0
    assert spec.spacing.paragraph_pt == 7.0
    _check_complete(spec)


def test_implausible_pitch_is_clamped(raw):
    spec = synthesize_design(raw.model_copy(update={"body_line_height_pt": 60.0}), None)
    assert spec.style(TypeRole.BODY).line_height == 2.2


def test_callout_style_from_measured_boxes(raw):
    spec = synthesize_design(raw, None)
    assert spec.callout.fill_role == ColorRole.SURFACE
    assert spec.callout.border_sides == "left" and spec.callout.border_role == ColorRole.ACCENT
    assert spec.callout.border_width_pt == pytest.approx(4.0, abs=0.5)


def test_no_boxes_keeps_default_callout(raw):
    spec = synthesize_design(raw.model_copy(update={"filled_boxes": []}), None)
    assert spec.callout.border_sides == "all" and spec.callout.border_width_pt == 0.0


def test_cover_regions_measured_without_vision(raw):
    spec = synthesize_design(raw, None)
    cover = spec.template(PageType.COVER)
    regions = {r.name: r for r in cover.regions}
    assert regions["title"].style_role == TypeRole.DISPLAY
    assert 0.2 <= regions["title"].bbox.y0 <= 0.35  # first title line sits near 250pt of 842
    assert regions["subtitle"].bbox.y0 >= regions["title"].bbox.y1 - 1e-6
    band = regions["shape"]
    assert band.kind == "shape" and band.fill_role == ColorRole.ACCENT
    assert band.bbox.x0 == 0 and band.bbox.x1 == 1 and band.bbox.y0 == pytest.approx(700 / 842, abs=0.01)


def test_cover_regions_added_when_vision_omits_cover_template(raw):
    notes = _fake_notes(raw, templates=[])
    spec = synthesize_design(raw, notes)
    cover = spec.template(PageType.COVER)
    assert cover is not None and any(r.kind == "shape" for r in cover.regions)


def test_vision_cover_without_regions_gets_measured_regions(raw):
    from rooted.models import TemplateNote

    notes = _fake_notes(raw, templates=[TemplateNote(page_type=PageType.COVER, example_page_index=0, columns=1)])
    cover = synthesize_design(raw, notes).template(PageType.COVER)
    assert {"title", "shape"} <= {r.name for r in cover.regions}


def test_derived_heading_roles_inherit_heading_family(raw):
    spec = synthesize_design(raw, None)
    h1 = spec.style(TypeRole.H1)
    for role in (TypeRole.H2, TypeRole.H3, TypeRole.TITLE, TypeRole.LABEL, TypeRole.STAT_VALUE):
        st = spec.style(role)
        assert st.family == h1.family and st.source_font == h1.source_font, role
    assert spec.style(TypeRole.H2).weight == h1.weight
    assert spec.style(TypeRole.BODY).source_font != h1.source_font
    assert spec.style(TypeRole.LEAD).source_font == spec.style(TypeRole.BODY).source_font


def test_footer_cluster_is_running_text_not_body(raw):
    spec = synthesize_design(raw, None)
    foot = [r for r in raw.repeated_texts if r.y_center_pt > 400]
    assert foot and all(r.cluster_id != raw.body_cluster_id for r in foot)
    assert spec.style(TypeRole.FOOTER).size_pt == 8.0
    assert [s.content for s in spec.footer.slots] == ["Page {page}"] or all("{page}" in s.content
                                                                          for s in spec.footer.slots)
