"""Browser lifecycle (needs the only live Playwright instance in the thread, hence its own module)."""

from __future__ import annotations

import pytest

from rooted.layout.render import get_renderer

pytestmark = pytest.mark.render


def test_renderer_closes_cleanly():
    with get_renderer("chromium") as r:
        assert r._browser is not None
    assert r._browser is None and r._pw is None
    r.close()  # idempotent


def test_lazy_start_and_manual_close(inputs):
    from rooted.layout.compose import compose
    from rooted.layout.html import build_html

    doc, plan, design, visuals, brief, work = inputs
    r = get_renderer("chromium")
    assert r._browser is None  # nothing launched until the first render
    html = build_html(doc, plan, design, visuals, compose(doc, plan, design, visuals, brief), work)
    res = r.render(html, base_url=work, pdf_path=work / "o.pdf", html_path=work / "o.html")
    assert res.page_count == 6 and r._browser is not None
    r.close()
    assert r._browser is None
