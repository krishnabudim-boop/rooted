from __future__ import annotations

import pytest

import rooted.content as content_pkg
import rooted.design as design_pkg
import rooted.layout as layout_pkg
from rooted.cache import WorkDir
from rooted.errors import InputError, LLMRefusalError, StageError
from rooted.models import STAGE_ORDER, BuildReport, Stage
from rooted.pipeline import plan_dry_run, run_pipeline, run_stage
from tests.conftest import FakeLLMClient

ALL_ARTIFACTS = [
    "ingest.json",
    "brief.json",
    "design_raw.json",
    "design.json",
    "content.json",
    "visuals.json",
    "layout.json",
    "render.json",
    "manifest.json",
]


def test_full_pipeline_writes_artifacts_and_report(config, stages):
    report = run_pipeline(config, FakeLLMClient())
    assert isinstance(report, BuildReport)
    for name in ALL_ARTIFACTS:
        assert (config.work_dir / name).exists(), name
    assert config.output_path.read_bytes().startswith(b"%PDF")
    assert report.output_pdf == str(config.output_path)
    assert [r.stage for r in WorkDir(config.work_dir).manifest().records] == STAGE_ORDER


def test_stage_order_and_wiring(config, stages):
    run_pipeline(config, FakeLLMClient())
    order = [c for c in stages.log if c in {"ingest", "brief", "design", "content", "visuals", "compose", "fit"}]
    assert order == ["ingest", "brief", "design", "content", "visuals", "compose", "fit", "compose"]
    assert stages.fit_args["target_pages"] == 8  # from the Brief
    assert "fonts" in stages.log


def test_rerun_is_fully_cached(config, stages):
    run_pipeline(config, FakeLLMClient())
    stages.log.clear()
    report = run_pipeline(config, FakeLLMClient())
    assert stages.log == []
    assert report is not None


def test_brief_change_invalidates_downstream_only(config, stages):
    run_pipeline(config, FakeLLMClient())
    stages.log.clear()
    config2 = config.model_copy(update={"brief_text": "12 pages, red"})
    run_pipeline(config2, FakeLLMClient())
    assert "ingest" not in stages.log
    assert stages.log[0] == "brief"
    # brief artifact bytes are the same fake brief except raw_text -> everything downstream reruns
    assert "design" in stages.log and "fit" in stages.log


def test_content_file_change_reruns_everything(config, stages):
    run_pipeline(config, FakeLLMClient())
    stages.log.clear()
    config.content_path.write_bytes(b"different docx")
    run_pipeline(config, FakeLLMClient())
    assert stages.log[0] == "ingest"
    assert "fit" in stages.log


def test_hand_edited_artifact_invalidates_downstream(config, stages):
    run_pipeline(config, FakeLLMClient())
    stages.log.clear()
    p = config.work_dir / "content.json"
    p.write_text(p.read_text("utf-8").replace("Annual Impact Report 2025", "Edited Title", 1))
    run_pipeline(config, FakeLLMClient())
    assert "content" not in stages.log  # the edit itself is kept
    assert "visuals" in stages.log and "fit" in stages.log
    assert "Edited Title" in p.read_text("utf-8")


def test_from_stage_reruns_that_stage_and_after(config, stages):
    run_pipeline(config, FakeLLMClient())
    stages.log.clear()
    run_pipeline(config.model_copy(update={"from_stage": Stage.CONTENT}), FakeLLMClient())
    assert stages.log[0] == "content"
    assert "design" not in stages.log and "brief" not in stages.log
    assert "visuals" in stages.log and "fit" in stages.log


def test_force_reruns_everything(config, stages):
    run_pipeline(config, FakeLLMClient())
    stages.log.clear()
    run_pipeline(config.model_copy(update={"force": True}), FakeLLMClient())
    for s in ("ingest", "brief", "design", "content", "visuals", "compose", "fit"):
        assert s in stages.log


def test_stop_after_stage(config, stages):
    result = run_pipeline(config.model_copy(update={"stop_after": Stage.CONTENT}), FakeLLMClient())
    assert result is None
    assert (config.work_dir / "content.json").exists()
    assert not (config.work_dir / "visuals.json").exists()
    assert "fit" not in stages.log
    assert not config.output_path.exists()


