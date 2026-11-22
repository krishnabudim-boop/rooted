"""Layout test fixtures (the root conftest skips ``render`` tests when Chromium is unavailable)."""

from __future__ import annotations

import pytest

from rooted.layout.render import get_renderer
from tests import factories


@pytest.fixture(scope="module")
def chromium():
    """One browser per test module (mirrors reuse across fit iterations). Playwright's sync API
    allows only one live instance per thread, so lifecycle tests live in their own module."""
    with get_renderer("chromium") as r:
        yield r


@pytest.fixture
def inputs(tmp_path):
    """(doc, plan, design, visuals, brief, work_dir) with a real placeholder chart SVG on disk."""
    doc = factories.sample_content_doc()
    work = tmp_path / "work"
    (work / "visuals").mkdir(parents=True)
    (work / "visuals" / "v01.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="483" height="260" viewBox="0 0 483 260">'
        '<rect width="483" height="260" fill="#F3F1EA"/>'
        '<rect x="40" y="60" width="90" height="180" fill="#1F2A44"/>'
        '<rect x="160" y="100" width="90" height="140" fill="#C9A227"/>'
        '<rect x="280" y="130" width="90" height="110" fill="#1F2A44"/></svg>'
    )
    return (
        doc,
        factories.sample_content_plan(doc),
        factories.sample_design_spec(),
        factories.sample_visual_set(doc),
        factories.sample_brief(),
        work,
    )
