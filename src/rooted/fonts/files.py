"""Low-level font file inspection with fontTools (WP1)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fontTools.ttLib import TTCollection, TTFont, TTLibError

FONT_EXTS = {".ttf", ".otf", ".ttc", ".woff", ".woff2"}


@dataclass(frozen=True)
class FontEntry:
    """One face in a font file."""

    family: str  # typographic family (name ID 16, else ID 1)
    legacy_family: str  # name ID 1
    subfamily: str
    weight: int  # OS/2 usWeightClass
    italic: bool
    path: str
    number: int = 0  # face index inside a .ttc
    variable: bool = False  # has a wght axis
    wmin: int = 0
    wmax: int = 0

    def covers(self, weight: int) -> bool:
        return self.variable and self.wmin <= weight <= self.wmax


def _name(font: TTFont, *ids: int) -> str:
    table = font["name"]
    for nid in ids:
        rec = table.getName(nid, 3, 1, 0x409) or table.getName(nid, 1, 0, 0) or table.getName(nid, 3, 1)
        if rec is not None:
            try:
                text = rec.toUnicode().strip()
            except Exception:
                continue
            if text:
                return text
    return ""


def _entry(font: TTFont, path: Path, number: int) -> Optional[FontEntry]:
    if "name" not in font:
        return None
    family = _name(font, 16, 1)
    if not family:
        return None
    legacy = _name(font, 1) or family
    sub = _name(font, 17, 2) or "Regular"
    weight = 400
    italic = False
    if "OS/2" in font:
        os2 = font["OS/2"]
        weight = int(getattr(os2, "usWeightClass", 400) or 400)
        italic = bool(os2.fsSelection & 1)
    if "head" in font and font["head"].macStyle & 2:
        italic = True
    variable, wmin, wmax = False, 0, 0
    if "fvar" in font:
        for axis in font["fvar"].axes:
            if axis.axisTag == "wght":
                variable, wmin, wmax = True, int(axis.minValue), int(axis.maxValue)
    return FontEntry(family, legacy, sub, weight, italic, str(path), number, variable, wmin, wmax)


def read_entries(path: Path) -> list[FontEntry]:
    """All faces of a font file; empty list when unreadable."""
    path = Path(path)
    try:
        if path.suffix.lower() == ".ttc":
            coll = TTCollection(str(path), lazy=True)
            out = [_entry(f, path, i) for i, f in enumerate(coll.fonts)]
            return [e for e in out if e]
        font = TTFont(str(path), lazy=True)
        try:
            entry = _entry(font, path, 0)
        finally:
            font.close()
        return [entry] if entry else []
    except (TTLibError, OSError, Exception):  # noqa: BLE001 - corrupt fonts must never break a build
        return []


def _open(path: Path, number: int = 0) -> Optional[TTFont]:
    try:
        return TTFont(str(path), fontNumber=number, lazy=True)
    except Exception:  # noqa: BLE001
        return None


def fs_type(path: Path) -> Optional[int]:
    font = _open(Path(path))
    if font is None:
        return None
    try:
        return int(font["OS/2"].fsType) if "OS/2" in font else 0
    finally:
        font.close()


def embedding_allowed(fs: Optional[int]) -> bool:
    """True when OS/2 fsType permits installable (0) or editable (bit 3) embedding.

    Restricted (bit 1), preview/print-only (bit 2) and bitmap-only (bit 8) are refused.
    """
    if fs is None:
        return False
    if fs & 0x2 or fs & 0x200:
        return False
    return fs == 0 or bool(fs & 0x8)


def cmap_covers(path: Path, chars: str) -> bool:
    """True when the font's cmap has a glyph for every printable char in ``chars``."""
    return not missing_chars(path, chars)


def missing_chars(path: Path, chars: str) -> set[str]:
    wanted = {c for c in chars if c.isprintable() and not c.isspace()}
    if not wanted:
        return set()
    font = _open(Path(path))
    if font is None:
        return wanted
    try:
        cmap = font.getBestCmap() or {}
    except Exception:  # noqa: BLE001
        return wanted
    finally:
        font.close()
    return {c for c in wanted if ord(c) not in cmap}