def test_dry_run_makes_no_llm_calls_and_no_pdf(config, stages, capsys):
    config = config.model_copy(update={"dry_run": True})
    llm = FakeLLMClient()
    assert run_pipeline(config, llm) is None
    assert llm.calls == []
    assert not config.output_path.exists()
    assert "content" not in stages.log and "design" not in stages.log  # extract_design (LLM) never called
    plan = plan_dry_run(config)
    assert [c.task for c in plan.calls] == ["brief", "design_vision", "content_plan", "visual_plan"]
    assert plan.estimated_pages is not None and plan.reference_pages == 2
    assert plan.calls[1].images == 2


def test_dry_run_without_brief_skips_brief_call(config, stages):
    plan = plan_dry_run(config.model_copy(update={"brief_text": "", "dry_run": True}))
    assert "brief" not in [c.task for c in plan.calls]


def test_run_stage_runs_one_stage_from_workdir(config, stages):
    run_pipeline(config.model_copy(update={"stop_after": Stage.DESIGN}), FakeLLMClient())
    stages.log.clear()
    run_stage(Stage.CONTENT, config, FakeLLMClient())
    assert stages.log == ["content"]


def test_run_stage_missing_input_is_friendly(config, stages):
    with pytest.raises(StageError, match="missing"):
        run_stage(Stage.CONTENT, config, FakeLLMClient())


def test_missing_input_file(config, stages):
    config.content_path.unlink()
    with pytest.raises(InputError):
        run_pipeline(config, FakeLLMClient())


def test_brief_refusal_degrades_to_regex(config, stages, monkeypatch):
    calls = []

    def parse(text, llm):
        calls.append(llm)
        if llm is not None:
            raise LLMRefusalError("no")
        from tests import factories

        return factories.sample_brief()

    monkeypatch.setattr(content_pkg, "parse_brief", parse)
    report = run_pipeline(config, FakeLLMClient())
    assert calls[-1] is None
    assert any("regex-only" in w for w in report.warnings)


def test_unreached_page_target_warns(config, stages, monkeypatch):
    from rooted.models import FitReport

    orig = layout_pkg.fit_to_pages

    def fit(render_fn, **kw):
        lay, res, rep = orig(render_fn, **kw)
        return lay, res, FitReport(target_pages=8, achieved_pages=9, status="over", final=rep.final)

    monkeypatch.setattr(layout_pkg, "fit_to_pages", fit)
    report = run_pipeline(config, FakeLLMClient())
    assert any("page target not reached" in w for w in report.warnings)


def test_design_stage_passes_font_hints_and_chars(config, stages, monkeypatch):
    from rooted.models import FontCharacter, VisionDesignNotes
    from tests import factories

    raw = factories.sample_raw_stats()
    cid = raw.font_clusters[0].id
    notes = VisionDesignNotes(
        overall_style="x",
        page_classifications=[],
        role_assignments=[],
        color_roles=[],
        font_characters=[FontCharacter(cluster_id=cid, classification="sans", similar_open_fonts=["Inter"])],
        templates=[],
    )
    monkeypatch.setattr(design_pkg, "extract_design", lambda *a, **k: (raw, notes, factories.sample_design_spec()))
    seen = {}
    import rooted.fonts as fonts_pkg

    def resolve(spec, **kw):
        seen.update(kw)
        return spec

    monkeypatch.setattr(fonts_pkg, "resolve_design_fonts", resolve)
    run_pipeline(config.model_copy(update={"stop_after": Stage.DESIGN}), FakeLLMClient())
    assert seen["hints"] == {raw.font_clusters[0].font_name: ["Inter"]}
    assert "A" in seen["required_chars"]
    assert (config.work_dir / "design_vision.json").exists()


def test_renderer_is_closed_after_render(config, stages, monkeypatch):
    from tests.pipeline.conftest import FakeRenderer

    closed = []

    class Closing(FakeRenderer):
        def close(self):
            closed.append(True)

    monkeypatch.setattr(layout_pkg, "get_renderer", lambda name: Closing())
    run_pipeline(config, FakeLLMClient())
    assert closed == [True]
