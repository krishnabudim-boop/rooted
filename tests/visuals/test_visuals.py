from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from rooted.errors import LLMRefusalError
from rooted.models import (
    Block,
    BlockType,
    ChartSeries,
    ChartSpec,
    ChartType,
    ColorRole,
    DataPoint,
    DecorSpec,
    DiagramEdge,
    DiagramNode,
    DiagramSpec,
    ImageRef,
    PlannedText,
    ProvenanceKind,
    VisualKind,
    VisualPlan,
    VisualSpec,
    VisualsPolicy,
)
from rooted.visuals import (
    ICON_NAMES,
    VISUAL_PLAN_SYSTEM,
    build_visuals,
    find_numeric_candidates,
    plan_visuals,
    render_visuals,
    validate_visual_plan,
)
from rooted.visuals.provenance import parse_number
from tests import factories as F
from tests.conftest import FakeLLMClient

doc = F.sample_content_doc()
HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")


def pt(text: str, doc_=doc, bid: str = "b0009") -> PlannedText:
    return F.verbatim(doc_, bid, text)


def dp(raw: str, value: float, bid: str = "b0009") -> DataPoint:
    return DataPoint(value=value, raw=raw, source=F.excerpt(doc, bid, raw))


def chart_spec(ctype=ChartType.COLUMN, multi=False) -> ChartSpec:
    cats = ["North", "Central", "South"]
    pts = [dp("5,200", 5200), dp("4,100", 4100), dp("3,100", 3100)]
    series = [ChartSeries(name="Families", points=pts)]
    if multi:
        series.append(ChartSeries(name="Again", points=[p.model_copy(deep=True) for p in pts]))
    return ChartSpec(chart_type=ctype, categories=cats, series=series, unit="families")


def vs(chart: ChartSpec | None = None, vid="v01", **kw) -> VisualSpec:
    return VisualSpec(id=vid, kind=VisualKind.CHART, section_id="s02", chart=chart or chart_spec(), **kw)


def validate(*visuals):
    return validate_visual_plan(VisualPlan(visuals=list(visuals)), doc)


# ---------------------------------------------------------------- provenance


def test_valid_chart_passes():
    plan, rej = validate(vs())
    assert not rej and len(plan.visuals) == 1


def test_fabricated_value_rejected():
    c = chart_spec()
    c.series[0].points[0] = DataPoint(value=4700, raw="4,700", source=c.series[0].points[0].source)
    plan, rej = validate(vs(c))
    assert not plan.visuals and "4,700" in rej[0].reason


def test_fabricated_value_with_forged_source_rejected():
    c = chart_spec()
    p = c.series[0].points[0]
    c.series[0].points[0] = DataPoint(value=47, raw="47", source=p.source.model_copy(update={"text": "47"}))
    _, rej = validate(vs(c))
    assert rej


def test_partial_number_token_rejected():
    c = chart_spec()
    # "5" is a substring of "5,200" but not a number the user wrote there
    c.series[0].points[0] = DataPoint(value=5, raw="5", source=F.excerpt(doc, "b0009", "5"))
    _, rej = validate(vs(c))
    assert rej


def test_raw_value_mismatch_rejected():
    c = chart_spec()
    c.series[0].points[1] = dp("4,100", 4200)
    _, rej = validate(vs(c))
    assert rej and "parses to" in rej[0].reason


def test_parse_number_scaling():
    assert parse_number("3.1M") == pytest.approx(3_100_000)
    assert parse_number("$3.1M") == pytest.approx(3_100_000)
    assert parse_number("12k") == 12_000
    assert parse_number("2.5 billion") == 2.5e9
    assert parse_number("42%") == 42
    assert parse_number("5,200") == 5200
    assert parse_number("abc") is None


def test_scaled_raw_accepted():
    d = F.sample_content_doc()
    d.blocks.append(Block(id="b0099", type=BlockType.PARAGRAPH, runs=[
        __import__("rooted.models", fromlist=["InlineRun"]).InlineRun(text="Raised $3.1M in 2024 and $2M in 2023")]))
    pts = [DataPoint(value=3_100_000, raw="$3.1M", source=F.excerpt(d, "b0099", "$3.1M")),
           DataPoint(value=2_000_000, raw="$2M", source=F.excerpt(d, "b0099", "$2M"))]
    ch = ChartSpec(chart_type=ChartType.COLUMN, categories=["2024", "2023"],
                   series=[ChartSeries(name="Raised", points=pts)])
    plan, rej = validate_visual_plan(VisualPlan(visuals=[vs(ch)]), d)
    assert plan.visuals and not rej


