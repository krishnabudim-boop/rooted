"""Stage runner (WP6): ordering, caching, --stage/--from/--dry-run.

Other packages are called ONLY through their public functions, looked up on
the package module at call time (``rooted.ingest.read_docx`` ...), so tests can
monkeypatch them.
"""

from __future__ import annotations

import logging
import math
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import rooted.content as content_pkg
import rooted.design as design_pkg
import rooted.fonts as fonts_pkg
import rooted.ingest as ingest_pkg
import rooted.layout as layout_pkg
import rooted.visuals as visuals_pkg
from rooted.cache import STAGE_VERSIONS, WorkDir, hash_inputs
from rooted.errors import InputError, LLMRefusalError, StageError
from rooted.layout import trace as layout_trace
from rooted.llm import (
    TASK_BRIEF,
    TASK_CONTENT_PLAN,
    TASK_DESIGN_VISION,
    TASK_VISUAL_PLAN,
    LLMClient,
    build_client,
)
from rooted.logging_setup import get_console
from rooted.models import (
    STAGE_ORDER,
    Brief,
    BuildConfig,
    BuildReport,
    ContentDoc,
    ContentPlan,
    DesignSpec,
    FitParams,
    LayoutPlan,
    RawDesignStats,
    RenderResult,
    Stage,
    VisionDesignNotes,
    VisualPlan,
    VisualSet,
)

log = logging.getLogger(__name__)

_IMAGE_TOKENS = 1500  # rough cost of one 100 dpi page thumbnail


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------


@dataclass
class _Ctx:
    config: BuildConfig
    wd: WorkDir
    llm: LLMClient | None
    warnings: list[str] = field(default_factory=list)
    report: BuildReport | None = None

    def need_llm(self) -> LLMClient:
        if self.llm is None:
            self.llm = build_client(self.config.llm, self.wd.root / "llm_cache")
        return self.llm

    def load(self, name: str, model_type: type) -> Any:
        obj = self.wd.load(name, model_type)
        if obj is None:
            raise StageError(name, f"missing {name}.json in {self.wd.root}; run the earlier stage first")
        return obj


def _workdir(config: BuildConfig) -> WorkDir:
    return WorkDir(config.work_dir).ensure()


def _doc_text(doc: ContentDoc) -> str:
    return "".join(b.text for b in doc.blocks)


def _config_subset(stage: Stage, c: BuildConfig) -> list[Any]:
    """Config values that change this stage's output."""
    if stage == Stage.BRIEF:
        return [c.brief_text, c.llm.model, c.llm.effort]
    if stage == Stage.DESIGN:
        return [c.vision_max_pages, c.vision_dpi, c.reuse_embedded_fonts, c.allow_font_download, c.llm.model,
                c.llm.effort]
    if stage in (Stage.CONTENT, Stage.VISUALS):
        return [c.llm.model, c.llm.effort, c.seed]
    if stage == Stage.LAYOUT:
        return [c.seed]
    if stage == Stage.RENDER:
        return [c.renderer, c.fit_bounds, c.max_fit_iterations, str(c.output_path)]
    return []


_UPSTREAM: dict[Stage, list[Stage]] = {
    Stage.INGEST: [],
    Stage.BRIEF: [],
    Stage.DESIGN: [Stage.INGEST, Stage.BRIEF],
    Stage.CONTENT: [Stage.INGEST, Stage.BRIEF, Stage.DESIGN],
    Stage.VISUALS: [Stage.INGEST, Stage.BRIEF, Stage.DESIGN, Stage.CONTENT],
    Stage.LAYOUT: [Stage.INGEST, Stage.BRIEF, Stage.DESIGN, Stage.CONTENT, Stage.VISUALS],
    Stage.RENDER: [Stage.INGEST, Stage.BRIEF, Stage.DESIGN, Stage.CONTENT, Stage.VISUALS, Stage.LAYOUT],
}


def stage_input_hash(stage: Stage, ctx: _Ctx) -> str:
    """sha256 of input files, the *current* upstream artifact bytes, the config
    subset and the stage version. Hand-editing an upstream artifact therefore
    invalidates everything after it."""
    c = ctx.config
    parts: list[Any] = [stage.value, STAGE_VERSIONS[stage]]
    if stage == Stage.INGEST:
        parts.append(c.content_path)
    if stage == Stage.DESIGN:
        parts.append(c.reference_pdf)
    parts += [f"{u.value}={ctx.wd.artifact_sha256(u.value)}" for u in _UPSTREAM[stage]]
    parts.append(_config_subset(stage, c))
    return hash_inputs(*parts)


