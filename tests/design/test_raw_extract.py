from __future__ import annotations

import pytest

from rooted.design import extract_raw, render_thumbnails
from rooted.errors import InputError
from rooted.models import RawDesignStats
from tests.design import reference_pdf as ref


def _cluster(raw, size, bold=None, color=None):
    return [c for c in raw.font_clusters if c.size_pt == size and (bold is None or c.bold == bold)
            and (color is None or c.color == color)]


def test_roundtrips_as_model(raw):
    assert RawDesignStats.model_validate_json(raw.model_dump_json()) == raw
    assert raw.page_count == 4
    assert len(raw.source_sha256) == 64


def test_body_and_heading_sizes_exact(raw):
    body = next(c for c in raw.font_clusters if c.id == raw.body_cluster_id)
    assert (body.size_pt, body.font_name, body.bold, body.color) == (10.0, "Helvetica", False, ref.TEXT.upper())
    (h1,) = _cluster(raw, 20.0)
    assert h1.bold and h1.color == ref.NAVY
    assert h1.sample_text in {"Introduction", "Programs", "Results"}
    assert h1.page_indices == [1, 2, 3]
    (h2,) = _cluster(raw, 14.0)
    assert h2.bold and h2.sample_text == "Our mission"
    (title,) = _cluster(raw, 40.0)
    assert title.color == "#FFFFFF" and title.page_indices == [0]
    assert not title.serif and not title.monospace


def test_margins_within_two_points(raw):
    m = raw.margins_estimate
    assert m.left == pytest.approx(ref.LEFT, abs=2)
    assert m.right == pytest.approx(ref.RIGHT, abs=2)
    assert m.top == pytest.approx(ref.TOP, abs=2)
    assert m.bottom == pytest.approx(ref.BOTTOM, abs=2)


def test_columns_and_gutter(raw):
    assert [p.column_estimate for p in raw.pages] == [1, 1, 2, 1]
    assert raw.pages[2].column_gutter_pt == pytest.approx(ref.GUTTER, abs=2)
    assert raw.pages[1].column_gutter_pt is None


def test_repeated_header_and_footer(raw):
    by_pattern = {r.pattern: r for r in raw.repeated_texts}
    assert set(by_pattern) == {"Annual Impact Report", "Page #"}
    footer = by_pattern["Page #"]
    assert footer.page_indices == [1, 2, 3]
    assert footer.y_center_pt > 0.88 * ref.H and footer.x_align == "right"
    header = by_pattern["Annual Impact Report"]
    assert header.y_center_pt < 0.12 * ref.H and header.x_align == "left"
    assert header.cluster_id == footer.cluster_id is not None


def test_palette_and_fill_stats(raw):
    fills = {c.hex: c for c in raw.colors if c.usage == "fill"}
    assert {ref.NAVY, ref.GOLD, ref.SURFACE} <= set(fills)
    assert fills[ref.NAVY].weight > fills[ref.GOLD].weight
    texts = {c.hex for c in raw.colors if c.usage == "text"}
    assert {ref.TEXT.upper(), ref.NAVY, "#FFFFFF", ref.GRAY} <= texts
    assert pytest.approx(sum(c.weight for c in raw.colors), abs=0.02) == 1.0
    assert raw.pages[0].dominant_fill == ref.NAVY and raw.pages[0].fill_area_ratio == pytest.approx(1.0, abs=0.01)
    assert raw.pages[2].dominant_fill == ref.SURFACE


def test_rules_found(raw):
    gray = [r for r in raw.rules if r.color == ref.GRAY]
    assert {r.page_index for r in gray} == {1, 2, 3}
    assert all(r.width_pt == pytest.approx(0.5) for r in gray)


def test_page_geometry_and_chars(raw):
    for p in raw.pages:
        assert (p.width_pt, p.height_pt) == (595.28, 841.89)
    assert raw.pages[0].text_bbox is None and raw.pages[0].char_count > 0
    assert raw.pages[1].text_bbox is not None and raw.pages[1].char_count > 1000


def test_thumbnails(reference_pdf, tmp_path):
    paths = render_thumbnails(reference_pdf, tmp_path / "thumbs", [0, 3], dpi=50)
    assert [p.name for p in paths] == ["page-01.png", "page-04.png"]
    assert all(p.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for p in paths)
    with pytest.raises(InputError):
        render_thumbnails(reference_pdf, tmp_path, [9])


def test_corrupt_pdf_is_input_error(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    with pytest.raises(InputError):
        extract_raw(bad)


def _paragraph_pdf(path, pitch=15.0, para_gap=9.0, lines_per_para=6, paras=4):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=ref.W, height=ref.H)
    y = 100.0
    for _ in range(paras):
        for i in range(lines_per_para):
            page.insert_text((ref.LEFT, y), f"Body line number {i} of the paragraph with enough words", fontname="helv",
                             fontsize=10)
            y += pitch
        y += para_gap
    doc.save(str(path))
    doc.close()
    return path


def test_measures_line_pitch_and_paragraph_spacing(tmp_path):
    raw = extract_raw(_paragraph_pdf(tmp_path / "p.pdf"))
    assert raw.body_line_height_pt == pytest.approx(15.0, abs=0.2)
    assert raw.paragraph_spacing_pt == pytest.approx(9.0, abs=0.3)


def test_line_metrics_none_without_enough_text(tmp_path):
    raw = extract_raw(_paragraph_pdf(tmp_path / "p.pdf", lines_per_para=1, paras=2))
    assert raw.body_line_height_pt is None and raw.paragraph_spacing_pt is None


def test_filled_boxes_measured(raw):
    fills = {(b.page_index, b.fill) for b in raw.filled_boxes}
    assert (2, ref.SURFACE.upper()) in fills  # callout panel
    assert (2, ref.GOLD) in fills  # its accent bar
    panel = next(b for b in raw.filled_boxes if b.fill == ref.SURFACE.upper())
    assert (panel.bbox.x0, panel.bbox.y0, panel.bbox.y1) == pytest.approx((ref.LEFT, 620, 700), abs=0.5)
    # the full-bleed cover background is not a "box"
    assert not any(b.page_index == 0 and b.fill == ref.NAVY for b in raw.filled_boxes)


def test_old_json_without_new_fields_still_loads(raw):
    data = raw.model_dump(mode="json")
    for k in ("body_line_height_pt", "paragraph_spacing_pt", "filled_boxes"):
        data.pop(k)
    old = RawDesignStats.model_validate(data)
    assert old.body_line_height_pt is None and old.filled_boxes == []


def test_body_lines_in_footer_band_are_not_running_text(raw):
    assert raw.repeated_texts
    assert all(r.cluster_id != raw.body_cluster_id for r in raw.repeated_texts)
    assert all("closing line" not in r.pattern for r in raw.repeated_texts)


def test_text_lines_recorded_with_cluster_ids(raw):
    cover = [ln for ln in raw.text_lines if ln.page_index == 0]
    ids = {c.id: c for c in raw.font_clusters}
    assert cover and all(ln.cluster_id in ids for ln in cover)
    title = max(cover, key=lambda ln: ids[ln.cluster_id].size_pt)
    assert ids[title.cluster_id].size_pt == 40.0 and 200 < title.bbox.y0 < 260
    assert RawDesignStats.model_validate_json(raw.model_dump_json()).text_lines == raw.text_lines
