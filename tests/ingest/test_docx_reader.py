from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from docx import Document

from rooted.errors import InputError
from rooted.ingest import read_docx
from rooted.models import BlockType, ContentDoc


def _types(doc: ContentDoc):
    return [b.type for b in doc.blocks]


def test_block_sequence(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    T = BlockType
    assert _types(doc) == [
        T.HEADING, T.PARAGRAPH, T.HEADING, T.PARAGRAPH, T.PARAGRAPH, T.HEADING, T.HEADING,
        T.LIST_ITEM, T.LIST_ITEM, T.LIST_ITEM, T.LIST_ITEM, T.QUOTE, T.TABLE, T.PARAGRAPH,
        T.IMAGE, T.CAPTION, T.PARAGRAPH, T.PAGE_BREAK, T.PARAGRAPH,
    ]
    assert doc.title == "Sample Title" and doc.author == "Ada"
    assert doc.word_count > 10


def test_ids_deterministic(sample_docx, assets_dir, tmp_path):
    a = read_docx(sample_docx, assets_dir)
    b = read_docx(sample_docx, tmp_path / "other" / "assets")
    assert a.block_ids == b.block_ids
    assert a.block_ids[:3] == ["b0001", "b0002", "b0003"]
    assert a.source_sha256 == b.source_sha256


def test_headings_and_styles(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    assert [(b.level, b.text) for b in doc.blocks if b.type == BlockType.HEADING] == [
        (1, "Report Title"), (1, "Intro"), (2, "Section"), (3, "Sub"),
    ]
    sub = doc.blocks[1]
    assert sub.type == BlockType.PARAGRAPH and sub.style_name == "Subtitle"


def test_runs_formatting_and_merge(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    runs = doc.blocks[3].runs
    assert [r.text for r in runs] == ["Plain ", "bold", " and ", "italic", " and ", "both", " x", "2"]
    assert runs[1].bold and not runs[1].italic
    assert runs[3].italic and not runs[3].bold
    assert runs[5].bold and runs[5].italic
    assert runs[7].superscript


def test_adjacent_identical_runs_merged(tmp_path, assets_dir):
    d = Document()
    p = d.add_paragraph()
    for t in ("ab", "cd", "ef"):
        p.add_run(t).bold = True
    p.add_run("gh")
    d.save(tmp_path / "m.docx")
    doc = read_docx(tmp_path / "m.docx", assets_dir)
    assert [(r.text, r.bold) for r in doc.blocks[0].runs] == [("abcdef", True), ("gh", False)]


def test_hyperlink(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    runs = doc.blocks[4].runs
    assert runs[0].text == "See " and runs[0].link is None
    assert runs[1].text == "the site" and runs[1].link == "https://example.com/x"


def test_empty_paragraphs_skipped(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    assert all(b.text.strip() for b in doc.blocks if b.type not in (BlockType.IMAGE, BlockType.PAGE_BREAK))


def test_lists(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    items = [b for b in doc.blocks if b.type == BlockType.LIST_ITEM]
    assert [(b.text, b.level, b.ordered) for b in items] == [
        ("Bullet one", 0, False), ("Nested bullet", 1, False), ("Number one", 0, True), ("Number two", 0, True),
    ]


def test_numpr_lists(tmp_path, assets_dir):
    """Lists defined by direct w:numPr (ilvl) resolve ordered via numFmt."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = Document()
    for style, ilvl in (("List Number", 0), ("List Number", 1)):
        p = d.add_paragraph(f"lvl{ilvl}", style=style)
        numpr = OxmlElement("w:numPr")
        il = OxmlElement("w:ilvl")
        il.set(qn("w:val"), str(ilvl))
        numpr.append(il)
        # keep the style's numId
        nid = OxmlElement("w:numId")
        style_numpr = d.styles[style].element.pPr.find(qn("w:numPr"))
        nid.set(qn("w:val"), style_numpr.find(qn("w:numId")).get(qn("w:val")))
        numpr.append(nid)
        p._p.get_or_add_pPr().append(numpr)
    d.save(tmp_path / "n.docx")
    doc = read_docx(tmp_path / "n.docx", assets_dir)
    assert [(b.level, b.ordered) for b in doc.blocks] == [(0, True), (1, True)]


def test_outline_level_fallback(tmp_path, assets_dir):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = Document()
    p = d.add_paragraph("Outlined")
    ol = OxmlElement("w:outlineLvl")
    ol.set(qn("w:val"), "1")
    p._p.get_or_add_pPr().append(ol)
    d.save(tmp_path / "o.docx")
    b = read_docx(tmp_path / "o.docx", assets_dir).blocks[0]
    assert (b.type, b.level) == (BlockType.HEADING, 2)


def test_table_merges(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    tb = next(b for b in doc.blocks if b.type == BlockType.TABLE)
    rows = tb.table.rows
    assert tb.table.header_rows == 1
    assert [[c.text for c in r] for r in rows] == [["H1", "H2", "H3"], ["wide", "tall"], ["a", "b"]]
    wide, tall = rows[1]
    assert wide.colspan == 2 and wide.rowspan == 1
    assert tall.rowspan == 2 and tall.colspan == 1


def test_table_header_flag_and_none(tmp_path, assets_dir):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = Document()
    t = d.add_table(rows=3, cols=1)
    for r in t.rows:
        r.cells[0].text = "x"
    for r in list(t.rows)[:2]:
        r._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
    d.save(tmp_path / "t.docx")
    assert read_docx(tmp_path / "t.docx", assets_dir).blocks[0].table.header_rows == 2

    d = Document()
    t = d.add_table(rows=2, cols=1)
    look = t._tbl.tblPr.find(qn("w:tblLook"))
    if look is None:
        look = OxmlElement("w:tblLook")
        t._tbl.tblPr.append(look)
    look.set(qn("w:firstRow"), "0")
    d.save(tmp_path / "t2.docx")
    assert read_docx(tmp_path / "t2.docx", assets_dir).blocks[0].table.header_rows == 0


def test_table_cell_formatting_and_multi_paragraph(tmp_path, assets_dir):
    d = Document()
    t = d.add_table(rows=1, cols=1)
    c = t.cell(0, 0)
    c.paragraphs[0].add_run("one ").bold = True
    c.add_paragraph("two")
    d.save(tmp_path / "c.docx")
    cell = read_docx(tmp_path / "c.docx", assets_dir).blocks[0].table.rows[0][0]
    assert cell.text == "one \ntwo"
    assert cell.runs[0].bold


def test_image(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    img = next(b for b in doc.blocks if b.type == BlockType.IMAGE).image
    assert img.mime == "image/png"
    assert (img.width_px, img.height_px) == (40, 20)
    assert img.path.startswith("assets/img_") and img.path.endswith(".png")
    assert (assets_dir.parent / img.path).is_file()


def test_body_order_table_between_paragraphs(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    i = _types(doc).index(BlockType.TABLE)
    assert doc.blocks[i - 1].text == "Quoted words"
    assert doc.blocks[i + 1].text == "After table"


def test_page_break_splits_text(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    i = _types(doc).index(BlockType.PAGE_BREAK)
    assert doc.blocks[i - 1].text == "Before break"
    assert doc.blocks[i + 1].text == "After break"


def test_roundtrip_json(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    assert ContentDoc.model_validate_json(doc.model_dump_json()) == doc


def test_errors(tmp_path, assets_dir):
    with pytest.raises(InputError):
        read_docx(tmp_path / "missing.docx", assets_dir)
    bad = tmp_path / "bad.docx"
    bad.write_text("not a zip")
    with pytest.raises(InputError):
        read_docx(bad, assets_dir)
    z = tmp_path / "empty.docx"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("hello.txt", "hi")
    with pytest.raises(InputError):
        read_docx(z, assets_dir)
    assert isinstance(Path(bad), Path)


def test_image_display_extent_in_points(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    img = next(b for b in doc.blocks if b.type == BlockType.IMAGE).image
    assert (img.width_pt, img.height_pt) == (pytest.approx(72.0), pytest.approx(36.0))  # 1 inch wide, 2:1


def _numbered_docx(path: Path):
    """Three items on the style's list, then two items on a NEW numId that restarts at 1."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    d = Document()
    base = d.styles["List Number"].element.pPr.find(qn("w:numPr")).find(qn("w:numId")).get(qn("w:val"))
    numbering = d.part.numbering_part.element
    abstract = next(n for n in numbering.findall(qn("w:num")) if n.get(qn("w:numId")) == base)
    abs_id = abstract.find(qn("w:abstractNumId")).get(qn("w:val"))
    new = OxmlElement("w:num")
    new.set(qn("w:numId"), "99")
    a = OxmlElement("w:abstractNumId")
    a.set(qn("w:val"), abs_id)
    new.append(a)
    ov = OxmlElement("w:lvlOverride")
    ov.set(qn("w:ilvl"), "0")
    so = OxmlElement("w:startOverride")
    so.set(qn("w:val"), "1")
    ov.append(so)
    new.append(ov)
    numbering.append(new)
    for t in ("one", "two", "three"):
        d.add_paragraph(t, style="List Number")
    for t in ("again one", "again two"):
        p = d.add_paragraph(t, style="List Number")
        numpr = OxmlElement("w:numPr")
        nid = OxmlElement("w:numId")
        nid.set(qn("w:val"), "99")
        numpr.append(nid)
        p._p.get_or_add_pPr().append(numpr)
    d.save(path)


def test_list_labels_ids_and_restart(tmp_path, assets_dir):
    _numbered_docx(tmp_path / "n.docx")
    doc = read_docx(tmp_path / "n.docx", assets_dir)
    items = [b for b in doc.blocks if b.type == BlockType.LIST_ITEM]
    assert [b.list_label for b in items] == ["1.", "2.", "3.", "1.", "2."]
    assert [b.list_id for b in items][3:] == ["99", "99"]
    assert items[0].list_id != items[3].list_id and all(b.ordered for b in items)


def test_bullets_have_no_label(sample_docx, assets_dir):
    doc = read_docx(sample_docx, assets_dir)
    bullets = [b for b in doc.blocks if b.type == BlockType.LIST_ITEM and not b.ordered]
    assert bullets and all(b.list_label is None for b in bullets)
    numbered = [b for b in doc.blocks if b.type == BlockType.LIST_ITEM and b.ordered]
    assert [b.list_label for b in numbered] == ["1.", "2."]
