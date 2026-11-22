"""Programmatic inputs for the end-to-end test: a realistic .docx and a reference PDF."""

from __future__ import annotations

from pathlib import Path

import pymupdf
from docx import Document
from docx.shared import Inches
from PIL import Image, ImageDraw

REGIONS = {"North": 5200, "Central": 4100, "South": 3100}

_SENTENCES = [
    "The programme began with a small group of volunteers who believed that stable housing and reliable food "
    "are the foundation of every healthy community.",
    "Over the following months the team documented what worked, what failed, and what families told us they "
    "needed most.",
    "Partners in each district shared their records openly, which allowed us to compare outcomes on a like for "
    "like basis.",
    "Staff received regular training, and every case worker kept a log of visits so that no household was "
    "overlooked.",
    "We learned that small, early interventions cost far less than emergency responses later in the year.",
    "Local councils supplied meeting rooms and introductions, while donors funded the core operating budget.",
    "Feedback from residents was collected through short surveys, community evenings and informal conversations.",
    "The board reviewed progress each quarter and adjusted priorities in line with what the evidence showed.",
    "Transport remained a persistent barrier, so mobile outreach teams were introduced in the most remote areas.",
    "Every decision was recorded and published so that supporters could follow how their gifts were used.",
    "Nothing here would have been possible without the patience and generosity of the families themselves.",
    "A modest reserve fund protected the programme from sudden changes in funding and demand.",
]


def _para(i: int, n: int = 7) -> str:
    return " ".join(_SENTENCES[(i + k * 5) % len(_SENTENCES)] for k in range(n))


def build_docx(path: Path) -> Path:
    img = path.with_suffix(".png")
    im = Image.new("RGB", (900, 420), "#DDE3EE")
    d = ImageDraw.Draw(im)
    for k in range(6):
        d.rectangle([40 + k * 140, 380 - 40 * (k + 1), 140 + k * 140, 380], fill="#2F4B7C")
    im.save(img)

    doc = Document()
    doc.core_properties.title = "Rooted Annual Review"
    doc.core_properties.author = "Rooted Foundation"
    doc.add_heading("Rooted Annual Review", level=0)
    doc.add_paragraph("A year of building stable homes and food security for families across three regions.")

    doc.add_heading("Introduction", level=1)
    for i in range(3):
        doc.add_paragraph(_para(i))
    doc.add_heading("Why it matters", level=2)
    doc.add_paragraph(_para(3))

    doc.add_heading("Our reach", level=1)
    doc.add_paragraph(
        f"In 2025 our teams supported {REGIONS['North']:,} families in the North, {REGIONS['Central']:,} families "
        f"in the Central region and {REGIONS['South']:,} families in the South."
    )
    for i in range(3, 6):
        doc.add_paragraph(_para(i))
    doc.add_picture(str(img), width=Inches(5))
    doc.add_paragraph("Figure 1. Households visited during the year.")

    doc.add_heading("Programmes", level=1)
    doc.add_paragraph("Three programmes carried most of the work this year:")
    for t in ("Food security: weekly parcels and community kitchens.",
              "Housing support: deposits, repairs and tenancy advice.",
              "Community programmes: training, childcare and youth clubs."):
        doc.add_paragraph(t, style="List Bullet")
    for i in range(6, 9):
        doc.add_paragraph(_para(i))
    doc.add_paragraph("Case workers followed a simple three step process:")
    for t in ("Listen to the household and record its priorities.",
              "Agree a plan with the family and a named partner.",
              "Review progress after ninety days."):
        doc.add_paragraph(t, style="List Number")

    doc.add_heading("Results", level=1)
    doc.add_paragraph(
        "Satisfaction rose from 71% in 2023 to 84% in 2024 and 92% in 2025, while the cost per household fell by 18%."
    )
    tbl = doc.add_table(rows=4, cols=3)
    tbl.style = "Table Grid"
    rows = [("Region", "Families", "Satisfaction"), ("North", "5,200", "91%"), ("Central", "4,100", "93%"),
            ("South", "3,100", "90%")]
    for r, row in enumerate(rows):
        for c, v in enumerate(row):
            tbl.cell(r, c).text = v
    for i in range(9, 12):
        doc.add_paragraph(_para(i))

    doc.add_heading("Looking ahead", level=1)
    for i in range(12, 15):
        doc.add_paragraph(_para(i))
    doc.add_paragraph("We will keep every promise made in this report, and we thank everyone who made it possible.")
    doc.save(str(path))
    img.unlink()
    return path


NAVY_REF = "#7A1F3D"  # deliberately NOT navy: the brief must override the reference palette
TEAL = "#0E7C7B"


def _rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


_FILLER = (
    "Reference text sets the rhythm of the page: two even columns, a calm serif at ten and a half points and "
    "generous leading. Headings are bold sans serif in a deep colour. "
) * 30


def _fit_box(page, rect, text):
    """insert_textbox writes nothing when the text does not fit; trim until it does."""
    words = text.split()
    while words:
        rc = page.insert_textbox(rect, " ".join(words), fontname="tiro", fontsize=10.5, color=_rgb("#222222"))
        if rc >= 0:
            return
        words = words[:-4]


def build_reference(path: Path) -> Path:
    W, H = 595.28, 841.89
    L, R, T, B, G = 56.0, 56.0, 100.0, 64.0, 20.0
    doc = pymupdf.open()
    # cover
    p = doc.new_page(width=W, height=H)
    p.draw_rect(p.rect, color=None, fill=_rgb(TEAL))
    p.draw_rect(pymupdf.Rect(0, 640, W, 652), color=None, fill=_rgb("#F2B705"))
    p.insert_text((L, 300), "Annual Review", fontname="hebo", fontsize=44, color=(1, 1, 1))
    p.insert_text((L, 350), "Twenty Twenty Five", fontname="helv", fontsize=12, color=(1, 1, 1))
    for n in (2, 3, 4):
        p = doc.new_page(width=W, height=H)
        p.insert_text((L, 36), "Annual Review", fontname="helv", fontsize=8, color=_rgb("#555555"))
        p.draw_line((L, 44), (W - R, 44), color=_rgb("#AAAAAA"), width=0.5)
        p.insert_text((W / 2 - 10, H - 30), f"Page {n}", fontname="helv", fontsize=8, color=_rgb("#555555"))
        p.insert_text((L, T + 18), f"Section {n}", fontname="hebo", fontsize=22, color=_rgb(NAVY_REF))
        colw = (W - L - R - G) / 2
        top = T + 40
        bottom = 560 if n == 3 else H - B
        _fit_box(p, pymupdf.Rect(L, top, L + colw, bottom), _FILLER)
        _fit_box(p, pymupdf.Rect(L + colw + G, top, W - R, bottom), _FILLER)
        if n == 3:
            box = pymupdf.Rect(L, 600, W - R, 700)
            p.draw_rect(box, color=None, fill=_rgb("#EEF6F6"))
            p.draw_rect(pymupdf.Rect(L, 600, L + 5, 700), color=None, fill=_rgb("#F2B705"))
            p.insert_textbox(box + (16, 10, -10, -6), "Callout: the key idea of the section goes in a tinted box.",
                             fontname="hebo", fontsize=11, color=_rgb(NAVY_REF))
    doc.save(str(path))
    doc.close()
    return path
