"""Parse a .docx into a ContentDoc (WP1).

Uses python-docx for package access and walks the body XML in document order
(paragraphs AND tables), not ``document.paragraphs``.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import zipfile
from pathlib import Path
from typing import Iterator, Optional

from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from docx.oxml.ns import qn
from lxml import etree
from PIL import Image

from rooted.errors import InputError
from rooted.models import (
    Block,
    BlockType,
    ContentDoc,
    ImageRef,
    InlineRun,
    TableCell,
    TableData,
)

_A_BLIP = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"
_R_ID = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
_WP_EXTENT = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}extent"
_EMU_PER_PT = 12700.0
_WP_DOCPR = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}docPr"

_HEADING_RE = re.compile(r"^heading\s*([1-9])$", re.I)
_LIST_STYLE_RE = re.compile(r"^list\s+(bullet|number)(?:\s+(\d))?$", re.I)
_EXT_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "image/svg+xml": "svg",
    "image/x-emf": "emf",
    "image/x-wmf": "wmf",
}


def read_docx(path: Path, assets_dir: Path) -> ContentDoc:
    """Read ``path`` and return a ContentDoc.

    * Block ids are ``b0001``, ``b0002``... in document order (stable for the
      same input).
    * Embedded images are written to ``assets_dir`` as ``img_<sha8>.<ext>``;
      ``ImageRef.path`` is relative to ``assets_dir.parent`` (the work dir).
    * Empty paragraphs are skipped; explicit page breaks become PAGE_BREAK blocks.
    * Raises rooted.errors.InputError for missing/corrupt files.
    """
    path = Path(path)
    assets_dir = Path(assets_dir)
    if not path.is_file():
        raise InputError(f"docx not found: {path}")
    raw = path.read_bytes()
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        raise InputError(f"not a valid .docx (not a zip archive): {path}")
    try:
        document = Document(io.BytesIO(raw))
        body = document.element.body
    except (PackageNotFoundError, KeyError, ValueError, etree.XMLSyntaxError) as e:
        raise InputError(f"could not read .docx {path}: {e}") from e

    reader = _Reader(document, assets_dir)
    try:
        for child in _body_children(body):
            if child.tag == qn("w:p"):
                reader.paragraph(child)
            elif child.tag == qn("w:tbl"):
                reader.table(child)
    except (KeyError, ValueError, etree.XMLSyntaxError) as e:
        raise InputError(f"could not parse .docx {path}: {e}") from e

    props = document.core_properties
    words = sum(len(b.text.split()) for b in reader.blocks)
    return ContentDoc(
        source_path=str(path),
        source_sha256=hashlib.sha256(raw).hexdigest(),
        title=(props.title or None),
        author=(props.author or None),
        blocks=reader.blocks,
        word_count=words,
    )


def _body_children(parent) -> Iterator:
    """Yield w:p and w:tbl in order, descending into content controls (w:sdt)."""
    for child in parent.iterchildren():
        if child.tag == qn("w:sdt"):
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from _body_children(content)
        else:
            yield child


def _on(el, tag: str) -> Optional[bool]:
    """Tri-state toggle property value inside an rPr-like element."""
    if el is None:
        return None
    node = el.find(qn(tag))
    if node is None:
        return None
    val = node.get(qn("w:val"))
    return val not in ("0", "false", "off")


class _Reader:
    def __init__(self, document, assets_dir: Path):
        self.doc = document
        self.assets_dir = assets_dir
        self.blocks: list[Block] = []
        self._styles = {s.style_id: s for s in document.styles}
        self._abs_levels: dict[str, dict[int, tuple[str, int, str]]] = {}  # abstractId -> ilvl -> (fmt, start, text)
        self._num_abs: dict[str, str] = {}  # numId -> abstractId
        self._num_over: dict[str, dict[int, int]] = {}  # numId -> ilvl -> startOverride
        self._counters: dict[str, dict[int, int]] = {}  # abstractId -> ilvl -> current count
        self._seen_nums: set[str] = set()
        self._numbering = self._load_numbering()
        self._images: dict[str, ImageRef] = {}

    # -- numbering ----------------------------------------------------------
    def _load_numbering(self) -> dict[tuple[str, int], str]:
        """(numId, ilvl) -> numFmt."""
        out: dict[tuple[str, int], str] = {}
        try:
            part = self.doc.part.numbering_part
        except (KeyError, NotImplementedError):
            return out
        root = part.element
        abstract: dict[str, dict[int, str]] = {}
        for an in root.findall(qn("w:abstractNum")):
            levels = {}
            full: dict[int, tuple[str, int, str]] = {}
            for lvl in an.findall(qn("w:lvl")):
                fmt = lvl.find(qn("w:numFmt"))
                i = int(lvl.get(qn("w:ilvl"), "0"))
                f = fmt.get(qn("w:val")) if fmt is not None else "bullet"
                levels[i] = f
                st = lvl.find(qn("w:start"))
                tx = lvl.find(qn("w:lvlText"))
                full[i] = (
                    f,
                    int(st.get(qn("w:val"))) if st is not None else 1,
                    (tx.get(qn("w:val")) if tx is not None else None) or f"%{i + 1}.",
                )
            abstract[an.get(qn("w:abstractNumId"))] = levels
            self._abs_levels[an.get(qn("w:abstractNumId"))] = full
        for num in root.findall(qn("w:num")):
            aid = num.find(qn("w:abstractNumId"))
            if aid is None:
                continue
            nid = num.get(qn("w:numId"))
            self._num_abs[nid] = aid.get(qn("w:val"))
            for ov in num.findall(qn("w:lvlOverride")):
                so = ov.find(qn("w:startOverride"))
                if so is not None:
                    self._num_over.setdefault(nid, {})[int(ov.get(qn("w:ilvl"), "0"))] = int(so.get(qn("w:val")))
            for ilvl, fmt in abstract.get(aid.get(qn("w:val")), {}).items():
                out[(nid, ilvl)] = fmt
        return out

    def _list_label(self, num_id: str, ilvl: int) -> Optional[str]:
        """Advance the list counters for one item and return its marker ("3.", "a)"), None for bullets."""
        aid = self._num_abs.get(num_id)
        levels = self._abs_levels.get(aid or "")
        if not levels or ilvl not in levels:
            return None
        counters = self._counters.setdefault(aid, {})
        if num_id not in self._seen_nums:  # first use of a numId with startOverride restarts those levels
            self._seen_nums.add(num_id)
            for lv, start in self._num_over.get(num_id, {}).items():
                counters[lv] = start - 1
                for deeper in [k for k in counters if k > lv]:
                    del counters[deeper]
        fmt, start, text = levels[ilvl]
        counters[ilvl] = counters[ilvl] + 1 if ilvl in counters else self._num_over.get(num_id, {}).get(ilvl, start)
        for deeper in [k for k in counters if k > ilvl]:
            del counters[deeper]
        if fmt in ("bullet", "none"):
            return None

        def sub(m: re.Match) -> str:
            lv = int(m.group(1)) - 1
            lf = levels.get(lv, (fmt, 1, ""))
            return _format_number(counters.get(lv, lf[1]), lf[0])

        return re.sub(r"%([1-9])", sub, text)

    # -- helpers --------------------------------------------------------------
    def _add(self, type_: BlockType, **kw) -> Block:
        block = Block(id=f"b{len(self.blocks) + 1:04d}", type=type_, **kw)
        self.blocks.append(block)
        return block

    def _style_chain(self, style_id: Optional[str]):
        seen = set()
        style = self._styles.get(style_id) if style_id else None
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            yield style
            style = style.base_style

    def _style_ppr_find(self, style_id: Optional[str], tag: str):
        for style in self._style_chain(style_id):
            ppr = style.element.find(qn("w:pPr"))
            if ppr is not None and ppr.find(qn(tag)) is not None:
                return ppr.find(qn(tag))
        return None

    def _style_run_flag(self, style_id: Optional[str], tag: str) -> Optional[bool]:
        for style in self._style_chain(style_id):
            val = _on(style.element.find(qn("w:rPr")), tag)
            if val is not None:
                return val
        return None

    # -- runs -----------------------------------------------------------------
    def _run_format(self, r, para_style: Optional[str], link: Optional[str]) -> dict:
        rpr = r.find(qn("w:rPr"))
        rstyle_el = rpr.find(qn("w:rStyle")) if rpr is not None else None
        rstyle = rstyle_el.get(qn("w:val")) if rstyle_el is not None else None

        def flag(tag: str) -> bool:
            v = _on(rpr, tag)
            if v is None and rstyle:
                v = self._style_run_flag(rstyle, tag)
            return bool(v)

        va = rpr.find(qn("w:vertAlign")) if rpr is not None else None
        valign = va.get(qn("w:val")) if va is not None else None
        fonts = rpr.find(qn("w:rFonts")) if rpr is not None else None
        mono = False
        if fonts is not None:
            name = (fonts.get(qn("w:ascii")) or "").lower()
            mono = any(k in name for k in ("courier", "consolas", "mono", "menlo"))
        underline = False
        if rpr is not None and rpr.find(qn("w:u")) is not None:
            underline = rpr.find(qn("w:u")).get(qn("w:val")) not in ("none", None)
        return {
            "bold": flag("w:b"),
            "italic": flag("w:i"),
            "underline": underline,
            "superscript": valign == "superscript",
            "subscript": valign == "subscript",
            "code": mono,
            "link": link,
        }

    def _inline(self, p, para_style: Optional[str]):
        """Yield ('run', InlineRun) | ('image', ImageRef) | ('break', None) in order."""
        for child in p.iterchildren():
            if child.tag == qn("w:r"):
                yield from self._run_items(child, para_style, None)
            elif child.tag == qn("w:hyperlink"):
                link = self._hyperlink_target(child)
                for r in child.iterchildren(qn("w:r")):
                    yield from self._run_items(r, para_style, link)
            elif child.tag in (qn("w:sdt"), qn("w:smartTag")):
                content = child.find(qn("w:sdtContent")) if child.tag == qn("w:sdt") else child
                if content is not None:
                    for r in content.iterchildren(qn("w:r")):
                        yield from self._run_items(r, para_style, None)

    def _hyperlink_target(self, h) -> Optional[str]:
        rid = h.get(_R_ID)
        if rid:
            rel = self.doc.part.rels.get(rid)
            if rel is not None:
                return rel.target_ref
        anchor = h.get(qn("w:anchor"))
        return f"#{anchor}" if anchor else None

    def _run_items(self, r, para_style, link):
        fmt = self._run_format(r, para_style, link)
        buf: list[str] = []

        def flush():
            if buf:
                text = "".join(buf)
                buf.clear()
                return ("run", InlineRun(text=text, **fmt))
            return None

        for node in r.iterchildren():
            tag = node.tag
            if tag == qn("w:t"):
                buf.append(node.text or "")
            elif tag == qn("w:tab"):
                buf.append("\t")
            elif tag == qn("w:noBreakHyphen"):
                buf.append("‑")
            elif tag == qn("w:cr"):
                buf.append("\n")
            elif tag == qn("w:br"):
                if node.get(qn("w:type")) == "page":
                    item = flush()
                    if item:
                        yield item
                    yield ("break", None)
                else:
                    buf.append("\n")
            elif tag in (qn("w:drawing"), qn("w:pict")):
                item = flush()
                if item:
                    yield item
                for ref in self._images_in(node):
                    yield ("image", ref)
        item = flush()
        if item:
            yield item

    @staticmethod
    def _merge(runs: list[InlineRun]) -> list[InlineRun]:
        out: list[InlineRun] = []
        for run in runs:
            if run.text == "":
                continue
            if out and out[-1].model_dump(exclude={"text"}) == run.model_dump(exclude={"text"}):
                out[-1] = out[-1].model_copy(update={"text": out[-1].text + run.text})
            else:
                out.append(run)
        return out

    # -- images ---------------------------------------------------------------
    def _images_in(self, node) -> list[ImageRef]:
        refs = []
        for blip in node.iter(_A_BLIP):
            rid = blip.get(_R_EMBED)
            if not rid:
                continue
            part = self.doc.part.related_parts.get(rid)
            if part is None:
                continue
            alt = None
            for dp in node.iter(_WP_DOCPR):
                alt = dp.get("descr") or None
                break
            refs.append(self._store_image(part, alt, _extent_pt(blip)))
        for imd in node.iter("{urn:schemas-microsoft-com:vml}imagedata"):
            rid = imd.get(_R_ID)
            part = self.doc.part.related_parts.get(rid) if rid else None
            if part is not None:
                refs.append(self._store_image(part, imd.get("title")))
        return refs

    def _store_image(self, part, alt: Optional[str], extent: Optional[tuple[float, float]] = None) -> ImageRef:
        blob = part.blob
        sha8 = hashlib.sha256(blob).hexdigest()[:8]
        mime = part.content_type
        ext = _EXT_BY_MIME.get(mime) or os.path.splitext(str(part.partname))[1].lstrip(".") or "bin"
        name = f"img_{sha8}.{ext}"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        dest = self.assets_dir / name
        if not dest.exists():
            dest.write_bytes(blob)
        try:
            with Image.open(io.BytesIO(blob)) as im:
                w, h = im.size
        except Exception:
            w = h = 0
        rel = os.path.relpath(dest, self.assets_dir.parent).replace(os.sep, "/")
        wpt, hpt = extent if extent else (None, None)
        return ImageRef(path=rel, mime=mime, width_px=w, height_px=h, alt_text=alt, width_pt=wpt, height_pt=hpt)

    # -- paragraphs -----------------------------------------------------------
    def _classify(self, p):
        """Return (BlockType, level, ordered, style_name, num_id); num_id is set for numbered-list items."""
        ppr = p.find(qn("w:pPr"))
        sid_el = ppr.find(qn("w:pStyle")) if ppr is not None else None
        sid = sid_el.get(qn("w:val")) if sid_el is not None else None
        style = self._styles.get(sid) if sid else None
        name = style.name if style is not None else None
        lname = (name or "").strip().lower()

        # numbering: direct, else via style chain
        numpr = ppr.find(qn("w:numPr")) if ppr is not None else None
        if numpr is None:
            numpr = self._style_ppr_find(sid, "w:numPr")
        m = _HEADING_RE.match(lname)
        if m:
            return BlockType.HEADING, int(m.group(1)), False, name, None
        if lname == "title":
            return BlockType.HEADING, 1, False, name, None
        if lname in ("quote", "intense quote"):
            return BlockType.QUOTE, 0, False, name, None
        if lname == "caption":
            return BlockType.CAPTION, 0, False, name, None
        if numpr is not None:
            num_el = numpr.find(qn("w:numId"))
            num_id = num_el.get(qn("w:val")) if num_el is not None else None
            if num_id not in (None, "0"):
                ilvl_el = numpr.find(qn("w:ilvl"))
                ilvl = int(ilvl_el.get(qn("w:val"))) if ilvl_el is not None else 0
                if ilvl_el is None:
                    sm = _LIST_STYLE_RE.match(lname)
                    if sm and sm.group(2):
                        ilvl = int(sm.group(2)) - 1
                fmt = self._numbering.get((num_id, ilvl))
                ordered = fmt not in (None, "bullet", "none")
                if fmt is None:
                    sm = _LIST_STYLE_RE.match(lname)
                    ordered = bool(sm and sm.group(1).lower() == "number")
                return BlockType.LIST_ITEM, ilvl, ordered, name, num_id
        sm = _LIST_STYLE_RE.match(lname)
        if sm:
            return BlockType.LIST_ITEM, int(sm.group(2) or 1) - 1, sm.group(1).lower() == "number", name, None

        # outline level fallback
        ol = ppr.find(qn("w:outlineLvl")) if ppr is not None else None
        if ol is None:
            ol = self._style_ppr_find(sid, "w:outlineLvl")
        if ol is not None:
            lvl = int(ol.get(qn("w:val"))) + 1
            if 1 <= lvl <= 6:
                return BlockType.HEADING, lvl, False, name, None
        return BlockType.PARAGRAPH, 0, False, name, None

    def paragraph(self, p) -> None:
        ppr = p.find(qn("w:pPr"))
        if ppr is not None and ppr.find(qn("w:pageBreakBefore")) is not None and (
            _on(ppr, "w:pageBreakBefore") is not False
        ):
            self._page_break()
        btype, level, ordered, style_name, num_id = self._classify(p)
        runs: list[InlineRun] = []
        first_text_style = style_name

        def flush():
            merged = self._merge(runs)
            runs.clear()
            if merged and "".join(r.text for r in merged).strip() != "":
                extra: dict = {}
                if btype == BlockType.LIST_ITEM:
                    extra["list_id"] = num_id
                    if num_id is not None:
                        extra["list_label"] = self._list_label(num_id, level)
                self._add(btype, level=level, ordered=ordered, runs=merged, style_name=first_text_style, **extra)

        for kind, item in self._inline(p, style_name):
            if kind == "run":
                runs.append(item)
            elif kind == "image":
                flush()
                self._add(BlockType.IMAGE, image=item, style_name=style_name)
            elif kind == "break":
                flush()
                self._page_break()
        flush()

    def _page_break(self) -> None:
        self._add(BlockType.PAGE_BREAK)

    # -- tables ---------------------------------------------------------------
    def _cell_runs(self, tc) -> list[InlineRun]:
        runs: list[InlineRun] = []
        first = True
        for p in tc.iterchildren(qn("w:p")):
            para_runs = []
            for kind, item in self._inline(p, None):
                if kind == "run":
                    para_runs.append(item)
            if not para_runs and not first:
                continue
            if not first and runs and para_runs:
                runs.append(InlineRun(text="\n"))
            runs.extend(para_runs)
            first = first and not para_runs
        return self._merge(runs)

    def table(self, tbl) -> None:
        rows: list[list[TableCell]] = []
        open_cells: dict[int, TableCell] = {}  # grid col -> cell awaiting vMerge continuation
        header_flags: list[bool] = []
        for tr in tbl.iterchildren(qn("w:tr")):
            trpr = tr.find(qn("w:trPr"))
            header_flags.append(trpr is not None and trpr.find(qn("w:tblHeader")) is not None)
            row: list[TableCell] = []
            col = 0
            for tc in _tr_cells(tr):
                tcpr = tc.find(qn("w:tcPr"))
                span = 1
                vmerge = None
                if tcpr is not None:
                    gs = tcpr.find(qn("w:gridSpan"))
                    if gs is not None:
                        span = max(1, int(gs.get(qn("w:val"))))
                    vm = tcpr.find(qn("w:vMerge"))
                    if vm is not None:
                        vmerge = vm.get(qn("w:val")) or "continue"
                if vmerge == "continue" and col in open_cells:
                    open_cells[col].rowspan += 1
                else:
                    cell = TableCell(runs=self._cell_runs(tc), colspan=span)
                    row.append(cell)
                    if vmerge == "restart":
                        open_cells[col] = cell
                    else:
                        open_cells.pop(col, None)
                col += span
            rows.append(row)
        if not rows:
            return
        header_rows = 0
        for flag in header_flags:
            if not flag:
                break
            header_rows += 1
        if header_rows == 0:
            look = tbl.find(qn("w:tblPr") + "/" + qn("w:tblLook"))
            first_row_off = look is not None and look.get(qn("w:firstRow")) in ("0", "false")
            header_rows = 0 if first_row_off else 1
        self._add(BlockType.TABLE, table=TableData(rows=rows, header_rows=header_rows),
                  style_name=_table_style(self, tbl))


def _extent_pt(blip) -> Optional[tuple[float, float]]:
    """Display size (pt) from the enclosing drawing's wp:extent."""
    for anc in blip.iterancestors():
        ext = anc.find(_WP_EXTENT)
        if ext is not None:
            try:
                cx, cy = float(ext.get("cx", 0)), float(ext.get("cy", 0))
            except ValueError:
                return None
            if cx > 0 and cy > 0:
                return round(cx / _EMU_PER_PT, 2), round(cy / _EMU_PER_PT, 2)
            return None
    return None


