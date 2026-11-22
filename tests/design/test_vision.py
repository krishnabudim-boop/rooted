from __future__ import annotations

import pytest

from rooted.design import analyze_pages, render_thumbnails, select_sample_pages
from rooted.errors import LLMTruncatedError, LLMValidationError
from rooted.llm import TASK_DESIGN_VISION
from rooted.models import ColorRole, PageType, RawPageStats, TypeRole, VisionDesignNotes
from tests import factories
from tests.conftest import FIXTURES, FakeLLMClient


def _pages(n, **over):
    return [RawPageStats(index=i, width_pt=600, height_pt=800, char_count=over.get("chars", lambda i: 100)(i),
                         image_area_ratio=0.0, fill_area_ratio=0.0, column_estimate=1) for i in range(n)]


def test_select_sample_pages_first_last_and_diverse():
    raw = factories.sample_raw_stats()
    pages = _pages(12, chars=lambda i: 3000 if i != 5 else 10)
    raw = raw.model_copy(update={"pages": pages, "page_count": 12})
    sel = select_sample_pages(raw, 3)
    assert sel[0] == 0 and sel[-1] == 11 and 5 in sel and len(sel) == 3
    assert select_sample_pages(raw, 20) == list(range(12))
    assert select_sample_pages(raw, 1) == [0]


def test_select_sample_pages_on_reference(raw):
    assert select_sample_pages(raw, 8) == [0, 1, 2, 3]
    sel = select_sample_pages(raw, 3)
    assert sel[0] == 0 and sel[-1] == 3 and len(sel) == 3


def test_analyze_uses_shipped_fixture_and_sends_images(tmp_path, reference_pdf):
    raw = factories.sample_raw_stats()
    paths = render_thumbnails(reference_pdf, tmp_path, [0, 1], dpi=40)
    llm = FakeLLMClient()  # falls back to tests/fixtures/llm/design_vision.json
    notes = analyze_pages(raw, [(0, paths[0]), (1, paths[1])], llm)
    assert isinstance(notes, VisionDesignNotes)
    call = llm.calls_for(TASK_DESIGN_VISION)[0]
    assert call.n_images == 2 and call.effort == "high" and call.output_model == "VisionDesignNotes"
    assert [i.label for i in llm.images[0]] == ["Page 1 (page_index 0)", "Page 2 (page_index 1)"]
    assert "f01" in call.prompt and "#1F2A44" in call.prompt
    assert {r.cluster_id for r in notes.role_assignments} == {"f01", "f02", "f03"}
    assert (FIXTURES / "llm" / "design_vision.json").exists()


def test_unknown_ids_hexes_and_pages_are_dropped(tmp_path, reference_pdf, caplog):
    raw = factories.sample_raw_stats()
    path = render_thumbnails(reference_pdf, tmp_path, [0], dpi=40)[0]
    bad = {
        "overall_style": "x",
        "page_classifications": [{"page_index": 0, "page_type": "cover", "description": "c"},
                                 {"page_index": 77, "page_type": "body", "description": "ghost"}],
        "role_assignments": [{"cluster_id": "f01", "role": "body"}, {"cluster_id": "f99", "role": "h1"}],
        "color_roles": [{"hex": "#1f2a44", "role": "primary"},      # case-normalized
                        {"hex": "#1F2A45", "role": "secondary"},    # within dE: snaps to measured
                        {"hex": "#FF00FF", "role": "accent"},       # never measured: dropped
                        {"hex": "nonsense", "role": "rule"}],
        "font_characters": [{"cluster_id": "zzz", "classification": "sans"}],
        "templates": [{"page_type": "cover", "example_page_index": 0, "columns": 1, "background_hex": "#123456",
                       "regions": [{"name": "shape", "kind": "shape", "fill_hex": "#654321",
                                    "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 0.1}}]}],
    }
    with caplog.at_level("WARNING"):
        notes = analyze_pages(raw, [(0, path)], FakeLLMClient({TASK_DESIGN_VISION: bad}))
    assert [r.cluster_id for r in notes.role_assignments] == ["f01"]
    assert [(c.hex, c.role) for c in notes.color_roles] == [("#1F2A44", ColorRole.PRIMARY),
                                                            ("#1F2A44", ColorRole.SECONDARY)]
    assert [p.page_index for p in notes.page_classifications] == [0]
    assert notes.font_characters == []
    assert notes.templates[0].background_hex is None and notes.templates[0].regions[0].fill_hex is None
    assert notes.templates[0].page_type == PageType.COVER
    assert "f99" in caplog.text and "#FF00FF" in caplog.text


def test_retries_once_on_validation_error_and_truncation(tmp_path, reference_pdf):
    raw = factories.sample_raw_stats()
    path = render_thumbnails(reference_pdf, tmp_path, [0], dpi=40)[0]
    good = FIXTURES / "llm" / "design_vision.json"
    llm = FakeLLMClient({TASK_DESIGN_VISION: [LLMValidationError("bad json"), good]})
    analyze_pages(raw, [(0, path)], llm)
    assert len(llm.calls) == 2 and "failed validation: bad json" in llm.calls[1].prompt
    llm = FakeLLMClient({TASK_DESIGN_VISION: [LLMTruncatedError("cut"), good]})
    analyze_pages(raw, [(0, path)], llm)
    assert len(llm.calls) == 2
    llm = FakeLLMClient({TASK_DESIGN_VISION: [LLMValidationError("a"), LLMValidationError("b")]})
    with pytest.raises(LLMValidationError):
        analyze_pages(raw, [(0, path)], llm)


def test_system_prompt_is_stable_and_nonempty():
    from rooted.design.prompts import DESIGN_VISION_SYSTEM, design_vision_prompt

    assert len(DESIGN_VISION_SYSTEM) > 500 and "never to measure" in DESIGN_VISION_SYSTEM
    a = design_vision_prompt({"b": 1, "a": [2, 1]})
    assert a == design_vision_prompt({"a": [2, 1], "b": 1})
    assert TypeRole.H1.value in DESIGN_VISION_SYSTEM


def test_summary_includes_filled_boxes(raw):
    from rooted.design.vision import _summary

    boxes = _summary(raw)["filled_boxes"]
    assert any(b["page"] == 0 and b["fill"] == "#C9A227" for b in boxes)
    assert all(len(b["bbox_pt"]) == 4 for b in boxes)


def test_normbbox_clamps_slight_overshoot_and_regionnote_literals():
    from pydantic import ValidationError

    from rooted.models import NormBBox, RegionNote

    bb = NormBBox(x0=-0.01, y0=0, x1=1.02, y1=0.5)
    assert (bb.x0, bb.x1) == (0.0, 1.0)
    n = RegionNote.model_validate({"name": "title", "kind": "text", "bbox": {"x0": 0, "y0": 0, "x1": 1.02, "y1": 1}})
    assert n.bbox.x1 == 1.0
    with pytest.raises(ValidationError):
        RegionNote.model_validate({"name": "nope", "kind": "text", "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}})
