"""HTML -> PDF backends (WP5).

Chromium (Playwright, sync API) is the default. WeasyPrint is an optional
extra behind the same protocol. Both build the page map the same way: the HTML
carries ``data-unit`` / ``data-el`` markers plus an invisible anchor list, the
PDF therefore contains named destinations, and PyMuPDF resolves each
destination to a page.
"""

from __future__ import annotations

import atexit
import functools
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional, Protocol

from rooted.errors import RenderError
from rooted.models import PageMapEntry, RenderResult

CHROMIUM_HINT = "Install the browser with: pip install playwright && playwright install chromium"
WEASYPRINT_HINT = (
    "WeasyPrint is an optional extra: pip install 'rooted[weasyprint]' "
    "(on macOS it also needs Pango: brew install pango). Use --renderer chromium instead."
)


class Renderer(Protocol):
    name: str

    def render(self, html: str, *, base_url: Path, pdf_path: Path, html_path: Path) -> RenderResult:
        """Write the PDF and return page count + page map (element ids per
        page, from ``data-el`` attributes) + overflow warnings."""
        ...

    def close(self) -> None: ...


# --------------------------------------------------------------------------
# page map (shared by all backends)
# --------------------------------------------------------------------------


class _MarkerParser(HTMLParser):
    """Collects units and their elements, in document order, from data-* markers."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.units: list[tuple[str, list[str]]] = []
        self.fixed: set[str] = set()

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "data-unit" in a and a.get("id"):
            self.units.append((a["id"], []))
            if "fixed" in (a.get("class") or "").split():
                self.fixed.add(a["id"])
        elif "data-el" in a and a.get("id") and self.units:
            self.units[-1][1].append(a["id"])


def parse_markers(html: str) -> list[tuple[str, list[str]]]:
    p = _MarkerParser()
    p.feed(html)
    return p.units


def _fixed_units(html: str) -> set[str]:
    p = _MarkerParser()
    p.feed(html)
    return p.fixed


def build_page_map(pdf_path: Path, html: str, overflow: list[str]) -> RenderResult:
    """Resolve named destinations in the PDF, strip the invisible anchor links
    and return the RenderResult skeleton (without html_path)."""
    import pymupdf

    units = parse_markers(html)
    fixed = _fixed_units(html)
    warnings: list[str] = []
    doc = pymupdf.open(str(pdf_path))
    try:
        names = doc.resolve_names()
        n = doc.page_count
        # remove the zero-size anchor links used only to materialise destinations
        for page in doc:
            for link in page.get_links():
                if link.get("kind") in (pymupdf.LINK_GOTO, pymupdf.LINK_NAMED):
                    page.delete_link(link)
        data = doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
    pdf_path.write_bytes(data)

    def where(ident: str) -> Optional[int]:
        d = names.get(ident)
        if not d:
            return None
        return max(0, min(n - 1, int(d["page"])))

    starts: list[int] = []
    prev = 0
    for uid, _ in units:
        p = where(uid)
        if p is None:
            warnings.append(f"page map: no destination for unit {uid}")
            p = prev
        starts.append(max(p, prev))
        prev = starts[-1]
    page_unit: dict[int, int] = {}
    ranges: list[tuple[int, int]] = []
    for i, (uid, _) in enumerate(units):
        last = (starts[i + 1] - 1) if i + 1 < len(units) else n - 1
        last = max(last, starts[i])
        ranges.append((starts[i], last))
        for pg in range(starts[i], last + 1):
            page_unit[pg] = i
        if last > starts[i] and uid in fixed:
            warnings.append(f"fixed unit {uid} spans {last - starts[i] + 1} pages")
            overflow.append(units[i][1][0] if units[i][1] else uid)

    per_page: dict[int, list[str]] = {p: [] for p in range(n)}
    for i, (_, els) in enumerate(units):
        lo, hi = ranges[i]
        for eid in els:
            s = where(eid)
            if s is None:
                continue
            t = where(eid + "-end")
            t = s if t is None else max(s, t)
            for pg in range(max(s, lo), min(t, hi) + 1):
                per_page[pg].append(eid)

    page_map: list[PageMapEntry] = []
    for pg in range(n):
        if pg in page_unit:
            uid = units[page_unit[pg]][0]
        else:
            uid = ""
            warnings.append(f"page {pg + 1} belongs to no layout unit")
        page_map.append(PageMapEntry(page_index=pg, unit_id=uid, element_ids=per_page[pg]))
    return RenderResult(
        pdf_path=str(pdf_path),
        html_path="",
        page_count=n,
        page_map=page_map,
        overflow_element_ids=sorted(set(overflow)),
        warnings=warnings,
    )


# --------------------------------------------------------------------------
# Chromium
# --------------------------------------------------------------------------

_FIT_JS = """
() => {
  const over = [];
  const shrunk = [];
  for (const el of document.querySelectorAll('[data-fit]')) {
    let n = 0;
    const bad = () => el.scrollHeight > el.clientHeight + 1 || el.scrollWidth > el.clientWidth + 1;
    while (bad() && n < 16) {
      const fs = parseFloat(getComputedStyle(el).fontSize);
      el.style.fontSize = (fs * 0.94) + 'px';
      n++;
    }
    if (n) shrunk.push(el.dataset.region + ' x' + (Math.pow(0.94, n)).toFixed(2));
    if (bad()) over.push(el.dataset.owner || el.dataset.region);
  }
  return { over, shrunk };
}
"""


@functools.lru_cache(maxsize=1)
def chromium_available() -> bool:
    """True if Playwright and a Chromium build are installed."""
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            return Path(p.chromium.executable_path).exists()
    except Exception:
        return False


class ChromiumRenderer:
    """Playwright headless Chromium. One browser is reused across renders
    (fitting iterations); use as a context manager or call ``close()``."""

    name = "chromium"

    def __init__(self) -> None:
        self._pw = None
        self._browser = None

    # -- lifecycle
    def start(self) -> ChromiumRenderer:
        if self._browser is not None:
            return self
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover
            raise RenderError(f"Playwright is not installed. {CHROMIUM_HINT}") from e
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=True)
        except Exception as e:
            self.close()
            raise RenderError(f"Could not launch Chromium ({e}). {CHROMIUM_HINT}") from e
        atexit.register(self.close)
        return self

    def close(self) -> None:
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:
            pass
        try:
            if self._pw is not None:
                self._pw.stop()
        except Exception:
            pass
        self._browser = None
        self._pw = None

    def __enter__(self) -> ChromiumRenderer:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()

    # -- render
    def render(self, html: str, *, base_url: Path, pdf_path: Path, html_path: Path) -> RenderResult:
        self.start()
        html_path = Path(html_path)
        pdf_path = Path(pdf_path)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        pdf_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(html, "utf-8")
        warnings: list[str] = []
        overflow: list[str] = []
        page = self._browser.new_page()
        try:
            page.emulate_media(media="print")
            page.goto(html_path.resolve().as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready.then(() => true)")
            res = page.evaluate(_FIT_JS)
            overflow.extend(res["over"])
            for s in res["shrunk"]:
                warnings.append(f"fixed-region text shrunk to fit: {s}")
            for o in res["over"]:
                warnings.append(f"fixed region overflows even after shrinking: {o}")
            data = page.pdf(
                prefer_css_page_size=True,
                print_background=True,
                margin={"top": "0", "right": "0", "bottom": "0", "left": "0"},
            )
            html_path.write_text(page.content(), "utf-8")
        except RenderError:
            raise
        except Exception as e:
            raise RenderError(f"Chromium failed to render: {e}") from e
        finally:
            page.close()
        pdf_path.write_bytes(data)
        result = build_page_map(pdf_path, html, overflow)
        result.html_path = str(html_path)
        result.warnings = warnings + result.warnings
        return result


class WeasyPrintRenderer:
    """Optional backend. Not the default; imports WeasyPrint lazily."""

    name = "weasyprint"

    def __init__(self) -> None:
        try:
            import weasyprint  # noqa: F401
        except Exception as e:
            raise RenderError(f"WeasyPrint is unavailable ({e}). {WEASYPRINT_HINT}") from e

    def close(self) -> None:
        return None

    def __enter__(self) -> WeasyPrintRenderer:
        return self

    def __exit__(self, *exc) -> None:
        return None

    def render(self, html: str, *, base_url: Path, pdf_path: Path, html_path: Path) -> RenderResult:
        from weasyprint import HTML  # type: ignore

        Path(html_path).write_text(html, "utf-8")
        try:
            HTML(string=html, base_url=str(base_url)).write_pdf(str(pdf_path))
        except Exception as e:
            raise RenderError(f"WeasyPrint failed to render: {e}") from e
        result = build_page_map(Path(pdf_path), html, [])
        result.html_path = str(html_path)
        return result


def get_renderer(name: str = "chromium") -> Renderer:
    """``"chromium"`` (default, Playwright) or ``"weasyprint"`` (optional
    extra). Raises RenderError with install instructions if the backend cannot
    load. The returned object is a context manager; reuse one instance across
    fitting iterations and ``close()`` it afterwards."""
    key = (name or "chromium").lower()
    if key == "chromium":
        try:
            import playwright  # noqa: F401
        except ImportError as e:
            raise RenderError(f"Playwright is not installed. {CHROMIUM_HINT}") from e
        return ChromiumRenderer()
    if key == "weasyprint":
        return WeasyPrintRenderer()
    raise RenderError(f"Unknown renderer {name!r}; choose 'chromium' or 'weasyprint'.")