def _format_number(n: int, fmt: str) -> str:
    if fmt in ("lowerLetter", "upperLetter"):
        n = max(n, 1)
        out = ""
        while n > 0:
            n, r = divmod(n - 1, 26)
            out = chr(97 + r) + out
        return out.upper() if fmt == "upperLetter" else out
    if fmt in ("lowerRoman", "upperRoman"):
        vals = [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"),
                (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")]
        out, k = "", max(n, 1)
        for v, sym in vals:
            while k >= v:
                out += sym
                k -= v
        return out.upper() if fmt == "upperRoman" else out
    if fmt == "decimalZero":
        return f"{n:02d}"
    return str(n)


def _tr_cells(tr) -> Iterator:
    for child in tr.iterchildren():
        if child.tag == qn("w:tc"):
            yield child
        elif child.tag == qn("w:sdt"):
            content = child.find(qn("w:sdtContent"))
            if content is not None:
                yield from content.iterchildren(qn("w:tc"))


def _table_style(reader: _Reader, tbl) -> Optional[str]:
    st = tbl.find(qn("w:tblPr") + "/" + qn("w:tblStyle"))
    if st is None:
        return None
    style = reader._styles.get(st.get(qn("w:val")))
    return style.name if style is not None else None


__all__ = ["read_docx"]
