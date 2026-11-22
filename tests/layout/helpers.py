"""Helpers for layout render tests."""

from __future__ import annotations

import unicodedata

import pymupdf

from rooted.models import (
    Block,
    BlockType,
    ContentDoc,
    ContentPlan,
    CoverPlan,
    InlineRun,
    PageType,
    Section,
    VisualPlan,
    VisualSet,
)
from tests.factories import sample_content_plan, verbatim

LOREM = (
    "Rooted supports families through housing, food security and community programs. "
    "Each program is run with local partners who know their neighbourhoods and measure results every quarter. "
    "In {n} case studies we found that stable housing improved school attendance, employment and health."
)


def norm(text: str) -> str:
    """Normalise for verbatim comparison: NFKC (ligatures), collapse whitespace."""
    return " ".join(unicodedata.normalize("NFKC", text).replace("­", "").split())


def long_inputs(paragraphs_per_section: int = 14, sections: int = 3):
    """A longer document: cover + N sections of lorem-like paragraphs (all text unique)."""
    blocks = [
        Block(id="b0001", type=BlockType.HEADING, level=1, runs=[InlineRun(text="Long Report")]),
        Block(id="b0002", type=BlockType.PARAGRAPH, runs=[InlineRun(text="Prepared by Rooted")]),
    ]
    secs = []
    n = 3
    for s in range(sections):
        ids = []
        hid = f"b{n:04d}"
        blocks.append(
            Block(id=hid, type=BlockType.HEADING, level=1, runs=[InlineRun(text=f"Chapter {s + 1} overview")])
        )
        ids.append(hid)
        n += 1
        for p in range(paragraphs_per_section):
            bid = f"b{n:04d}"
            blocks.append(
                Block(
                    id=bid, type=BlockType.PARAGRAPH, runs=[InlineRun(text=f"[{s + 1}.{p + 1}] " + LOREM.format(n=n))]
                )
            )
            ids.append(bid)
            n += 1
        secs.append((hid, ids))
    doc = ContentDoc(
        source_path="long.docx",
        source_sha256="0" * 64,
        title="Long Report",
        author="Rooted",
        blocks=blocks,
        word_count=sum(len(b.text.split()) for b in blocks),
    )
    base = sample_content_plan()
    plan = ContentPlan(
        profile=base.profile,
        cover=CoverPlan(
            title=verbatim(doc, "b0001", "Long Report"),
            byline=verbatim(doc, "b0002", "Prepared by Rooted"),
            consumed_block_ids=["b0001", "b0002"],
        ),
        sections=[
            Section(
                id=f"s{i + 1:02d}",
                title=verbatim(doc, hid, doc.block(hid).text),
                heading_block_id=hid,
                block_ids=ids,
                opener=True,
                page_type=PageType.BODY,
            )
            for i, (hid, ids) in enumerate(secs)
        ],
    )
    return doc, plan, VisualSet(plan=VisualPlan(visuals=[]))


def pdf_text(path, doc_pages=None) -> str:
    """Text of all pages. Headers/footers live in the page margins, so body
    pages are clipped to the area between them."""
    out = []
    with pymupdf.open(str(path)) as d:
        for page in d:
            r = page.rect
            out.append(page.get_text("text", clip=pymupdf.Rect(r.x0, r.y0 + 52, r.x1, r.y1 - 52)))
    return "\n".join(out)
