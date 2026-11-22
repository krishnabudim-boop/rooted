"""WP3. Reference PDF -> DesignSpec."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from rooted.design.overrides import apply_brief
from rooted.design.raw_extract import extract_raw, render_thumbnails
from rooted.design.synthesize import synthesize_design
from rooted.design.vision import analyze_pages, select_sample_pages
from rooted.errors import LLMError
from rooted.llm import LLMClient
from rooted.models import Brief, BuildConfig, DesignSpec, RawDesignStats, VisionDesignNotes

__all__ = ["extract_raw", "render_thumbnails", "select_sample_pages", "analyze_pages", "synthesize_design",
           "apply_brief", "extract_design"]

log = logging.getLogger(__name__)


def extract_design(
    pdf_path: Path, work_dir: Path, brief: Brief, llm: LLMClient, config: BuildConfig
) -> tuple[RawDesignStats, Optional[VisionDesignNotes], DesignSpec]:
    """Convenience: raw -> thumbnails -> vision -> synthesize -> apply_brief.
    Returns (RawDesignStats, VisionDesignNotes | None, DesignSpec). Font
    resolution is a separate pipeline step (rooted.fonts).

    The vision pass is skipped on ``config.dry_run`` and degrades to heuristics
    (with a warning on the spec) if the LLM fails."""
    pdf_path, work_dir = Path(pdf_path), Path(work_dir)
    raw = extract_raw(pdf_path)
    notes: Optional[VisionDesignNotes] = None
    warning: Optional[str] = None
    if config.dry_run:
        warning = "Dry run: vision pass skipped."
    else:
        indices = select_sample_pages(raw, config.vision_max_pages)
        paths = render_thumbnails(pdf_path, work_dir / "thumbs", indices, dpi=config.vision_dpi)
        pngs = {i: p for i, p in zip(indices, paths, strict=True)}
        raw = raw.model_copy(update={"pages": [
            pg.model_copy(update={"png_path": _rel(pngs[pg.index], work_dir)}) if pg.index in pngs else pg
            for pg in raw.pages]})
        try:
            notes = analyze_pages(raw, list(zip(indices, paths, strict=True)), llm)
        except LLMError as e:
            log.warning("design vision pass failed, using heuristics: %s", e)
            warning = f"Vision pass failed ({type(e).__name__}: {e}); design derived from heuristics."
    spec = synthesize_design(raw, notes)
    if warning:
        spec.warnings.append(warning)
    spec = apply_brief(spec, brief)
    return raw, notes, spec


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)