def test_series_length_mismatch_rejected():
    c = chart_spec()
    c.series[0].points.pop()
    _, rej = validate(vs(c))
    assert rej and "points" in rej[0].reason


def test_category_not_in_text_rejected():
    c = chart_spec()
    c.categories[2] = "Atlantis"
    _, rej = validate(vs(c))
    assert rej and "Atlantis" in rej[0].reason


def test_single_point_chart_rejected_but_stat_tiles_ok():
    c = ChartSpec(chart_type=ChartType.COLUMN, categories=["North"],
                  series=[ChartSeries(name="x", points=[dp("5,200", 5200)])])
    assert validate(vs(c))[1]
    c.chart_type = ChartType.STAT_TILES
    assert not validate(vs(c))[1]


def test_generated_title_with_invented_number_rejected():
    c = chart_spec()
    c.title = PlannedText(text="Top 7 regions", provenance=ProvenanceKind.GENERATED_LABEL)
    assert validate(vs(c))[1]
    c.title = PlannedText(text="Families by region", provenance=ProvenanceKind.GENERATED_LABEL)
    assert not validate(vs(c))[1]


def test_diagram_verbatim_label_must_be_substring():
    def diag(label):
        return VisualSpec(id="d1", kind=VisualKind.DIAGRAM, diagram=DiagramSpec(kind="process", nodes=[
            DiagramNode(id="a", label=F.verbatim(doc, "b0006", "Food security")),
            DiagramNode(id="b", label=PlannedText(text=label, provenance=ProvenanceKind.VERBATIM)),
        ], edges=[DiagramEdge(source="a", target="b")]))
    assert not validate(diag("Housing support"))[1]
    assert validate(diag("Housing subsidies"))[1]


def test_diagram_bad_edge_and_unknown_icon_rejected():
    d = VisualSpec(id="d1", kind=VisualKind.DIAGRAM, diagram=DiagramSpec(kind="process", nodes=[
        DiagramNode(id="a", label=F.verbatim(doc, "b0006", "Food security"))],
        edges=[DiagramEdge(source="a", target="zz")]))
    assert validate(d)[1]
    assert validate(VisualSpec(id="i", kind=VisualKind.ICON, icon_name="nope"))[1]
    assert not validate(VisualSpec(id="i", kind=VisualKind.ICON, icon_name="home"))[1]


def test_missing_spec_and_duplicate_id_rejected():
    assert validate(VisualSpec(id="c", kind=VisualKind.CHART))[1]
    assert validate(vs(), vs())[1][0].reason == "duplicate visual id"


# ---------------------------------------------------------------- rendering


def render_one(spec: VisualSpec, tmp_path: Path, design=None):
    design = design or F.sample_design_spec()
    out = render_visuals(VisualPlan(visuals=[spec]), design, doc, tmp_path / "visuals")
    assert len(out) == 1
    return out[0], tmp_path / out[0].path


@pytest.mark.parametrize("ctype", list(ChartType))
def test_each_chart_type_renders_valid_palette_svg(ctype, tmp_path):
    design = F.sample_design_spec()
    rv, path = render_one(vs(chart_spec(ctype, multi=ctype == ChartType.STACKED_COLUMN)), tmp_path, design)
    root = ET.fromstring(path.read_text("utf-8"))
    assert root.tag.endswith("svg")
    text = path.read_text("utf-8")
    palette = {c.hex.upper() for c in design.palette}
    used = {h.upper() for h in HEX.findall(text)}
    assert used and used <= palette, used - palette
    assert "Montserrat" in text
    families = set(re.findall(r"font-family[=:]\s*[\"']?([^\"';>]+)", text))
    assert all("Montserrat" in fam for fam in families), families
    assert rv.width_pt > 0 and rv.height_pt > 0
    # live text carries the user's own numbers
    assert "5,200" in text


def test_chart_text_is_live_and_user_numbers_only(tmp_path):
    _, path = render_one(vs(chart_spec()), tmp_path)
    text = path.read_text("utf-8")
    assert "<text" in text
    assert "4,100" in text and "3,100" in text


def test_chart_width_follows_size(tmp_path):
    design = F.sample_design_spec()
    widths = {}
    for size in ("small", "medium", "large"):
        rv, _ = render_one(vs(chart_spec(), size=size), tmp_path / size, design)
        widths[size] = rv.width_pt
    full = design.page_size.width_pt - design.margins.left - design.margins.right
    assert widths["small"] < widths["medium"] < widths["large"]
    assert widths["large"] == pytest.approx(full, abs=0.01)
    assert widths["small"] == pytest.approx(full / 3, abs=0.01)


