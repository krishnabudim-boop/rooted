"""Programmatic 4-page reference PDF with known geometry (PyMuPDF only).

Page 1: full-bleed navy cover, white 40pt bold title, gold band.
Page 2: Helvetica 10pt justified body, 20pt bold navy H1, 14pt bold H2, header + footer.
Page 3: two-column body (gutter 18pt), light callout box with gold bar, header + footer.
Page 4: data page: H1, table from rects, thin rule, header + footer.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf

W, H = 595.28, 841.89
LEFT, RIGHT, TOP, BOTTOM = 56.0, 56.0, 64.0, 64.0
GUTTER = 18.0
NAVY = "#1F2A44"
GOLD = "#C9A227"
TEXT = "#222222"
GRAY = "#6B7280"
SURFACE = "#F3F1EA"
BODY_SIZE, H1_SIZE, H2_SIZE, TITLE_SIZE = 10.0, 20.0, 14.0, 40.0
ASC = 1.07  # Helvetica-Bold ascender used by PyMuPDF span bboxes (y0 = baseline - ASC*size)
DESC = 0.299  # Helvetica descender (y1 = baseline + DESC*size)

LOREM = (
    "Rooted helps families put down roots in safe and stable homes through food security, "
    "housing support and community programs delivered across three regions. "
) * 14


def rgb(h: str) -> tuple[float, float, float]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _heading(page, text, y_top, size, color, x=LEFT):
    page.insert_text((x, y_top + ASC * size), text, fontname="hebo", fontsize=size, color=rgb(color))


def _chrome(page, n):
    page.insert_text((LEFT, 30), "Annual Impact Report", fontname="helv", fontsize=8, color=rgb(GRAY))
    page.insert_text((W - RIGHT - 30, H - 30), f"Page {n}", fontname="helv", fontsize=8, color=rgb(GRAY))
    page.draw_line((LEFT, 40), (W - RIGHT, 40), color=rgb(GRAY), width=0.5)


def _last_line(page, text):
    """A closing body line whose bbox bottom sits exactly on the bottom margin."""
    size = BODY_SIZE
    page.insert_text((LEFT, H - BOTTOM - DESC * size), text, fontname="helv",
                     fontsize=size, color=rgb(TEXT))


def _body(page, rect, text=LOREM):
    page.insert_textbox(rect, text, fontname="helv", fontsize=BODY_SIZE, color=rgb(TEXT),
                        align=pymupdf.TEXT_ALIGN_JUSTIFY)


def build_reference_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    # --- page 1: cover
    p = doc.new_page(width=W, height=H)
    p.draw_rect(p.rect, color=None, fill=rgb(NAVY))
    p.insert_text((LEFT + 20, 250), "Annual Impact", fontname="hebo", fontsize=TITLE_SIZE, color=(1, 1, 1))
    p.insert_text((LEFT + 20, 300), "Report 2025", fontname="hebo", fontsize=TITLE_SIZE, color=(1, 1, 1))
    p.draw_rect(pymupdf.Rect(0, 700, W, 712), color=None, fill=rgb(GOLD))
    p.insert_text((LEFT + 20, 780), "Rooted Foundation", fontname="helv", fontsize=12, color=(1, 1, 1))

    # --- page 2: single column body
    p = doc.new_page(width=W, height=H)
    _chrome(p, 2)
    _heading(p, "Introduction", TOP, H1_SIZE, NAVY)
    _body(p, pymupdf.Rect(LEFT, TOP + 36, W - RIGHT, 500), LOREM[:1800])
    _heading(p, "Our mission", 520, H2_SIZE, NAVY)
    _body(p, pymupdf.Rect(LEFT, 546, W - RIGHT, 700), LOREM[:900])
    _last_line(p, "A distinct closing line on page two.")

    # --- page 3: two columns + callout
    p = doc.new_page(width=W, height=H)
    _chrome(p, 3)
    _heading(p, "Programs", TOP, H1_SIZE, NAVY)
    colw = (W - LEFT - RIGHT - GUTTER) / 2
    _body(p, pymupdf.Rect(LEFT, TOP + 36, LEFT + colw, 600), LOREM[:1500])
    _body(p, pymupdf.Rect(LEFT + colw + GUTTER, TOP + 36, W - RIGHT, 600), LOREM[200:1700])
    box = pymupdf.Rect(LEFT, 620, W - RIGHT, 700)
    p.draw_rect(box, color=None, fill=rgb(SURFACE))
    p.draw_rect(pymupdf.Rect(LEFT, 620, LEFT + 4, 700), color=None, fill=rgb(GOLD))
    p.insert_textbox(box + (14, 8, -8, -4), "Callout: every family deserves a place to put down roots.",
                     fontname="hebo", fontsize=11, color=rgb(NAVY))
    _last_line(p, "Another closing line, this time on page three.")

    # --- page 4: data page with a table drawn from rects
    p = doc.new_page(width=W, height=H)
    _chrome(p, 4)
    _heading(p, "Results", TOP, H1_SIZE, NAVY)
    y = TOP + 50
    for r in range(4):
        cell = pymupdf.Rect(LEFT, y + r * 24, W - RIGHT, y + (r + 1) * 24)
        if r == 0:
            p.draw_rect(cell, color=None, fill=rgb(NAVY))
        p.insert_text((LEFT + 6, y + r * 24 + 16), ["Region", "North", "Central", "South"][r], fontname="helv",
                      fontsize=9, color=(1, 1, 1) if r == 0 else rgb(TEXT))
        p.draw_line((LEFT, y + (r + 1) * 24), (W - RIGHT, y + (r + 1) * 24), color=rgb("#D1D5DB"), width=0.5)
    _body(p, pymupdf.Rect(LEFT, 260, W - RIGHT, 500), "Program reach by region. " * 30)

    path = Path(path)
    doc.save(str(path))
    doc.close()
    return path
