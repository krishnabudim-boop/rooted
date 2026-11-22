"""Deterministic PyMuPDF extraction (WP3). No LLM."""

from __future__ import annotations

import hashlib
import logging
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pymupdf

from rooted.design import color as C
from rooted.errors import InputError
from rooted.models import (
    BBox,
    ColorStat,
    FilledRect,
    FontCluster,
    Margins,
    RawDesignStats,
    RawPageStats,
    RawTextLine,
    RepeatedText,
    RuleStat,
)

log = logging.getLogger(__name__)

MAX_BOXES = 200
MAX_LINES_PER_PAGE = 80
HF_BAND = 0.12  # top/bottom fraction of the page searched for running text
MERGE_DE = 3.0  # colors closer than this (CIE76) are merged
_SUBSET = re.compile(r"^[A-Z]{6}\+")
_USAGE_IMPORTANCE = {"text": 0.4, "fill": 0.4, "stroke": 0.05, "image": 0.15}


def normalize_font_name(name: str) -> str:
    return _SUBSET.sub("", name or "").strip() or "Unknown"


@dataclass
class _Line:
    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    chars: int
    size: float
    cluster_key: tuple
    horizontal: bool = True
    hf: bool = False


@dataclass
class _Cluster:
    key: tuple
    font_name: str
    size: float
    bold: bool
    italic: bool
    serif: bool
    mono: bool
    color: str
    chars: int = 0
    pages: set = field(default_factory=set)
    sample: str = ""


def _flags(span: dict) -> tuple[bool, bool, bool, bool]:
    f = span.get("flags", 0)
    name = span.get("font", "").lower()
    bold = bool(f & 16) or any(t in name for t in ("bold", "black", "heavy", "semibold", "demi"))
    italic = bool(f & 2) or any(t in name for t in ("italic", "oblique"))
    mono = bool(f & 8) or any(t in name for t in ("mono", "courier", "consolas", "menlo"))
    serif = bool(f & 4) or any(t in name for t in ("times", "georgia", "garamond", "serif", "minion", "palatino"))
    if "sans" in name:
        serif = False
    return bold, italic, serif, mono


def _percentile(vals: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(vals, dtype=float), q))