# --------------------------------------------------------------------------
# Stage implementations
# --------------------------------------------------------------------------


def _do_ingest(ctx: _Ctx) -> str:
    c = ctx.config
    if not Path(c.content_path).is_file():
        raise InputError(f"content file not found: {c.content_path}")
    doc = ingest_pkg.read_docx(Path(c.content_path), ctx.wd.root / "assets")
    ctx.wd.save("ingest", doc)
    return "ingest.json"


def _do_brief(ctx: _Ctx) -> str:
    text = ctx.config.brief_text
    llm: LLMClient | None = ctx.need_llm() if text.strip() else None
    try:
        brief = content_pkg.parse_brief(text, llm)
    except LLMRefusalError as e:
        msg = f"brief: model refused ({e}); using regex-only brief"
        log.warning(msg)
        ctx.warnings.append(msg)
        brief = content_pkg.parse_brief(text, None)
    ctx.wd.save("brief", brief)
    return "brief.json"


def _font_hints(raw: RawDesignStats, notes: VisionDesignNotes | None) -> dict[str, list[str]]:
    if notes is None:
        return {}
    names = {fc.id: fc.font_name for fc in raw.font_clusters}
    hints: dict[str, list[str]] = {}
    for fch in notes.font_characters:
        name = names.get(fch.cluster_id)
        if name and fch.similar_open_fonts:
            hints.setdefault(name, [])
            hints[name] += [f for f in fch.similar_open_fonts if f not in hints[name]]
    return hints


def _do_design(ctx: _Ctx) -> str:
    c = ctx.config
    if not Path(c.reference_pdf).is_file():
        raise InputError(f"reference PDF not found: {c.reference_pdf}")
    doc: ContentDoc = ctx.load("ingest", ContentDoc)
    brief: Brief = ctx.load("brief", Brief)
    raw, notes, spec = design_pkg.extract_design(Path(c.reference_pdf), ctx.wd.root, brief, ctx.need_llm(), c)
    ctx.wd.save("design_raw", raw)
    if notes is not None:
        ctx.wd.save("design_vision", notes)
    spec = fonts_pkg.resolve_design_fonts(
        spec,
        required_chars="".join(sorted(set(_doc_text(doc)))),
        config=c,
        hints=_font_hints(raw, notes),
    )
    ctx.wd.save("design", spec)
    ctx.warnings.extend(spec.warnings)
    return "design.json"


def _do_content(ctx: _Ctx) -> str:
    doc = ctx.load("ingest", ContentDoc)
    brief = ctx.load("brief", Brief)
    design = ctx.load("design", DesignSpec)
    plan = content_pkg.plan_content(doc, brief, design, ctx.need_llm())
    ctx.wd.save("content", plan)
    return "content.json"


def _do_visuals(ctx: _Ctx) -> str:
    doc = ctx.load("ingest", ContentDoc)
    brief = ctx.load("brief", Brief)
    design = ctx.load("design", DesignSpec)
    plan = ctx.load("content", ContentPlan)
    try:
        vs = visuals_pkg.build_visuals(doc, plan, design, brief, ctx.need_llm(), ctx.wd.root)
    except LLMRefusalError as e:
        msg = f"visuals: model refused ({e}); building without generated visuals"
        log.warning(msg)
        ctx.warnings.append(msg)
        vs = VisualSet(plan=VisualPlan(visuals=[], rationale="model refused"))
    ctx.wd.save("visuals", vs)
    return "visuals.json"


def _load_all(ctx: _Ctx) -> tuple[ContentDoc, ContentPlan, DesignSpec, VisualSet, Brief]:
    return (
        ctx.load("ingest", ContentDoc),
        ctx.load("content", ContentPlan),
        ctx.load("design", DesignSpec),
        ctx.load("visuals", VisualSet),
        ctx.load("brief", Brief),
    )