@pytest.mark.parametrize("ctype", [ChartType.COLUMN, ChartType.DONUT, ChartType.LINE, ChartType.STAT_TILES])
def test_chart_svg_deterministic(ctype, tmp_path):
    a = render_one(vs(chart_spec(ctype)), tmp_path / "a")[1].read_text("utf-8")
    b = render_one(vs(chart_spec(ctype)), tmp_path / "b")[1].read_text("utf-8")
    assert a == b


def test_highlight_category_changes_output(tmp_path):
    c = chart_spec()
    plain = render_one(vs(c), tmp_path / "a")[1].read_text("utf-8")
    c2 = chart_spec()
    c2.highlight_category = 0
    hi = render_one(vs(c2), tmp_path / "b")[1].read_text("utf-8")
    assert plain != hi


@pytest.mark.parametrize("kind", ["process", "cycle", "timeline", "hierarchy", "comparison"])
def test_diagram_kinds_render(kind, tmp_path):
    nodes = [DiagramNode(id=f"n{i}", label=PlannedText(text=t, provenance=ProvenanceKind.GENERATED_LABEL),
                         detail=(PlannedText(text="Short detail text", provenance=ProvenanceKind.GENERATED_LABEL)
                                 if i == 0 else None))
             for i, t in enumerate(["Assess needs", "Match housing", "Provide support", "Follow up", "Review"])]
    spec = VisualSpec(id="d1", kind=VisualKind.DIAGRAM, diagram=DiagramSpec(kind=kind, nodes=nodes,
                      edges=[DiagramEdge(source="n0", target="n1"), DiagramEdge(source="n0", target="n2"),
                             DiagramEdge(source="n2", target="n3", label="then")] if kind == "hierarchy" else []))
    design = F.sample_design_spec()
    rv, path = render_one(spec, tmp_path, design)
    text = path.read_text("utf-8")
    ET.fromstring(text)
    assert {h.upper() for h in HEX.findall(text)} <= {c.hex.upper() for c in design.palette}
    assert "Assess" in text and "Review" in text
    assert rv.height_pt > 0


@pytest.mark.parametrize("motif", ["band", "corner", "dots", "waves", "diagonal", "grid"])
def test_decor_and_cover_art_deterministic_and_seeded(motif, tmp_path):
    design = F.sample_design_spec()

    def spec(seed, kind=VisualKind.DECOR):
        return VisualSpec(id="x", kind=kind, size="large", decor=DecorSpec(
            motif=motif, color_roles=[ColorRole.PRIMARY, ColorRole.ACCENT], seed=seed))
    for kind in (VisualKind.DECOR, VisualKind.COVER_ART):
        a = render_one(spec(1, kind), tmp_path / "a", design)[1].read_text("utf-8")
        b = render_one(spec(1, kind), tmp_path / "b", design)[1].read_text("utf-8")
        ET.fromstring(a)
        assert a == b
        assert {h.upper() for h in HEX.findall(a)} <= {c.hex.upper() for c in design.palette}
    if motif in ("dots", "waves", "diagonal", "grid", "band"):
        c = render_one(spec(2), tmp_path / "c", design)[1].read_text("utf-8")
        assert c != render_one(spec(1), tmp_path / "d", design)[1].read_text("utf-8")


def test_cover_art_is_page_sized(tmp_path):
    design = F.sample_design_spec()
    rv, _ = render_one(VisualSpec(id="c", kind=VisualKind.COVER_ART, decor=DecorSpec(
        motif="waves", color_roles=[ColorRole.ACCENT], seed=1)), tmp_path, design)
    assert (rv.width_pt, rv.height_pt) == (design.page_size.width_pt, design.page_size.height_pt)


def test_icons_all_render(tmp_path):
    assert len(ICON_NAMES) >= 24
    for name in ICON_NAMES:
        rv, path = render_one(VisualSpec(id=f"i_{name}", kind=VisualKind.ICON, icon_name=name), tmp_path)
        ET.fromstring(path.read_text("utf-8"))


def test_user_image_referenced(tmp_path):
    d = F.sample_content_doc()
    d.blocks.append(Block(id="b0100", type=BlockType.IMAGE, image=ImageRef(
        path="assets/img1.png", mime="image/png", width_px=800, height_px=400)))
    design = F.sample_design_spec()
    spec = VisualSpec(id="img_b0100", kind=VisualKind.USER_IMAGE, image_block_id="b0100", size="large")
    out = render_visuals(VisualPlan(visuals=[spec]), design, d, tmp_path / "visuals")
    assert out[0].path == "assets/img1.png"
    assert out[0].height_pt == pytest.approx(out[0].width_pt / 2, abs=0.01)


