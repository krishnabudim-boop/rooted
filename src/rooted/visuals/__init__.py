"""WP4. Visual planning (LLM) and rendering (matplotlib/SVG)."""

from __future__ import annotations

from pathlib import Path

from rooted.models import RejectedVisual, VisualSet
from rooted.visuals.icons import ICON_NAMES
from rooted.visuals.planner import find_numeric_candidates, plan_visuals
from rooted.visuals.prompts import VISUAL_PLAN_SYSTEM
from rooted.visuals.provenance import validate_visual_plan
from rooted.visuals.render import render_visuals, render_visuals_checked

__all__ = [
    "plan_visuals", "validate_visual_plan", "render_visuals", "build_visuals",
    "find_numeric_candidates", "ICON_NAMES", "VISUAL_PLAN_SYSTEM",
]


def build_visuals(doc, plan, design, brief, llm, work_dir) -> VisualSet:  # type: ignore[no-untyped-def]
    """plan_visuals -> validate_visual_plan -> render_visuals. Returns VisualSet.

    Visuals that fail the never-invent-data check land in ``rejected``; visuals
    that fail to render are dropped from the plan and also reported there."""
    work_dir = Path(work_dir)
    planned = plan_visuals(doc, plan, design, brief, llm)
    valid, rejected = validate_visual_plan(planned, doc)
    rendered, errors = render_visuals_checked(valid, design, doc, work_dir / "visuals")
    if errors:
        valid = valid.model_copy(update={"visuals": [v for v in valid.visuals if v.id not in errors]})
        rejected = rejected + [RejectedVisual(visual_id=i, reason=r) for i, r in errors.items()]
    return VisualSet(plan=valid, rendered=rendered, rejected=rejected)
