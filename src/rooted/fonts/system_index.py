"""Cached index of installed font files (WP1).

``FontIndex`` maps a family name (compared ignoring case, spaces and
punctuation) to the faces found under a set of directories. Scans are cached
on disk (keyed by path, mtime and size) so repeat builds do not re-read every
font on the machine.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, Optional, Sequence

from rooted.fonts.files import FONT_EXTS, FontEntry, read_entries
from rooted.models import FontFace

ENV_DIRS = "ROOTED_SYSTEM_FONT_DIRS"  # os.pathsep list; replaces the defaults (used by tests)

_DEFAULT_DIRS = [
    "~/Library/Fonts",
    "/Library/Fonts",
    "/System/Library/Fonts",
    "/System/Library/Fonts/Supplemental",
    "/usr/share/fonts",
    "/usr/local/share/fonts",
    "~/.fonts",
    "~/.local/share/fonts",
    "C:/Windows/Fonts",
]

_memo: dict[tuple, FontIndex] = {}


def family_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def system_font_dirs() -> list[Path]:
    env = os.environ.get(ENV_DIRS)
    if env is not None:
        return [Path(p) for p in env.split(os.pathsep) if p]
    return [Path(p).expanduser() for p in _DEFAULT_DIRS if Path(p).expanduser().is_dir()]


def _fc_list_files() -> list[Path]:
    exe = shutil.which("fc-list")
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--format", "%{file}\n"], capture_output=True, text=True, timeout=15).stdout
    except Exception:  # noqa: BLE001
        return []
    return [Path(line) for line in out.splitlines() if line.strip()]


def iter_font_files(roots: Iterable[Path]) -> Iterable[Path]:
    seen: set[str] = set()
    for root in roots:
        root = Path(root)
        if not root.is_dir():
            continue
        for dirpath, _dirs, files in os.walk(root, followlinks=True):
            for f in files:
                p = Path(dirpath) / f
                if p.suffix.lower() in FONT_EXTS and str(p) not in seen:
                    seen.add(str(p))
                    yield p


class FontIndex:
    def __init__(self, entries: Sequence[FontEntry]):
        self.entries = list(entries)
        self._by_key: dict[str, list[FontEntry]] = {}
        for e in self.entries:
            for k in {family_key(e.family), family_key(e.legacy_family)}:
                self._by_key.setdefault(k, []).append(e)

    # -- construction ---------------------------------------------------------
    @classmethod
    def build(cls, roots: Sequence[Path], extra_files: Sequence[Path] = (), cache_file: Optional[Path] = None):
        cached: dict[str, dict] = {}
        if cache_file and Path(cache_file).is_file():
            try:
                cached = json.loads(Path(cache_file).read_text("utf-8"))
            except (OSError, ValueError):
                cached = {}
        fresh: dict[str, dict] = {}
        entries: list[FontEntry] = []
        files = list(iter_font_files(roots)) + [Path(p) for p in extra_files if Path(p).is_file()]
        for p in files:
            try:
                st = p.stat()
            except OSError:
                continue
            sig = f"{st.st_mtime_ns}:{st.st_size}"
            hit = cached.get(str(p))
            if hit and hit.get("sig") == sig:
                faces = [FontEntry(**d) for d in hit["entries"]]
            else:
                faces = read_entries(p)
            fresh[str(p)] = {"sig": sig, "entries": [f.__dict__ for f in faces]}
            entries.extend(faces)
        if cache_file:
            try:
                Path(cache_file).parent.mkdir(parents=True, exist_ok=True)
                Path(cache_file).write_text(json.dumps(fresh), "utf-8")
            except OSError:
                pass
        return cls(entries)

    # -- queries --------------------------------------------------------------
    def find(self, family: str) -> list[FontEntry]:
        return list(self._by_key.get(family_key(family), []))

    def families(self) -> list[str]:
        return sorted({e.family for e in self.entries})


def system_index(cache_dir: Optional[Path] = None) -> FontIndex:
    """Index of installed fonts (memoized per process and root set)."""
    roots = system_font_dirs()
    extra = _fc_list_files() if ENV_DIRS not in os.environ else []
    key = (tuple(map(str, roots)), len(extra), str(cache_dir))
    if key not in _memo:
        cache_file = Path(cache_dir) / "system_index.json" if cache_dir else None
        _memo[key] = FontIndex.build(roots, extra, cache_file)
    return _memo[key]


def directory_index(directory: Path) -> FontIndex:
    """Uncached, unmemoized index of one directory (the download cache changes during a build)."""
    return FontIndex.build([directory])


def select_faces(entries: Sequence[FontEntry], weights: Sequence[int], italic: bool) -> list[FontFace]:
    """Pick one face per requested (weight, italic) pair, nearest weight wins.

    Variable fonts satisfy any weight in their range. Upright faces are always
    returned; italic faces only when the family has some. Static non-.ttc files
    are preferred over collections (browsers cannot address a .ttc member).
    """
    if not entries:
        return []
    family = sorted(entries, key=lambda e: (e.path.lower().endswith(".ttc"), e.path))[0].family
    out: list[FontFace] = []
    seen: set[tuple[str, int, int, bool]] = set()
    wanted = [(int(w), False) for w in weights] + ([(int(w), True) for w in weights] if italic else [])
    for w, it in wanted:
        pool = [e for e in entries if e.italic == it]
        if not pool:
            continue
        var = [e for e in pool if e.covers(w)]
        if var:
            best = sorted(var, key=lambda e: (e.path, e.number))[0]
            face_w = w
        else:
            best = sorted(pool, key=lambda e: (abs(e.weight - w), e.path.lower().endswith(".ttc"), e.path, e.number))[0]
            face_w = best.weight if not best.variable else min(max(w, best.wmin), best.wmax)
        k = (best.path, best.number, face_w, it)
        if k in seen:
            continue
        seen.add(k)
        out.append(FontFace(family=family, path=best.path, weight=face_w, italic=it, number=best.number))
    return out
