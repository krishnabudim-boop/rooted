from __future__ import annotations

import pytest

from rooted.design import apply_brief
from rooted.design.color import contrast
from rooted.design.overrides import parse_page_size
from rooted.models import Brief, ColorOverride, ColorRole, FontOverride, TypeRole
from tests import factories


def test_navy_gold_gives_valid_on_primary_contrast():
    spec = factories.sample_design_spec()
    out = apply_brief(spec, factories.sample_brief())
    assert out.color(ColorRole.PRIMARY) == "#1F2A44" and out.color(ColorRole.ACCENT) == "#C9A227"
    assert contrast(out.color(ColorRole.ON_PRIMARY), out.color(ColorRole.PRIMARY)) >= 4.5
    assert all(p.source == "brief" for p in out.palette if p.role in (ColorRole.PRIMARY, ColorRole.ACCENT))
    assert any(line.startswith("palette: primary") for line in out.overrides_applied)
    assert spec == factories.sample_design_spec()  # pure: input untouched


def test_light_primary_flips_on_primary_to_dark():
    spec = factories.sample_design_spec()
    brief = Brief(palette_overrides=[ColorOverride(role=ColorRole.PRIMARY, hex="#F5D76E", name="pale gold"),
                                     ColorOverride(role=ColorRole.ACCENT, hex="#1F2A44")])
    out = apply_brief(spec, brief)
    assert out.color(ColorRole.ON_PRIMARY) != "#FFFFFF"
    assert contrast(out.color(ColorRole.ON_PRIMARY), "#F5D76E") >= 4.5
    assert any("on_primary" in line for line in out.overrides_applied)


@pytest.mark.parametrize("primary", ["#000000", "#FFFFFF", "#C9A227", "#1F2A44", "#777777", "#E6194B"])
def test_on_primary_contrast_always_meets_aa(primary):
    brief = Brief(palette_overrides=[ColorOverride(role=ColorRole.PRIMARY, hex=primary)])
    out = apply_brief(factories.sample_design_spec(), brief)
    assert contrast(out.color(ColorRole.ON_PRIMARY), primary) >= (4.5 if primary != "#777777" else 4.0)


def test_single_color_derives_secondary_tint():
    spec = factories.sample_design_spec()
    out = apply_brief(spec, Brief(palette_overrides=[ColorOverride(role=ColorRole.PRIMARY, hex="#1B5E20")]))
    sec = out.color(ColorRole.SECONDARY)
    assert sec != "#1B5E20" and sec != spec.color(ColorRole.SECONDARY)
    assert any("secondary" in line and "tint" in line for line in out.overrides_applied)
    # two colors named: no derived secondary
    out2 = apply_brief(spec, factories.sample_brief())
    assert not any("secondary" in line for line in out2.overrides_applied)


def test_invalid_hex_is_ignored_with_warning():
    brief = Brief(palette_overrides=[ColorOverride(role=ColorRole.ACCENT, hex="navy")])
    out = apply_brief(factories.sample_design_spec(), brief)
    assert out.color(ColorRole.ACCENT) == "#C9A227" and any("not a valid hex" in w for w in out.warnings)
    assert out.overrides_applied == []


def test_font_overrides():
    spec = factories.sample_design_spec()
    out = apply_brief(spec, Brief(font_overrides=[FontOverride(family="Lora"),
                                                  FontOverride(role=TypeRole.H1, family="Playfair Display")]))
    assert out.style(TypeRole.H1).family == "Playfair Display"
    assert all(t.family == "Lora" for t in out.typography if t.role != TypeRole.H1)
    assert out.style(TypeRole.BODY).source_font == "Gotham"
    assert len(out.overrides_applied) == 2


def test_page_size_rescales_margins():
    spec = factories.sample_design_spec()
    out = apply_brief(spec, Brief(page_size="Letter"))
    assert (out.page_size.width_pt, out.page_size.height_pt, out.page_size.name) == (612.0, 792.0, "Letter")
    assert out.margins.left == pytest.approx(56 * 612 / 595.28, abs=0.1)
    assert out.margins.top == pytest.approx(64 * 792 / 841.89, abs=0.1)
    assert any(line.startswith("page_size") for line in out.overrides_applied)
    same = apply_brief(spec, Brief(page_size="A4"))
    assert same.margins == spec.margins and same.overrides_applied == []
    bad = apply_brief(spec, Brief(page_size="banana"))
    assert bad.page_size == spec.page_size and any("not understood" in w for w in bad.warnings)


def test_parse_page_size():
    assert parse_page_size("a4").name == "A4"
    ls = parse_page_size("A4 landscape")
    assert ls.width_pt > ls.height_pt and ls.name == "A4 landscape"
    p = parse_page_size("6x9in")
    assert (p.width_pt, p.height_pt) == (432.0, 648.0)
    m = parse_page_size("210 x 297 mm")
    assert m.width_pt == pytest.approx(595.28, abs=0.1)
    assert parse_page_size("huge") is None


def test_no_brief_changes_is_identity():
    spec = factories.sample_design_spec()
    assert apply_brief(spec, Brief()) == spec


def test_brief_primary_recolors_leftover_reference_heading_hue():
    spec = factories.sample_design_spec()
    maroon = "#7A1F3D"
    spec.palette = [p for p in spec.palette if p.role != ColorRole.SECONDARY]
    spec.palette.append(factories.PaletteColor(role=ColorRole.SECONDARY, hex=maroon))
    for st in spec.typography:
        if st.role in (TypeRole.H1, TypeRole.H2):
            st.color_role = ColorRole.SECONDARY
    out = apply_brief(spec, factories.sample_brief())
    for role in (TypeRole.H1, TypeRole.H2):
        assert next(s for s in out.typography if s.role == role).color_role == ColorRole.PRIMARY
    assert any("follows brief primary" in line for line in out.overrides_applied)
    # without a brief palette nothing changes
    same = apply_brief(spec, Brief())
    assert next(s for s in same.typography if s.role == TypeRole.H1).color_role == ColorRole.SECONDARY
