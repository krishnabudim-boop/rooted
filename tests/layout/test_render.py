"""End-to-end layout tests: compose -> HTML -> Chromium PDF -> PyMuPDF measurement."""

from __future__ import annotations

import pymupdf
import pytest

from rooted.errors import RenderError
from rooted.layout.compose import compose
from rooted.layout.fit import fit_to_pages, make_render_fn, optional_ids_by_priority
from rooted.layout.html import build_html
from rooted.layout.render import get_renderer
from rooted.layout.trace import build_trace
from rooted.models import FitBounds, InlineRun
from tests import factories
from tests.layout.helpers import long_inputs, norm, pdf_text

pytestmark = pytest.mark.render


def _fit(chromium, doc, plan, design, visuals, brief, work, target, tol=0):
    pdf, html = work / "out" / "out.pdf", work / "out" / "out.html"
    fn = make_render_fn(doc, plan, design, visuals, brief, work, chromium, pdf_path=pdf, html_path=html)
    base = compose(doc, plan, design, visuals, brief)
    return fit_to_pages(
        fn,
        target_pages=target,
        tolerance=tol,
        bounds=FitBounds(),
        max_iterations=10,
        optional_ids=optional_ids_by_priority(base),
    )


def _near(a, b, tol=2):
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=False))


def _expected_texts(doc):
    for b in doc.blocks:
        if b.table is not None:
            for row in b.table.rows:
                for c in row:
                    yield b.id, c.text
        else:
            yield b.id, b.text


def _assert_verbatim(doc, path):
    text = norm(pdf_text(path))
    missing = [(bid, t) for bid, t in _expected_texts(doc) if norm(t) not in text]
    assert not missing, f"user text missing from PDF: {missing}"


def test_sample_single_render_basics(inputs, chromium):
    doc, plan, design, visuals, brief, work = inputs
    layout = compose(doc, plan, design, visuals, brief)
    html = build_html(doc, plan, design, visuals, layout, work)
    res = chromium.render(html, base_url=work, pdf_path=work / "o.pdf", html_path=work / "o.html")
    assert (work / "o.pdf").exists() and (work / "o.html").exists()
    assert res.page_count == 6 and res.warnings == [] and res.overflow_element_ids == []
    with pymupdf.open(str(work / "o.pdf")) as d:
        # A4
        assert d[0].rect.width == pytest.approx(595.28, abs=0.5) and d[0].rect.height == pytest.approx(841.89, abs=0.5)
        # navy cover, exactly one page, title present
        assert _near(d[0].get_pixmap(dpi=20).pixel(5, 5), (0x1F, 0x2A, 0x44))
        assert "Annual Impact Report 2025" in norm(d[0].get_text())
        assert "Rooted Foundation" in d[0].get_text()
        # back cover is navy too
        assert _near(d[5].get_pixmap(dpi=20).pixel(5, 5), (0x1F, 0x2A, 0x44))
        # fonts embedded on every text page
        for i in range(5):
            assert d[i].get_fonts(), f"no fonts on page {i}"
        # no leftover navigation links from the page-map anchors
        assert all(not d[i].get_links() for i in range(d.page_count))
    _assert_verbatim(doc, work / "o.pdf")


def test_two_column_body(inputs, chromium):
    doc, plan, design, visuals, brief, work = inputs
    layout = compose(doc, plan, design, visuals, brief)
    html = build_html(doc, plan, design, visuals, layout, work)
    res = chromium.render(html, base_url=work, pdf_path=work / "o.pdf", html_path=work / "o.html")
    body_page = next(e.page_index for e in res.page_map if e.unit_id == "u03")
    with pymupdf.open(str(work / "o.pdf")) as d:
        xs = sorted({round(b[0]) for b in d[body_page].get_text("blocks") if b[1] > 80 and b[1] < 760})
    # column 1 starts at the left margin (boxes add <= 12pt padding); column 2 at margin + column + gutter
    right = design.margins.left + (595.28 - 112 - 18) / 2 + 18
    assert any(56 <= x <= 70 for x in xs) and any(right - 2 <= x <= right + 16 for x in xs), xs


def test_page_map_complete(inputs, chromium):
    doc, plan, design, visuals, brief, work = inputs
    layout = compose(doc, plan, design, visuals, brief)
    html = build_html(doc, plan, design, visuals, layout, work)
    res = chromium.render(html, base_url=work, pdf_path=work / "o.pdf", html_path=work / "o.html")
    assert [e.page_index for e in res.page_map] == list(range(res.page_count))
    assert [e.unit_id for e in res.page_map] == ["u01", "u02", "u03", "u04", "u05", "u06"]
    mapped = {i for e in res.page_map for i in e.element_ids}
    assert mapped == {e.id for u in layout.units for e in u.elements}
    # trace: every block has a page
    trace = build_trace(doc, plan, layout, res)
    assert all(t.pages for t in trace), [t for t in trace if not t.pages]
    by = {t.block_id: t for t in trace}
    assert by["b0001"].pages == [1] and by["b0003"].pages == [2] and by["b0013"].pages == [5]


