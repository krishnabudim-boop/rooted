"""Programmatic .docx fixtures for ingest tests."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from docx import Document
from docx.enum.text import WD_BREAK
from docx.opc.constants import RELATIONSHIP_TYPE as RT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches
from PIL import Image


def png_bytes(size=(40, 20), color=(200, 30, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def add_hyperlink(paragraph, url: str, text: str) -> None:
    rid = paragraph.part.relate_to(url, RT.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), rid)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def build_sample_docx(path: Path) -> Path:
    d = Document()
    d.core_properties.title = "Sample Title"
    d.core_properties.author = "Ada"
    d.add_heading("Report Title", level=0)  # Title style
    d.add_paragraph("A subtitle line", style="Subtitle")
    d.add_heading("Intro", level=1)
    p = d.add_paragraph()
    p.add_run("Plain ")
    p.add_run("bold").bold = True
    p.add_run(" and ")
    p.add_run("italic").italic = True
    p.add_run(" and ")
    r = p.add_run("both")
    r.bold = True
    r.italic = True
    p.add_run(" x")
    r = p.add_run("2")
    r.font.superscript = True
    d.add_paragraph("")  # empty
    d.add_paragraph("   ")  # whitespace only
    p = d.add_paragraph("See ")
    add_hyperlink(p, "https://example.com/x", "the site")
    d.add_heading("Section", level=2)
    d.add_heading("Sub", level=3)
    d.add_paragraph("Bullet one", style="List Bullet")
    d.add_paragraph("Nested bullet", style="List Bullet 2")
    d.add_paragraph("Number one", style="List Number")
    d.add_paragraph("Number two", style="List Number")
    d.add_paragraph("Quoted words", style="Quote")
    table = d.add_table(rows=3, cols=3)
    table.cell(0, 0).text = "H1"
    table.cell(0, 1).text = "H2"
    table.cell(0, 2).text = "H3"
    table.cell(1, 0).merge(table.cell(1, 1)).text = "wide"
    table.cell(1, 2).text = "tall"
    table.cell(2, 2).text = ""
    table.cell(1, 2).merge(table.cell(2, 2))
    table.cell(2, 0).text = "a"
    table.cell(2, 1).text = "b"
    d.add_paragraph("After table")
    d.add_paragraph().add_run().add_picture(io.BytesIO(png_bytes()), width=Inches(1))
    d.add_paragraph("Figure 1. A red box", style="Caption")
    d.add_paragraph("Before break").add_run().add_break(WD_BREAK.PAGE)
    d.add_paragraph("After break")
    d.save(path)
    return path


@pytest.fixture
def sample_docx(tmp_path: Path) -> Path:
    return build_sample_docx(tmp_path / "sample.docx")


@pytest.fixture
def assets_dir(tmp_path: Path) -> Path:
    return tmp_path / "work" / "assets"
