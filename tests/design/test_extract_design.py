from __future__ import annotations

from rooted.design import extract_design
from rooted.errors import LLMRefusalError
from rooted.llm import TASK_DESIGN_VISION
from rooted.models import Brief, BuildConfig, ColorRole, TypeRole
from tests import factories
from tests.conftest import FakeLLMClient
from tests.design import reference_pdf as ref


def _vision_for(raw_stats_cluster_ids):
    def make(_call):
        return {
            "overall_style": "navy and gold", "page_classifications": [], "font_characters": [], "templates": [],
            "role_assignments": [{"cluster_id": i, "role": r} for i, r in raw_stats_cluster_ids],
            "color_roles": [{"hex": ref.NAVY, "role": "primary"}],
        }
    return make


def test_end_to_end_with_fake_llm(reference_pdf, work_dir):
    cfg = BuildConfig(content_path=work_dir / "c.docx", reference_pdf=reference_pdf, work_dir=work_dir)
    llm = FakeLLMClient({TASK_DESIGN_VISION: _vision_for([("f04", "h1"), ("f05", "display")])})
    raw, notes, spec = extract_design(reference_pdf, work_dir, factories.sample_brief(), llm, cfg)
    assert notes is not None and len(llm.calls) == 1 and llm.calls[0].n_images == 4
    assert sorted(p.name for p in (work_dir / "thumbs").glob("*.png")) == [f"page-0{i}.png" for i in range(1, 5)]
    assert raw.pages[0].png_path == "thumbs/page-01.png"
    assert spec.style(TypeRole.BODY).size_pt == 10.0 and spec.style(TypeRole.H1).size_pt == 20.0
    assert spec.color(ColorRole.PRIMARY) == "#1F2A44" and spec.color(ColorRole.ACCENT) == "#C9A227"
    assert spec.overrides_applied  # brief navy/gold logged
    assert {t.role for t in spec.typography} == set(TypeRole)


def test_vision_failure_degrades_to_heuristics(reference_pdf, work_dir):
    cfg = BuildConfig(content_path=work_dir / "c.docx", reference_pdf=reference_pdf, work_dir=work_dir)
    llm = FakeLLMClient({TASK_DESIGN_VISION: LLMRefusalError("no", "x")})
    _, notes, spec = extract_design(reference_pdf, work_dir, Brief(), llm, cfg)
    assert notes is None
    assert any("Vision pass failed" in w for w in spec.warnings)
    assert spec.style(TypeRole.H1).size_pt == 20.0


def test_dry_run_skips_llm(reference_pdf, work_dir):
    cfg = BuildConfig(content_path=work_dir / "c.docx", reference_pdf=reference_pdf, work_dir=work_dir, dry_run=True)
    llm = FakeLLMClient()
    _, notes, spec = extract_design(reference_pdf, work_dir, Brief(), llm, cfg)
    assert notes is None and llm.calls == [] and any("Dry run" in w for w in spec.warnings)