@pytest.mark.parametrize("target", [4, 6, 8])
def test_sample_fits_exact_page_count(inputs, chromium, target):
    doc, plan, design, visuals, brief, work = inputs
    layout, result, report = _fit(chromium, doc, plan, design, visuals, brief, work, target)
    assert report.status == "exact" and report.achieved_pages == target
    assert len(report.iterations) <= 10 and not result.warnings
    assert result.page_count == target
    with pymupdf.open(result.pdf_path) as d:
        assert d.page_count == target  # the PDF on disk is the returned result
        assert "Annual Impact Report 2025" in norm(d[0].get_text())
    assert result.overflow_element_ids == []
    assert len(result.page_map) == target
    _assert_verbatim(doc, result.pdf_path)


@pytest.mark.parametrize("target", [5, 9])
def test_all_text_present_at_other_targets(inputs, chromium, target):
    doc, plan, design, visuals, brief, work = inputs
    _, result, report = _fit(chromium, doc, plan, design, visuals, brief, work, target)
    assert report.achieved_pages == target
    _assert_verbatim(doc, result.pdf_path)


def test_long_document_fits_with_knobs_and_no_text_edit(chromium, tmp_path):
    doc, plan, visuals = long_inputs()
    design, brief = factories.sample_design_spec(), factories.sample_brief()
    work = tmp_path / "w"
    work.mkdir()
    for target in (8, 9, 12, 14):
        layout, result, report = _fit(chromium, doc, plan, design, visuals, brief, work, target)
        assert report.status == "exact", (target, report.status, [(i.action, i.page_count) for i in report.iterations])
        assert result.page_count == target and not result.overflow_element_ids
        _assert_verbatim(doc, result.pdf_path)


def test_unreachable_target_returns_closest_and_warns(chromium, inputs):
    doc, plan, design, visuals, brief, work = inputs
    _, result, report = _fit(chromium, doc, plan, design, visuals, brief, work, 2)
    assert report.status == "over" and report.achieved_pages == 4
    assert any("Could not reach 2" in w for w in result.warnings)
    with pymupdf.open(result.pdf_path) as d:
        assert d.page_count == 4
    _assert_verbatim(doc, result.pdf_path)


def test_oversized_cover_title_shrinks_instead_of_overflowing(inputs, chromium):
    doc, plan, design, visuals, brief, work = inputs
    long_title = "An Extraordinarily Long Report Title About Community Housing Resilience And Opportunity " * 3
    plan2 = plan.model_copy(
        update={
            "cover": plan.cover.model_copy(
                update={"title": plan.cover.title.model_copy(update={"text": long_title.strip()})}
            )
        }
    )
    layout = compose(doc, plan2, design, visuals, brief)
    html = build_html(doc, plan2, design, visuals, layout, work)
    res = chromium.render(html, base_url=work, pdf_path=work / "o.pdf", html_path=work / "o.html")
    assert res.page_count == 6
    assert any("shrunk" in w for w in res.warnings)
    assert res.overflow_element_ids == []
    assert norm(long_title) in norm(pdf_text(work / "o.pdf") + pymupdf.open(str(work / "o.pdf"))[0].get_text())


def test_hostile_text_renders_as_text(inputs, chromium):
    doc, plan, design, visuals, brief, work = inputs
    evil = "<script>document.title='x'</script> <b>not bold</b> & more"
    blocks = [b.model_copy(update={"runs": [InlineRun(text=evil)]}) if b.id == "b0005" else b for b in doc.blocks]
    doc = doc.model_copy(update={"blocks": blocks})
    layout = compose(doc, plan, design, visuals, brief)
    html = build_html(doc, plan, design, visuals, layout, work)
    chromium.render(html, base_url=work, pdf_path=work / "o.pdf", html_path=work / "o.html")
    assert norm(evil) in norm(pdf_text(work / "o.pdf"))


def test_external_links_survive(inputs, chromium):
    doc, plan, design, visuals, brief, work = inputs
    runs = [InlineRun(text="Visit "), InlineRun(text="our site", link="https://example.com/x")]
    blocks = [b.model_copy(update={"runs": runs}) if b.id == "b0005" else b for b in doc.blocks]
    doc = doc.model_copy(update={"blocks": blocks})
    layout = compose(doc, plan, design, visuals, brief)
    html = build_html(doc, plan, design, visuals, layout, work)
    chromium.render(html, base_url=work, pdf_path=work / "o.pdf", html_path=work / "o.html")
    with pymupdf.open(str(work / "o.pdf")) as d:
        uris = [lk.get("uri") for p in d for lk in p.get_links()]
    assert "https://example.com/x" in uris


def test_browser_is_reused_across_renders(inputs, chromium):
    doc, plan, design, visuals, brief, work = inputs
    layout = compose(doc, plan, design, visuals, brief)
    html = build_html(doc, plan, design, visuals, layout, work)
    browser = chromium._browser
    for _ in range(2):
        chromium.render(html, base_url=work, pdf_path=work / "o.pdf", html_path=work / "o.html")
    assert chromium._browser is browser


def test_get_renderer_errors():
    with pytest.raises(RenderError):
        get_renderer("nope")
    try:
        import weasyprint  # noqa: F401
    except Exception:
        with pytest.raises(RenderError) as exc:
            get_renderer("weasyprint")
        assert "rooted[weasyprint]" in str(exc.value)
