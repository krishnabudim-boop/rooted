"""End-to-end: the REAL pipeline (no stage monkeypatching) with only the LLM faked.

The fake LLM builds schema-valid answers from the real artifacts: ContentDoc ids from ``ingest.json`` and
cluster ids / colors from a fresh ``extract_raw`` of the reference.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

import rooted.pipeline as pipeline
from rooted.cli import app
from rooted.design import extract_raw
from rooted.design.color import delta_e
from rooted.models import (
    Block,
    BlockType,
    Brief,
    BuildReport,
    Callout,
    ChartSeries,
    ChartSpec,
    ChartType,
    ColorOverride,
    ColorRole,
    ColorRoleAssignment,
    ContentDoc,
    ContentPlan,
    CoverPlan,
    DataPoint,
    DesignSpec,
    FontCharacter,
    PageClassification,
    PageType,
    PlannedText,
    ProvenanceKind,
    RawDesignStats,
    ReportProfile,
    RoleAssignment,
    Section,
    TemplateNote,
    TextExcerpt,
    TypeRole,
    VisionDesignNotes,
    VisualKind,
    VisualPlan,
    VisualSpec,
)
from tests.conftest import FakeLLMClient
from tests.pipeline.e2e_inputs import REGIONS, build_docx, build_reference

pytestmark = pytest.mark.render

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "pipeline"
NAVY, GOLD = "#1F2A44", "#C9A227"
runner = CliRunner()


def _excerpt(block: Block, needle: str) -> TextExcerpt:
    i = block.text.index(needle)
    return TextExcerpt(block_id=block.id, start=i, end=i + len(needle), text=needle)


def _verbatim(block: Block, needle: str | None = None) -> PlannedText:
    return PlannedText(text=needle or block.text, provenance=ProvenanceKind.VERBATIM,
                       source=_excerpt(block, needle or block.text))


class DynamicLLM(FakeLLMClient):
    """FakeLLMClient whose per-task responses are computed from the real artifacts at call time."""

    def __init__(self, work_dir: Path, reference: Path, target_pages: int):
        super().__init__()
        self.work_dir, self.reference, self.target_pages = work_dir, reference, target_pages
        self.responses = {
            "brief": self._brief, "design_vision": self._vision,
            "content_plan": self._plan, "visual_plan": self._visuals,
        }

    def _doc(self) -> ContentDoc:
        return ContentDoc.model_validate_json((self.work_dir / "ingest.json").read_text("utf-8"))

    def _brief(self, call) -> Brief:
        return Brief(target_pages=self.target_pages, tone="formal",
                     palette_overrides=[ColorOverride(role=ColorRole.PRIMARY, hex=NAVY, name="navy"),
                                        ColorOverride(role=ColorRole.ACCENT, hex=GOLD, name="gold")])

    def _vision(self, call) -> VisionDesignNotes:
        raw: RawDesignStats = extract_raw(self.reference)
        roles: list[RoleAssignment] = []
        by_size = sorted(raw.font_clusters, key=lambda c: -c.size_pt)
        for c in raw.font_clusters:
            if c.id == raw.body_cluster_id:
                roles.append(RoleAssignment(cluster_id=c.id, role=TypeRole.BODY))
        big = [c for c in by_size if c.size_pt >= 40]
        h1 = [c for c in by_size if 20 <= c.size_pt < 30]
        if big:
            roles.append(RoleAssignment(cluster_id=big[0].id, role=TypeRole.DISPLAY))
        if h1:
            roles.append(RoleAssignment(cluster_id=h1[0].id, role=TypeRole.H1))
        colors = sorted((c for c in raw.colors if c.usage == "fill"), key=lambda c: -c.weight)
        return VisionDesignNotes(
            overall_style="Two-column serif body, deep sans headings, full-bleed cover.",
            page_classifications=[
                PageClassification(page_index=p.index, page_type=PageType.COVER if p.index == 0 else PageType.BODY,
                                   description="page")
                for p in raw.pages],
            role_assignments=roles,
            color_roles=[ColorRoleAssignment(hex=colors[0].hex, role=ColorRole.PRIMARY)] if colors else [],
            font_characters=[FontCharacter(cluster_id=raw.font_clusters[0].id, classification="serif",
                                           similar_open_fonts=["Libre Baskerville"])],
            templates=[TemplateNote(page_type=PageType.COVER, example_page_index=0, columns=1,
                                    shows_header=False, shows_footer=False),
                       TemplateNote(page_type=PageType.BODY, example_page_index=1, columns=2)],
            callout_description="Tinted box with a gold bar on the left edge.",
        )

    def _plan(self, call) -> ContentPlan:
        doc = self._doc()
        heads = [b for b in doc.blocks if b.type == BlockType.HEADING and b.level == 1]
        title, intro_heads = heads[0], heads[1:]
        sections: list[Section] = []
        for i, h in enumerate(intro_heads):
            start = doc.blocks.index(h)
            end = doc.blocks.index(intro_heads[i + 1]) if i + 1 < len(intro_heads) else len(doc.blocks)
            sections.append(Section(id=f"s{i + 1:02d}", title=_verbatim(h), heading_block_id=h.id,
                                    block_ids=[b.id for b in doc.blocks[start:end]], page_budget=1.0))
        subtitle = doc.blocks[doc.blocks.index(title) + 1]
        closing = next(b for b in doc.blocks if b.text.startswith("We will keep every promise"))
        for s in sections:  # the closing paragraph moves into a callout box
            if closing.id in s.block_ids:
                s.block_ids.remove(closing.id)
                s.callouts.append(Callout(id="c01", kind="note", block_ids=[closing.id]))
        return ContentPlan(
            profile=ReportProfile(purpose="Annual review", audience="Donors", report_type="annual_report",
                                  tone="formal", summary="Annual review of a housing and food charity."),
            cover=CoverPlan(title=_verbatim(title), subtitle=_verbatim(subtitle),
                            consumed_block_ids=[title.id, subtitle.id]),
            sections=sections,
        )

    def _visuals(self, call) -> VisualPlan:
        doc = self._doc()
        block = next(b for b in doc.blocks if "5,200" in b.text and b.type == BlockType.PARAGRAPH)
        plan = ContentPlan.model_validate_json((self.work_dir / "content.json").read_text("utf-8"))
        sec = next(s for s in plan.sections if block.id in s.block_ids)
        pts = [DataPoint(value=float(v), raw=f"{v:,}", source=_excerpt(block, f"{v:,}")) for v in REGIONS.values()]
        chart = ChartSpec(chart_type=ChartType.COLUMN, categories=list(REGIONS), unit="families",
                          title=PlannedText(text="Families supported by region",
                                            provenance=ProvenanceKind.GENERATED_LABEL),
                          series=[ChartSeries(name="Families", points=pts)])
        sat = next(b for b in doc.blocks if b.text.startswith("Satisfaction rose from 71%"))
        sat_sec = next(s for s in plan.sections if sat.id in s.block_ids)
        sat_pts = [DataPoint(value=float(v), raw=f"{v}%", source=_excerpt(sat, f"{v}%")) for v in (71, 84, 92)]
        line = ChartSpec(chart_type=ChartType.LINE, categories=["2023", "2024", "2025"], unit="%",
                         title=PlannedText(text="Satisfaction", provenance=ProvenanceKind.GENERATED_LABEL),
                         series=[ChartSeries(name="Satisfaction", points=sat_pts)])
        return VisualPlan(visuals=[VisualSpec(id="v01", kind=VisualKind.CHART, section_id=sec.id,
                                              anchor_block_id=block.id, placement="full_width", size="medium",
                                              priority="required", chart=chart, alt_text="Families by region"),
                                   VisualSpec(id="v02", kind=VisualKind.CHART, section_id=sat_sec.id,
                                              anchor_block_id=sat.id, placement="inline", size="small",
                                              priority="required", chart=line, alt_text="Satisfaction over time")],
                          rationale="Three regional counts and a satisfaction trend")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("­", "")).strip()


def _pdf_text(path: Path) -> str:
    with pymupdf.open(str(path)) as d:
        return _norm(" ".join(p.get_text() for p in d))


@pytest.fixture(scope="module")
def inputs(tmp_path_factory):
    d = tmp_path_factory.mktemp("e2e_inputs")
    return build_docx(d / "e2e_input.docx"), build_reference(d / "e2e_reference.pdf")


def _build(monkeypatch, inputs, tmp_path, target: int, brief: str):
    docx, ref = inputs
    work, out = tmp_path / "work", tmp_path / "out.pdf"
    llm = DynamicLLM(work, ref, target)
    monkeypatch.setattr(pipeline, "build_client", lambda cfg, cache_dir=None: llm)
    args = ["build", str(docx), "--model", str(ref), "--brief", brief, "-o", str(out), "--work-dir", str(work),
            "--offline"]
    res = runner.invoke(app, args)
    return res, llm, work, out, args


def test_e2e_six_pages(monkeypatch, inputs, tmp_path):
    docx, ref = inputs
    res, llm, work, out, args = _build(monkeypatch, inputs, tmp_path, 6, "6 pages, navy and gold palette, formal tone")
    assert res.exit_code == 0, res.output
    assert out.exists()

    with pymupdf.open(str(out)) as d:
        assert d.page_count == 6

    # verbatim: every user paragraph/list item/heading is present in the output text
    doc = ContentDoc.model_validate_json((work / "ingest.json").read_text("utf-8"))
    text = _pdf_text(out)
    missing = [b.id for b in doc.blocks if b.type in (BlockType.PARAGRAPH, BlockType.LIST_ITEM, BlockType.HEADING)
               and _norm(b.text) not in text]
    assert not missing, f"blocks not verbatim in PDF: {missing}"
    for b in doc.blocks:
        if b.type == BlockType.TABLE:
            for row in b.table.rows:
                for cell in row:
                    assert _norm(cell.text) in text

    # chart embedded: the visual was rendered and is inlined in the final HTML
    report = BuildReport.model_validate_json((work / "render.json").read_text("utf-8"))
    vs = (work / "visuals.json").read_text("utf-8")
    assert '"visual_id": "v01"' in vs and '"visual_id": "v02"' in vs
    html = Path(report.render.html_path).read_text("utf-8")
    assert "visuals/v01" in html and "visuals/v02" in html  # two charts embedded
    assert report.fit.achieved_pages == 6 and report.fit.status == "exact"
    with pymupdf.open(str(out)) as d:  # the chart is vector art: three navy bars on the "Our reach" page
        page = next(p for p in d if "Our reach" in p.get_text())
        bars = [dr for dr in page.get_drawings()
                if dr.get("fill") and delta_e("#" + "".join(f"{round(c * 255):02X}" for c in dr["fill"]), NAVY) < 8]
        assert len(bars) >= 3

    # the closing paragraph sits in a tinted callout box
    with pymupdf.open(str(out)) as d:
        page = next(p for p in d if "We will keep every promise" in _norm(p.get_text()))
        hit = page.search_for("We will keep every promise")[0]
        boxes = [dr["rect"] for dr in page.get_drawings()
                 if dr.get("fill") and dr["rect"].width > 150 and dr["rect"].height > 20 and dr["rect"].contains(hit)
                 and min(dr["fill"]) < 0.99]
        assert boxes, "no tinted callout box behind the callout text"

        # the H1 spans both columns: its line is wider than one column
        hpage = next(p for p in d if "Our reach" in p.get_text())
        head = next(b for b in hpage.get_text("dict")["blocks"] for ln in b.get("lines", [])
                    if "Our reach" in "".join(sp["text"] for sp in ln["spans"]))
        # a bold font span exists (headings are bold)
        spans = [sp for p in d for b in p.get_text("dict")["blocks"] for ln in b.get("lines", [])
                 for sp in ln["spans"]]
        assert any(sp["flags"] & 16 or "bold" in sp["font"].lower() for sp in spans)
        hy0, hy1 = head["bbox"][1], head["bbox"][3]
        beside = [ln for blk in hpage.get_text("dict")["blocks"] for ln in blk.get("lines", [])
                  if blk is not head and ln["bbox"][1] < hy1 - 1 and ln["bbox"][3] > hy0 + 1
                  and ln["bbox"][0] > head["bbox"][2] + 5]
        assert not beside, "text sits beside the H1, so it does not span the columns"

    # palette from the brief is used (design spec and ink actually painted)
    design = DesignSpec.model_validate_json((work / "design.json").read_text("utf-8"))
    assert design.color(ColorRole.PRIMARY) == NAVY and design.color(ColorRole.ACCENT) == GOLD
    painted = {c.hex for c in extract_raw(out).colors}
    assert any(delta_e(h, NAVY) < 8 for h in painted), painted
    assert any(delta_e(h, GOLD) < 8 for h in painted), painted
    # and the reference's own colors do not leak in as the primary
    assert delta_e(design.color(ColorRole.PRIMARY), "#0E7C7B") > 20

    # running header/footer sit where the reference measured them (reference: header ~y=33, footer ~y=808)
    if design.header is not None and design.header.y_center_pt is not None:
        with pymupdf.open(str(out)) as d:
            lines = [ln for blk in d[2].get_text("dict")["blocks"] for ln in blk.get("lines", [])]
        head = next(ln for ln in lines if "Annual Review" in "".join(sp["text"] for sp in ln["spans"])
                    and ln["bbox"][1] < 100)
        assert (head["bbox"][1] + head["bbox"][3]) / 2 == pytest.approx(design.header.y_center_pt, abs=4)

    # the reference's two columns carry through
    assert design.grid.columns == 2
    assert any(p.column_estimate >= 2 for p in extract_raw(out).pages)

    # re-run uses the stage cache and makes no new LLM calls
    n_calls = len(llm.calls)
    out.unlink()
    res2 = runner.invoke(app, args)
    assert res2.exit_code == 0, res2.output
    assert res2.output.count("cached") >= 7
    assert len(llm.calls) == n_calls
    assert out.exists()

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out, FIXTURE_DIR / "e2e_output.pdf")
    shutil.copyfile(docx, FIXTURE_DIR / "e2e_input.docx")
    shutil.copyfile(ref, FIXTURE_DIR / "e2e_reference.pdf")


def test_e2e_impossible_target(monkeypatch, inputs, tmp_path):
    res, llm, work, out, _ = _build(monkeypatch, inputs, tmp_path, 1, "1 page, navy and gold, formal tone")
    assert res.exit_code == 0, res.output
    assert out.exists()
    report = BuildReport.model_validate_json((work / "render.json").read_text("utf-8"))
    assert report.fit.target_pages == 1
    assert report.fit.status in ("over", "under")
    assert report.fit.achieved_pages > 1
    assert any("page target not reached" in w for w in report.warnings)
    with pymupdf.open(str(out)) as d:
        assert d.page_count == report.fit.achieved_pages
    # text still untouched
    doc = ContentDoc.model_validate_json((work / "ingest.json").read_text("utf-8"))
    text = _pdf_text(out)
    assert all(_norm(b.text) in text for b in doc.blocks if b.type == BlockType.PARAGRAPH)
