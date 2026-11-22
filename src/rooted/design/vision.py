"""Claude vision pass over reference thumbnails (WP3)."""

from __future__ import annotations

import logging
from pathlib import Path

from rooted.design import color as C
from rooted.design.prompts import DESIGN_VISION_SYSTEM, design_vision_prompt
from rooted.errors import LLMTruncatedError, LLMValidationError
from rooted.llm import TASK_DESIGN_VISION, ImageInput, LLMClient
from rooted.models import RawDesignStats, VisionDesignNotes

log = logging.getLogger(__name__)

SNAP_DE = 5.0  # a labeled hex this close to a measured color snaps to it


def select_sample_pages(raw: RawDesignStats, max_pages: int) -> list[int]:
    """Always first and last page; then pages that differ most in
    (char_count, image_area_ratio, fill_area_ratio, column_estimate,
    dominant_fill) via greedy farthest-point selection."""
    n = len(raw.pages)
    if n == 0 or max_pages <= 0:
        return []
    if n <= max_pages:
        return [p.index for p in raw.pages]
    max_chars = max((p.char_count for p in raw.pages), default=0) or 1
    max_cols = max((p.column_estimate for p in raw.pages), default=1) or 1

    def vec(p) -> list[float]:  # type: ignore[no-untyped-def]
        rgb = C.hex_to_rgb(p.dominant_fill) if p.dominant_fill else (255, 255, 255)
        return [p.char_count / max_chars, p.image_area_ratio, p.fill_area_ratio, p.column_estimate / max_cols,
                *(v / 255 for v in rgb)]

    vecs = {p.index: vec(p) for p in raw.pages}
    order = [p.index for p in raw.pages]
    chosen = [order[0]] if max_pages == 1 else [order[0], order[-1]]
    while len(chosen) < max_pages:
        best, best_d = None, -1.0
        for i in order:
            if i in chosen:
                continue
            d = min(sum((a - b) ** 2 for a, b in zip(vecs[i], vecs[j], strict=True)) for j in chosen)
            if d > best_d:
                best, best_d = i, d
        if best is None:
            break
        chosen.append(best)
    return sorted(chosen)


def _summary(raw: RawDesignStats) -> dict:
    return {
        "page_count": raw.page_count,
        "page_size_pt": [raw.pages[0].width_pt, raw.pages[0].height_pt] if raw.pages else None,
        "body_cluster_id": raw.body_cluster_id,
        "font_clusters": [
            {"id": c.id, "font": c.font_name, "size_pt": c.size_pt, "bold": c.bold, "italic": c.italic,
             "color": c.color, "chars": c.char_count, "pages": c.page_indices, "sample": c.sample_text[:60]}
            for c in raw.font_clusters
        ],
        "colors": [{"hex": c.hex, "usage": c.usage, "weight": c.weight} for c in raw.colors],
        "repeated_texts": [{"pattern": r.pattern, "y_pt": r.y_center_pt, "align": r.x_align, "pages": r.page_indices,
                            "cluster_id": r.cluster_id} for r in raw.repeated_texts],
        "filled_boxes": [{"page": b.page_index, "bbox_pt": [round(v, 1) for v in (b.bbox.x0, b.bbox.y0, b.bbox.x1, b.bbox.y1)],
                          "fill": b.fill} for b in raw.filled_boxes[:40]],
        "pages": [{"page_index": p.index, "chars": p.char_count, "image_ratio": p.image_area_ratio,
                   "fill_ratio": p.fill_area_ratio, "columns": p.column_estimate, "dominant_fill": p.dominant_fill}
                  for p in raw.pages],
    }


def _snap(hexval: str | None, known: list[str]) -> str | None:
    """Normalize ``hexval`` and snap it to a measured color; None if not measured."""
    if not hexval:
        return None
    h = C.normalize_hex(hexval)
    if h is None:
        return None
    if h in known:
        return h
    best = min(known, key=lambda k: C.delta_e(h, k), default=None)
    if best is not None and C.delta_e(h, best) <= SNAP_DE:
        return best
    return None


def _validate(notes: VisionDesignNotes, raw: RawDesignStats) -> VisionDesignNotes:
    """Drop labels that reference unknown clusters, pages or colors."""
    ids = {c.id for c in raw.font_clusters}
    known = sorted({c.hex for c in raw.colors} | {c.color for c in raw.font_clusters}
                   | {p.dominant_fill for p in raw.pages if p.dominant_fill})
    pages = {p.index for p in raw.pages}
    out = notes.model_copy(deep=True)

    def keep_cluster(item) -> bool:  # type: ignore[no-untyped-def]
        if item.cluster_id in ids:
            return True
        log.warning("vision notes: dropping unknown cluster id %r", item.cluster_id)
        return False

    out.role_assignments = [r for r in out.role_assignments if keep_cluster(r)]
    out.font_characters = [f for f in out.font_characters if keep_cluster(f)]
    kept = []
    for cr in out.color_roles:
        h = _snap(cr.hex, known)
        if h is None:
            log.warning("vision notes: dropping unmeasured color %r for role %s", cr.hex, cr.role.value)
            continue
        kept.append(cr.model_copy(update={"hex": h}))
    out.color_roles = kept
    classes = []
    for pc in out.page_classifications:
        if pc.page_index in pages:
            classes.append(pc)
        else:
            log.warning("vision notes: dropping classification of unknown page %r", pc.page_index)
    out.page_classifications = classes
    templates = []
    for t in out.templates:
        bg = _snap(t.background_hex, known)
        if t.background_hex and bg is None:
            log.warning("vision notes: dropping unmeasured background %r", t.background_hex)
        regions = []
        for r in t.regions:
            fh = _snap(r.fill_hex, known)
            if r.fill_hex and fh is None:
                log.warning("vision notes: dropping unmeasured region fill %r", r.fill_hex)
            regions.append(r.model_copy(update={"fill_hex": fh}))
        idx = t.example_page_index if t.example_page_index in pages else min(pages, default=0)
        templates.append(t.model_copy(update={"background_hex": bg, "regions": regions, "example_page_index": idx}))
    out.templates = templates
    return out


def analyze_pages(raw: RawDesignStats, thumbnails: list[tuple[int, Path]], llm: LLMClient) -> VisionDesignNotes:
    """One ``llm.structured(task=TASK_DESIGN_VISION, output_model=VisionDesignNotes)``
    call with all thumbnails (labeled "Page N") plus a compact JSON summary of
    font clusters / colors / repeated texts. Validate cluster_ids and hexes
    against ``raw``; drop unknown ones with a log warning."""
    images = [ImageInput.from_path(path, label=f"Page {idx + 1} (page_index {idx})") for idx, path in thumbnails]
    prompt = design_vision_prompt(_summary(raw))
    kwargs = dict(task=TASK_DESIGN_VISION, system=DESIGN_VISION_SYSTEM, output_model=VisionDesignNotes,
                  images=images, effort="high")
    try:
        notes = llm.structured(prompt=prompt, **kwargs)  # type: ignore[arg-type]
    except LLMTruncatedError:
        notes = llm.structured(prompt=prompt, max_tokens=64000, **kwargs)  # type: ignore[arg-type]
    except LLMValidationError as e:
        notes = llm.structured(prompt=f"{prompt}\n\nYour previous output failed validation: {e}", **kwargs)  # type: ignore[arg-type]
    return _validate(notes, raw)