def extract_raw(pdf_path: Path) -> RawDesignStats:
    """Use ``page.get_text("dict")`` spans (font, size, flags, color, bbox),
    ``page.get_drawings()`` (fills, strokes, rules), ``page.get_image_info()``.
    Cluster spans into FontCluster by (normalized name, round(size*2)/2, bold,
    italic, color). Margins = robust percentiles of body-text bboxes. Columns
    via x-histogram of body-span left edges. Repeated text = same normalized
    pattern within +-3pt y on >= 50% of pages."""
    pdf_path = Path(pdf_path)
    try:
        data = pdf_path.read_bytes()
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise InputError(f"cannot open reference PDF {pdf_path}: {e}") from e
    if doc.needs_pass or doc.is_encrypted:
        raise InputError(f"reference PDF is encrypted: {pdf_path}")
    if doc.page_count == 0:
        raise InputError(f"reference PDF has no pages: {pdf_path}")
    sha = hashlib.sha256(data).hexdigest()

    clusters: dict[tuple, _Cluster] = {}
    lines: list[_Line] = []
    text_chars_by_color: dict[str, int] = defaultdict(int)
    fill_area: dict[str, float] = defaultdict(float)
    stroke_len: dict[str, float] = defaultdict(float)
    image_area: dict[str, float] = defaultdict(float)
    rules: list[RuleStat] = []
    boxes: list[FilledRect] = []
    page_stats_partial: list[dict] = []
    embedded_names: set[str] = set()
    img_budget = 30

    for pno in range(doc.page_count):
        page = doc[pno]
        pw, ph = page.rect.width, page.rect.height
        page_area = pw * ph
        for f in page.get_fonts():
            if f[1] != "n/a":
                embedded_names.add(normalize_font_name(f[3]))

        # ---- text ----
        char_count = 0
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for ln in block["lines"]:
                spans = [s for s in ln["spans"] if s["text"].strip()]
                if not spans:
                    continue
                best = None
                for s in spans:
                    bold, italic, serif, mono = _flags(s)
                    name = normalize_font_name(s["font"])
                    hexc = C.int_to_hex(s["color"])
                    size = round(s["size"] * 2) / 2
                    key = (name, size, bold, italic, hexc)
                    cl = clusters.get(key)
                    if cl is None:
                        cl = clusters[key] = _Cluster(key, name, size, bold, italic, serif, mono, hexc)
                    n = len(s["text"].strip())
                    cl.chars += n
                    cl.pages.add(pno)
                    if len(s["text"].strip()) > len(cl.sample):
                        cl.sample = s["text"].strip()[:120]
                    text_chars_by_color[hexc] += n
                    char_count += n
                    if best is None or n > best[0]:
                        best = (n, key, s["size"])
                x0, y0, x1, y1 = ln["bbox"]
                d = ln.get("dir", (1, 0))
                text = " ".join(" ".join(s["text"].split()) for s in spans)
                lines.append(_Line(pno, x0, y0, x1, y1, text, sum(len(s["text"].strip()) for s in spans),
                                   best[2], best[1], horizontal=abs(d[1]) < 0.1))

        # ---- drawings ----
        page_fill: dict[str, float] = defaultdict(float)
        prect = page.rect
        for dr in page.get_drawings():
            rect = dr.get("rect")
            if rect is None:
                continue
            kind = dr.get("type", "")
            if "f" in kind and dr.get("fill") is not None:
                hexc = C.floats_to_hex(dr["fill"])
                opacity = dr.get("fill_opacity", 1.0)
                if hexc and hexc != "#FFFFFF" and (opacity is None or opacity >= 0.05):
                    r = rect & prect
                    w, h = rect.width, rect.height
                    if min(w, h) < 2 and max(w, h) >= 10:
                        rules.append(RuleStat(page_index=pno, bbox=BBox(x0=rect.x0, y0=rect.y0, x1=rect.x1, y1=rect.y1),
                                              color=hexc, width_pt=round(max(min(w, h), 0.1), 3)))
                    area = max(r.width, 0) * max(r.height, 0) if not r.is_empty else 0.0
                    if (len(boxes) < MAX_BOXES and not r.is_empty and min(r.width, r.height) >= 2 and max(r.width, r.height) >= 6
                            and area < 0.9 * page_area):
                        boxes.append(FilledRect(page_index=pno, bbox=BBox(x0=r.x0, y0=r.y0, x1=r.x1, y1=r.y1),
                                                fill=hexc))
                    fill_area[hexc] += area
                    page_fill[hexc] += area
            if "s" in kind and dr.get("color") is not None:
                hexc = C.floats_to_hex(dr["color"])
                width = dr.get("width") or 1.0
                if hexc:
                    for it in dr["items"]:
                        if it[0] == "l":
                            p1, p2 = it[1], it[2]
                            length = ((p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2) ** 0.5
                            stroke_len[hexc] += length * width
                            if length >= 10 and (abs(p1.y - p2.y) < 0.5 or abs(p1.x - p2.x) < 0.5):
                                rules.append(RuleStat(
                                    page_index=pno,
                                    bbox=BBox(x0=min(p1.x, p2.x), y0=min(p1.y, p2.y),
                                              x1=max(p1.x, p2.x), y1=max(p1.y, p2.y)),
                                    color=hexc, width_pt=round(width, 3)))
                        elif it[0] == "re":
                            rr = it[1]
                            stroke_len[hexc] += 2 * (rr.width + rr.height) * width

        # ---- images ----
        img_area = 0.0
        for info in page.get_image_info():
            ib = pymupdf.Rect(info["bbox"]) & prect
            if ib.is_empty:
                continue
            img_area += ib.width * ib.height
            if img_budget > 0 and ib.width * ib.height > 400:
                img_budget -= 1
                for hexc, share in _image_palette(page, ib):
                    image_area[hexc] += share * ib.width * ib.height
        dom = max(page_fill.items(), key=lambda kv: (kv[1], kv[0]))[0] if page_fill else None
        page_stats_partial.append({
            "index": pno, "w": pw, "h": ph, "chars": char_count,
            "img": min(img_area / page_area, 1.0) if page_area else 0.0,
            "fill": min(sum(page_fill.values()) / page_area, 1.0) if page_area else 0.0,
            "dom": dom,
        })
    doc.close()

    n_pages = len(page_stats_partial)
    mid_chars: dict[tuple, int] = defaultdict(int)
    for ln in lines:
        ph = page_stats_partial[ln.page]["h"]
        if ph * HF_BAND <= (ln.y0 + ln.y1) / 2 <= ph * (1 - HF_BAND):
            mid_chars[ln.cluster_key] += ln.chars
    pre_body = max(mid_chars.items(), key=lambda kv: (kv[1], kv[0][1]))[0] if mid_chars else None
    repeated = _find_repeated(lines, page_stats_partial, clusters, body_key=pre_body)

    # ---- cluster ids ----
    ordered = sorted(clusters.values(), key=lambda c: (-c.chars, -c.size, c.font_name, c.color))
    ids = {c.key: f"f{i + 1:02d}" for i, c in enumerate(ordered)}
    font_clusters = [
        FontCluster(id=ids[c.key], font_name=c.font_name, size_pt=c.size, bold=c.bold, italic=c.italic,
                    serif=c.serif, monospace=c.mono, color=c.color, char_count=c.chars,
                    page_indices=sorted(c.pages), sample_text=c.sample,
                    embedded=c.font_name in embedded_names)
        for c in ordered
    ]
    for r in repeated:
        r_key = r.pop("key")
        r["obj"].cluster_id = ids.get(r_key)
    repeated_models = [r["obj"] for r in repeated]

    # ---- body cluster: most chars outside running header/footer ----
    body_chars: dict[tuple, int] = defaultdict(int)
    for ln in lines:
        if not ln.hf:
            body_chars[ln.cluster_key] += ln.chars
    body_key = max(body_chars.items(), key=lambda kv: (kv[1], kv[0][1]))[0] if body_chars else None
    body_id = ids.get(body_key) if body_key else None
    body_size = clusters[body_key].size if body_key else None

    # ---- per page geometry ----
    pages: list[RawPageStats] = []
    body_lines_by_page: dict[int, list[_Line]] = defaultdict(list)
    for ln in lines:
        if (not ln.hf and ln.horizontal and body_size is not None and abs(ln.size - body_size) <= 0.6):
            body_lines_by_page[ln.page].append(ln)
    for p in page_stats_partial:
        bl = body_lines_by_page.get(p["index"], [])
        tb = None
        cols, gutter = 1, None
        if bl:
            tb = BBox(x0=min(ln.x0 for ln in bl), y0=min(ln.y0 for ln in bl),
                      x1=max(ln.x1 for ln in bl), y1=max(ln.y1 for ln in bl))
            cols, gutter = _columns(bl)
        pages.append(RawPageStats(
            index=p["index"], width_pt=round(p["w"], 2), height_pt=round(p["h"], 2), text_bbox=tb,
            char_count=p["chars"], image_area_ratio=round(p["img"], 4), fill_area_ratio=round(p["fill"], 4),
            column_estimate=cols, column_gutter_pt=round(gutter, 2) if gutter else None,
            dominant_fill=p["dom"]))

    all_lines_by_page: dict[int, list[_Line]] = defaultdict(list)
    for ln in lines:
        if not ln.hf and ln.horizontal:
            all_lines_by_page[ln.page].append(ln)
    text_lines: list[RawTextLine] = []
    per_page: dict[int, int] = defaultdict(int)
    for ln in lines:
        if not ln.horizontal or per_page[ln.page] >= MAX_LINES_PER_PAGE:
            continue
        per_page[ln.page] += 1
        text_lines.append(RawTextLine(page_index=ln.page, bbox=BBox(x0=ln.x0, y0=ln.y0, x1=ln.x1, y1=ln.y1),
                                      cluster_id=ids[ln.cluster_key], chars=ln.chars))
    margins = _margins(pages, body_lines_by_page, all_lines_by_page)
    colors = _color_stats(text_chars_by_color, fill_area, stroke_len, image_area)
    pitch, para_gap = _line_metrics(body_lines_by_page)
    return RawDesignStats(
        source_pdf=str(pdf_path), source_sha256=sha, page_count=n_pages, pages=pages,
        font_clusters=font_clusters, colors=colors, rules=rules, repeated_texts=repeated_models,
        margins_estimate=margins, body_cluster_id=body_id,
        body_line_height_pt=pitch, paragraph_spacing_pt=para_gap, filled_boxes=boxes,
        text_lines=text_lines)


def _margins(pages: list[RawPageStats], body_lines: dict[int, list[_Line]],
             all_lines: dict[int, list[_Line]]) -> Margins:
    """Body pages = pages carrying a substantial share of body-size text. Left/right are the
    5th percentiles of line edges on those pages; top is the median first-line y; bottom is the
    furthest-reaching text (running header/footer excluded)."""
    usable = [p for p in pages if body_lines.get(p.index)]
    if not usable:
        p0 = pages[0]
        return Margins(top=round(p0.height_pt * 0.08, 1), right=round(p0.width_pt * 0.1, 1),
                       bottom=round(p0.height_pt * 0.08, 1), left=round(p0.width_pt * 0.1, 1))
    med = statistics.median(sum(ln.chars for ln in body_lines[p.index]) for p in usable)
    body_pages = [p for p in usable if sum(ln.chars for ln in body_lines[p.index]) >= 0.5 * med]
    xs0, xs1, tops, bottoms = [], [], [], []
    for p in body_pages:
        ls = all_lines[p.index]
        xs0 += [ln.x0 for ln in ls]
        xs1 += [p.width_pt - ln.x1 for ln in ls]
        tops.append(min(ln.y0 for ln in ls))
        bottoms.append(p.height_pt - max(ln.y1 for ln in ls))
    return Margins(top=round(statistics.median(tops), 1), right=round(_percentile(xs1, 5), 1),
                   bottom=round(min(bottoms), 1), left=round(_percentile(xs0, 5), 1))


def _line_metrics(body_lines: dict[int, list[_Line]]) -> tuple[float | None, float | None]:
    """Median baseline pitch of consecutive body lines in one column, and the median extra gap where a
    paragraph break (a clearly larger step) occurs. Both None when there is too little text to tell."""
    steps: list[tuple[float, float]] = []  # (dy, size)
    for lines in body_lines.values():
        cols: dict[int, list[_Line]] = defaultdict(list)
        for ln in lines:
            cols[round(ln.x0 / 6)].append(ln)
        for col in cols.values():
            col.sort(key=lambda ln: ln.y0)
            steps += [(b.y0 - a.y0, a.size) for a, b in zip(col, col[1:], strict=False) if abs(a.size - b.size) < 0.6]
    plausible = [(dy, sz) for dy, sz in steps if 0.9 * sz <= dy <= 2.6 * sz]
    if len(plausible) < 5:
        return None, None
    base = statistics.median(dy for dy, sz in plausible if dy <= 1.6 * sz) if any(
        dy <= 1.6 * sz for dy, sz in plausible) else None
    if base is None:
        return None, None
    gaps = [dy - base for dy, sz in plausible if dy > base + 0.25 * sz and dy <= 2.6 * sz]
    pitch = round(base, 2)
    return pitch, (round(statistics.median(gaps), 2) if len(gaps) >= 2 else None)


def _columns(bl: list[_Line]) -> tuple[int, float | None]:
    """x-histogram of body line left edges -> (column count, gutter)."""
    if len(bl) < 4:
        return 1, None
    width = max(ln.x1 for ln in bl) - min(ln.x0 for ln in bl)
    if width <= 0:
        return 1, None
    xs = sorted(ln.x0 for ln in bl)
    groups: list[list[float]] = [[xs[0]]]
    for x in xs[1:]:
        if x - groups[-1][-1] <= 4:
            groups[-1].append(x)
        else:
            groups.append([x])
    min_support = max(3, int(0.15 * len(bl)))
    peaks = [(statistics.median(g), len(g)) for g in groups if len(g) >= min_support]
    accepted: list[float] = []
    for pos, _n in sorted(peaks, key=lambda t: (-t[1], t[0])):
        if all(abs(pos - a) > 0.3 * width for a in accepted):
            accepted.append(pos)
    accepted.sort()
    if len(accepted) < 2:
        return 1, None
    gutters = []
    for i in range(len(accepted) - 1):
        lo, hi = accepted[i] - 4, accepted[i + 1] - 4
        right = [ln.x1 for ln in bl if lo <= ln.x0 < hi and ln.x1 < accepted[i + 1] - 1]
        if right:
            gutters.append(accepted[i + 1] - max(right))
    g = statistics.median(gutters) if gutters else None
    return len(accepted), (g if g and g > 0 else None)


def _find_repeated(lines: list[_Line], pstats: list[dict], clusters: dict, body_key: tuple | None = None
                   ) -> list[dict]:
    n_pages = len(pstats)
    if n_pages < 2:
        return []
    groups: dict[tuple, list[_Line]] = defaultdict(list)
    for ln in lines:
        ph = pstats[ln.page]["h"]
        yc = (ln.y0 + ln.y1) / 2
        band = "top" if yc < ph * HF_BAND else "bottom" if yc > ph * (1 - HF_BAND) else None
        if band is None or not ln.horizontal:
            continue
        groups[(re.sub(r"\d+", "#", ln.text), band)].append(ln)
    out = []
    for (pattern, _band), members in groups.items():
        members.sort(key=lambda ln: (ln.y0 + ln.y1) / 2)
        runs: list[list[_Line]] = [[members[0]]]
        for m in members[1:]:
            if (m.y0 + m.y1) / 2 - (runs[-1][-1].y0 + runs[-1][-1].y1) / 2 <= 3:
                runs[-1].append(m)
            else:
                runs.append([m])
        for run in runs:
            pgs = sorted({m.page for m in run})
            if len(pgs) < 2 or len(pgs) < 0.5 * n_pages:
                continue
            if body_key is not None and run[0].cluster_key == body_key:
                continue  # body text that sits in the band is not a running header/footer
            texts = {m.text for m in run}
            lit = next(iter(texts)) if len(texts) == 1 else pattern
            ycs = [(m.y0 + m.y1) / 2 for m in run]
            xc = statistics.median([((m.x0 + m.x1) / 2) / pstats[m.page]["w"] for m in run])
            align = "left" if xc < 0.33 else "right" if xc > 0.67 else "center"
            for m in run:
                m.hf = True
            out.append({
                "key": run[0].cluster_key,
                "obj": RepeatedText(pattern=lit, y_center_pt=round(statistics.median(ycs), 2), x_align=align,
                                    page_indices=pgs),
            })
    out.sort(key=lambda r: (r["obj"].y_center_pt, r["obj"].x_align, r["obj"].pattern))
    return out


def _image_palette(page: pymupdf.Page, rect: pymupdf.Rect, k: int = 5) -> list[tuple[str, float]]:
    try:
        pix = page.get_pixmap(clip=rect, dpi=16, colorspace=pymupdf.csRGB, alpha=False)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(-1, 3).astype(float)
    except Exception:  # noqa: BLE001
        return []
    if arr.shape[0] == 0:
        return []
    lum = arr @ np.array([0.299, 0.587, 0.114])
    order = np.argsort(lum, kind="stable")
    k = min(k, arr.shape[0])
    centers = arr[order[((np.arange(k) + 0.5) * len(order) / k).astype(int)]]
    for _ in range(8):
        d = ((arr[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2)
        lab = d.argmin(axis=1)
        for j in range(k):
            sel = arr[lab == j]
            if len(sel):
                centers[j] = sel.mean(axis=0)
    counts = np.bincount(lab, minlength=k)
    return [(C.rgb_to_hex(*centers[j]), float(counts[j]) / len(arr)) for j in range(k) if counts[j]]


def _merge(weights: dict[str, float]) -> dict[str, float]:
    merged: dict[str, float] = {}
    for hexc, w in sorted(weights.items(), key=lambda kv: (-kv[1], kv[0])):
        for m in merged:
            if C.delta_e(hexc, m) < MERGE_DE:
                merged[m] += w
                break
        else:
            merged[hexc] = w
    return merged


def _color_stats(text: dict, fill: dict, stroke: dict, image: dict) -> list[ColorStat]:
    groups = {"text": _merge({k: float(v) for k, v in text.items()}),
              "fill": _merge(dict(fill)), "stroke": _merge(dict(stroke)), "image": _merge(dict(image))}
    groups = {u: g for u, g in groups.items() if sum(g.values()) > 0}
    total_imp = sum(_USAGE_IMPORTANCE[u] for u in groups) or 1.0
    out: list[ColorStat] = []
    for usage, g in groups.items():
        tot = sum(g.values())
        for hexc, w in g.items():
            share = w / tot * _USAGE_IMPORTANCE[usage] / total_imp
            out.append(ColorStat(hex=hexc, usage=usage, weight=round(share, 4)))
    out.sort(key=lambda c: (-c.weight, c.hex, c.usage))
    return out


def render_thumbnails(pdf_path: Path, out_dir: Path, page_indices: list[int], dpi: int = 100) -> list[Path]:
    """Render selected pages to PNG (``page-<NN>.png``, NN = 1-based page number);
    returns paths in the order of ``page_indices``."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        doc = pymupdf.open(str(pdf_path))
    except Exception as e:  # noqa: BLE001
        raise InputError(f"cannot open reference PDF {pdf_path}: {e}") from e
    paths: list[Path] = []
    with doc:
        for i in page_indices:
            if not 0 <= i < doc.page_count:
                raise InputError(f"page index {i} out of range (0..{doc.page_count - 1})")
            pix = doc[i].get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            p = out_dir / f"page-{i + 1:02d}.png"
            pix.save(str(p))
            paths.append(p)
    return paths