def test_render_failure_is_skipped(tmp_path):
    bad = VisualSpec(id="bad", kind=VisualKind.CHART)  # no chart spec
    out = render_visuals(VisualPlan(visuals=[bad, vs()]), F.sample_design_spec(), doc, tmp_path / "visuals")
    assert [r.visual_id for r in out] == ["v01"]


# ---------------------------------------------------------------- planner


def planner_args(policy=VisualsPolicy.NORMAL):
    brief = F.sample_brief().model_copy(update={"visuals_policy": policy})
    return doc, F.sample_content_plan(doc), F.sample_design_spec(), brief


def test_find_numeric_candidates():
    cands = find_numeric_candidates(doc)
    raws = {(c["block_id"], c["raw"]) for c in cands}
    assert ("b0004", "12,400") in raws and ("b0004", "18%") in raws
    assert ("b0009", "5,200") in raws and ("b0010", "4,100") in raws
    kinds = {c["raw"]: c["kind"] for c in cands}
    assert kinds["18%"] == "percent" and kinds["2025"] == "year"
    assert all(c["context"] for c in cands)


def test_policy_none_makes_no_llm_call():
    llm = FakeLLMClient()
    d = F.sample_content_doc()
    d.blocks.append(Block(id="b0100", type=BlockType.IMAGE, image=ImageRef(
        path="assets/i.png", mime="image/png", width_px=500, height_px=300)))
    _, plan, design, brief = planner_args(VisualsPolicy.NONE)
    out = plan_visuals(d, plan, design, brief, llm)
    assert llm.calls == []
    assert [v.kind for v in out.visuals] == [VisualKind.USER_IMAGE]


def test_plan_uses_fixture_prompt_and_adds_user_images():
    llm = FakeLLMClient()
    d = F.sample_content_doc()
    d.blocks.append(Block(id="b0100", type=BlockType.IMAGE, image=ImageRef(
        path="assets/i.png", mime="image/png", width_px=500, height_px=300)))
    _, plan, design, brief = planner_args()
    out = plan_visuals(d, plan, design, brief, llm)
    call = llm.calls_for("visual_plan")[0]
    assert call.system == VISUAL_PLAN_SYSTEM and "NEVER INVENT DATA" in call.system
    assert "5,200" in call.prompt and "ICON_NAMES" in call.prompt and "home" in call.prompt
    ids = [v.id for v in out.visuals]
    assert "v01" in ids and "img_b0100" in ids


def test_budget_caps_charts_per_section():
    chart2 = vs(vid="v02")
    chart3 = vs(vid="v03")
    one = F.sample_content_plan(doc)
    for s in one.sections:
        s.page_budget = 1.0
    llm = FakeLLMClient({"visual_plan": VisualPlan(visuals=[vs(), chart2, chart3])})
    brief = F.sample_brief()
    out = plan_visuals(doc, one, F.sample_design_spec(), brief, llm)
    assert [v.id for v in out.visuals] == ["v01"]


def test_refusal_degrades_to_user_images():
    llm = FakeLLMClient({"visual_plan": LLMRefusalError("no")})
    _, plan, design, brief = planner_args()
    out = plan_visuals(doc, plan, design, brief, llm)
    assert out.visuals == [] and "refused" in out.rationale


# ---------------------------------------------------------------- end to end


def test_build_visuals_sample(work_dir):
    llm = FakeLLMClient()
    _, plan, design, brief = planner_args()
    vset = build_visuals(doc, plan, design, brief, llm, work_dir)
    assert not vset.rejected
    assert {r.visual_id for r in vset.rendered} == {v.id for v in vset.plan.visuals}
    for r in vset.rendered:
        assert (work_dir / r.path).exists()
    assert vset.rendered_for("v01").path == "visuals/v01.svg"


def test_build_visuals_rejects_invented_chart(work_dir):
    bad = chart_spec()
    bad.series[0].points[0] = DataPoint(value=9999, raw="9,999", source=bad.series[0].points[0].source)
    llm = FakeLLMClient({"visual_plan": VisualPlan(visuals=[vs(bad)])})
    _, plan, design, brief = planner_args()
    vset = build_visuals(doc, plan, design, brief, llm, work_dir)
    assert vset.plan.visuals == [] and vset.rendered == []
    assert vset.rejected[0].visual_id == "v01"


def test_prompts_are_stable_text():
    assert VISUAL_PLAN_SYSTEM == VISUAL_PLAN_SYSTEM.strip() + "\n"
