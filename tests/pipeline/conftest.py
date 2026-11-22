"""Fixtures for WP6: fake stage functions returning factory objects."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import rooted.content as content_pkg
import rooted.design as design_pkg
import rooted.fonts as fonts_pkg
import rooted.ingest as ingest_pkg
import rooted.layout as layout_pkg
import rooted.visuals as visuals_pkg
from rooted.layout import trace as layout_trace
from rooted.models import BuildConfig, FitReport, PageCapacity, RenderResult
from tests import factories


class FakeRenderer:
    name = "fake"

    def __init__(self, pages: int = 8):
        self.pages = pages

    def render(self, html, *, base_url, pdf_path, html_path):
        Path(pdf_path).write_bytes(b"%PDF-1.4 fake")
        Path(html_path).write_text(html, "utf-8")
        return RenderResult(pdf_path=str(pdf_path), html_path=str(html_path), page_count=self.pages)


@pytest.fixture
def inputs(tmp_path):
    docx = tmp_path / "content.docx"
    pdf = tmp_path / "ref.pdf"
    docx.write_bytes(b"docx-bytes")
    pdf.write_bytes(b"pdf-bytes")
    return docx, pdf


@pytest.fixture
def config(inputs, tmp_path):
    docx, pdf = inputs
    return BuildConfig(
        content_path=docx,
        reference_pdf=pdf,
        brief_text="8 pages, navy and gold",
        output_path=tmp_path / "out" / "result.pdf",
        work_dir=tmp_path / "work",
    )


@pytest.fixture
def stages(monkeypatch):
    """Patch every cross-package stage function; returns a call counter."""
    calls = SimpleNamespace(log=[])
    doc = factories.sample_content_doc()
    spec = factories.sample_design_spec()
    raw = factories.sample_raw_stats()
    plan = factories.sample_content_plan(doc)
    vset = factories.sample_visual_set(doc)
    layout = factories.sample_layout_plan()
    brief = factories.sample_brief()

    def rec(name, ret):
        def fn(*a, **k):
            calls.log.append(name)
            return ret(*a, **k) if callable(ret) else ret

        return fn

    monkeypatch.setattr(
        ingest_pkg,
        "read_docx",
        rec("ingest", lambda path, assets: doc.model_copy(update={"title": Path(path).read_text("latin-1")})),
    )
    monkeypatch.setattr(
        content_pkg, "parse_brief", rec("brief", lambda text, llm: brief.model_copy(update={"raw_text": text}))
    )
    monkeypatch.setattr(design_pkg, "extract_design", rec("design", (raw, None, spec)))
    monkeypatch.setattr(fonts_pkg, "resolve_design_fonts", rec("fonts", lambda s, **k: s))
    monkeypatch.setattr(content_pkg, "plan_content", rec("content", plan))
    monkeypatch.setattr(visuals_pkg, "build_visuals", rec("visuals", vset))
    monkeypatch.setattr(layout_pkg, "compose", rec("compose", layout))
    monkeypatch.setattr(layout_pkg, "build_html", rec("html", "<html></html>"))
    monkeypatch.setattr(layout_pkg, "get_renderer", rec("get_renderer", lambda name: FakeRenderer()))

    def fake_fit(render_fn, *, target_pages, tolerance, bounds, max_iterations, optional_ids):
        calls.log.append("fit")
        calls.fit_args = dict(target_pages=target_pages, tolerance=tolerance, optional_ids=optional_ids)
        lay, res = render_fn(layout.fit)
        return (
            lay,
            res,
            FitReport(target_pages=target_pages, achieved_pages=res.page_count, status="exact", final=layout.fit),
        )

    monkeypatch.setattr(layout_pkg, "fit_to_pages", fake_fit)
    monkeypatch.setattr(layout_trace, "build_trace", rec("trace", []))
    monkeypatch.setattr(layout_trace, "collect_generated_text", rec("gen", []))
    # dry-run helpers
    monkeypatch.setattr(design_pkg, "extract_raw", rec("raw", raw))
    monkeypatch.setattr(design_pkg, "synthesize_design", rec("synth", spec))
    monkeypatch.setattr(design_pkg, "apply_brief", rec("apply", lambda s, b: s))
    monkeypatch.setattr(
        content_pkg,
        "estimate_capacity",
        rec("capacity", PageCapacity(words_per_body_page=400, words_per_opener_page=150, fixed_pages=2)),
    )
    calls.doc, calls.plan = doc, plan
    return calls