def _do_layout(ctx: _Ctx) -> str:
    doc, plan, design, vs, brief = _load_all(ctx)
    layout = layout_pkg.compose(doc, plan, design, vs, brief)
    if brief.target_pages is not None and layout.target_pages is None:
        layout = layout.model_copy(update={"target_pages": brief.target_pages})
    ctx.wd.save("layout", layout)
    return "layout.json"


def _do_render(ctx: _Ctx) -> str:
    c = ctx.config
    doc, plan, design, vs, brief = _load_all(ctx)
    initial: LayoutPlan = ctx.load("layout", LayoutPlan)
    renderer = layout_pkg.get_renderer(c.renderer)
    out_dir = ctx.wd.root / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    def render_fn(params: FitParams) -> tuple[LayoutPlan, RenderResult]:
        layout = layout_pkg.compose(doc, plan, design, vs, brief, params)
        html = layout_pkg.build_html(doc, plan, design, vs, layout, ctx.wd.root)
        result = renderer.render(
            html, base_url=ctx.wd.root, pdf_path=out_dir / "document.pdf", html_path=out_dir / "document.html"
        )
        return layout, result

    optional_ids = [e.id for u in initial.units for e in u.elements if e.optional]
    try:
        layout, result, fit = layout_pkg.fit_to_pages(
            render_fn,
            target_pages=brief.target_pages,
            tolerance=brief.page_tolerance,
            bounds=c.fit_bounds,
            max_iterations=c.max_fit_iterations,
            optional_ids=optional_ids,
        )
    finally:
        # Playwright's sync API leaves a running loop in this thread until the browser is closed; without this
        # a second build in the same process fails to launch Chromium.
        close = getattr(renderer, "close", None)
        if close is not None:
            close()
    ctx.wd.save("out/layout_final", layout)

    out = Path(c.output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(result.pdf_path, out)

    warnings = list(ctx.warnings) + list(result.warnings) + list(design.warnings)
    if fit.target_pages is not None and fit.status in ("over", "under"):
        warnings.append(
            f"page target not reached: wanted {fit.target_pages}, closest achievable is {fit.achieved_pages} "
            "(layout knobs only; your text was not edited)"
        )
    if result.overflow_element_ids:
        n = len(result.overflow_element_ids)
        warnings.append(f"{n} fixed region(s) overflow: {result.overflow_element_ids[:5]}")
    report = BuildReport(
        output_pdf=str(out),
        render=result,
        fit=fit,
        trace=layout_trace.build_trace(doc, plan, layout, result),
        generated_text=layout_trace.collect_generated_text(plan, vs),
        warnings=list(dict.fromkeys(warnings)),
    )
    ctx.wd.save("render", report)
    ctx.report = report
    return "render.json"


_IMPL: dict[Stage, Callable[[_Ctx], str]] = {
    Stage.INGEST: _do_ingest,
    Stage.BRIEF: _do_brief,
    Stage.DESIGN: _do_design,
    Stage.CONTENT: _do_content,
    Stage.VISUALS: _do_visuals,
    Stage.LAYOUT: _do_layout,
    Stage.RENDER: _do_render,
}


def _execute(stage: Stage, ctx: _Ctx) -> None:
    console = get_console()
    input_hash = stage_input_hash(stage, ctx)
    t0 = time.monotonic()
    with console.status(f"[bold]{stage.value}[/bold] ...", spinner="dots"):
        try:
            artifact = _IMPL[stage](ctx)
        except NotImplementedError as e:
            raise StageError(stage.value, "stage is not implemented yet") from e
    dt = time.monotonic() - t0
    ctx.wd.record(stage, input_hash, artifact, dt)
    console.print(f"[green]done[/green]   {stage.value:<8} {dt:6.1f}s  -> {artifact}")


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def run_stage(stage: Stage, config: BuildConfig, llm: LLMClient | None) -> None:
    """Run exactly one stage, loading its inputs from the work dir."""
    ctx = _Ctx(config=config, wd=_workdir(config), llm=llm)
    _execute(stage, ctx)


def run_pipeline(config: BuildConfig, llm: LLMClient | None = None) -> BuildReport | None:
    """Run stages in STAGE_ORDER, reusing cached artifacts whose input hash
    matches the manifest. Returns None when stopped early (--stage) or in
    dry-run mode. ``llm=None`` -> build from config (real client)."""
    if config.dry_run:
        plan = plan_dry_run(config)
        print_dry_run(plan)
        return None

    ctx = _Ctx(config=config, wd=_workdir(config), llm=llm)
    console = get_console()
    forced_from = STAGE_ORDER.index(config.from_stage) if config.from_stage else None
    if forced_from is not None:
        ctx.wd.invalidate(STAGE_ORDER[forced_from:])

    last = STAGE_ORDER.index(config.stop_after) if config.stop_after else len(STAGE_ORDER) - 1
    for stage in STAGE_ORDER[: last + 1]:
        if not config.force and ctx.wd.is_fresh(stage, stage_input_hash(stage, ctx)):
            console.print(f"[dim]cached[/dim] {stage.value}")
            continue
        _execute(stage, ctx)

    if config.stop_after is not None and config.stop_after != Stage.RENDER:
        return None
    if ctx.report is None:  # render was cached: load the stored report
        ctx.report = ctx.wd.load("render", BuildReport)
        if ctx.report is not None and not Path(config.output_path).exists():
            Path(config.output_path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ctx.report.render.pdf_path, config.output_path)
    return ctx.report


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------


@dataclass
class PlannedCall:
    task: str
    images: int
    approx_input_tokens: int


@dataclass
class DryRunPlan:
    calls: list[PlannedCall]
    estimated_pages: int | None
    words: int
    words_per_body_page: float | None
    fixed_pages: int | None
    target_pages: int | None
    reference_pages: int | None


def plan_dry_run(config: BuildConfig) -> DryRunPlan:
    """Ingest, regex-only brief, raw design extraction and capacity estimate.
    Makes no LLM calls and writes no PDF."""
    wd = _workdir(config)
    for label, path in (("content file", config.content_path), ("reference PDF", config.reference_pdf)):
        if not Path(path).is_file():
            raise InputError(f"{label} not found: {path}")
    doc = ingest_pkg.read_docx(Path(config.content_path), wd.root / "assets")
    brief = content_pkg.parse_brief(config.brief_text, None)
    raw = design_pkg.extract_raw(Path(config.reference_pdf))
    spec = design_pkg.apply_brief(design_pkg.synthesize_design(raw, None), brief)
    cap = content_pkg.estimate_capacity(spec, brief)

    words = doc.word_count or len(_doc_text(doc).split())
    pages = math.ceil(words / cap.words_per_body_page) + cap.fixed_pages if cap.words_per_body_page > 0 else None
    n_thumbs = min(config.vision_max_pages, raw.page_count)
    block_chars = sum(len(b.text) for b in doc.blocks)
    cluster_chars = sum(len(fc.sample_text) + 80 for fc in raw.font_clusters)
    calls = []
    if config.brief_text.strip():
        calls.append(PlannedCall(TASK_BRIEF, 0, len(config.brief_text) // 4 + 400))
    calls += [
        PlannedCall(TASK_DESIGN_VISION, n_thumbs, cluster_chars // 4 + n_thumbs * _IMAGE_TOKENS + 800),
        PlannedCall(TASK_CONTENT_PLAN, 0, block_chars // 4 + len(doc.blocks) * 12 + 1200),
    ]
    if brief.visuals_policy.value != "none":
        calls.append(PlannedCall(TASK_VISUAL_PLAN, 0, block_chars // 8 + 1000))
    return DryRunPlan(
        calls=calls,
        estimated_pages=pages,
        words=words,
        words_per_body_page=cap.words_per_body_page,
        fixed_pages=cap.fixed_pages,
        target_pages=brief.target_pages,
        reference_pages=raw.page_count,
    )


def print_dry_run(plan: DryRunPlan) -> None:
    from rich.table import Table

    console = get_console()
    table = Table(title="Planned LLM calls (dry run: none are made)")
    table.add_column("task")
    table.add_column("images", justify="right")
    table.add_column("~input tokens", justify="right")
    for c in plan.calls:
        table.add_row(c.task, str(c.images), f"{c.approx_input_tokens:,}")
    console.print(table)
    console.print(f"words: {plan.words:,}   reference pages: {plan.reference_pages}")
    if plan.estimated_pages is not None:
        target = plan.target_pages if plan.target_pages else "natural length"
        console.print(f"estimated pages: ~{plan.estimated_pages} (target: {target})")
