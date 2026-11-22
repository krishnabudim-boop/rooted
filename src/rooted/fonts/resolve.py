"""Map reference-PDF font names to embeddable fonts (WP1).

Resolution order for each requested font (see PLAN.md section 7):
1. embedded font extracted from the reference PDF, only if
   BuildConfig.reuse_embedded_fonts AND OS/2 fsType permits AND its cmap
   covers every character in the user's text;
2. exact family installed on the system (or already in the font cache);
3. exact family on Google Fonts (downloaded to the font cache; skipped for
   families that are known to be proprietary, and when downloads are disabled);
4. curated substitution table (rooted/fonts/substitutes.json);
5. vision hints (``similar_open_fonts``);
6. classification-based generic pick (serif/sans/mono/display/script), with a
   last-resort system font when nothing is available offline.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional, Sequence

from platformdirs import user_cache_dir

from rooted.fonts import files as fontfiles
from rooted.fonts import system_index as sysidx
from rooted.fonts.google import cache_subdir, fetch_google_family
from rooted.models import BuildConfig, DesignSpec, FontFace, FontResolution, TypeRole

# fetcher(family, weights, italic, dest_dir) -> list of downloaded file paths.
# Injected so tests never hit the network.
FontFetcher = Callable[[str, Sequence[int], bool, Path], list[Path]]

_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
_JUNK = {"mt", "ps", "psmt", "std", "pro", "lt", "ltstd", "psstd"}
_STYLE_ATOMS = {
    "thin", "hairline", "extra", "ultra", "semi", "demi", "light", "book", "regular", "normal",
    "roman", "medium", "bold", "black", "heavy", "italic", "oblique", "it",
}
_ITALIC_WORDS = {"italic", "oblique", "it"}
_WEIGHTS = {
    "thin": 100, "hairline": 100, "extralight": 200, "ultralight": 200, "light": 300,
    "book": 400, "regular": 400, "normal": 400, "roman": 400, "medium": 500,
    "semibold": 600, "demibold": 600, "demi": 600, "semi": 600, "bold": 700,
    "extrabold": 800, "ultrabold": 800, "heavy": 900, "black": 900, "extrablack": 900,
    "ultrablack": 900, "ultra": 900,
}
_STYLE_ATOMS |= set(_WEIGHTS)  # fused spellings such as "Semibold"


def _tokens(name: str) -> list[str]:
    name = _SUBSET_RE.sub("", name.strip())
    name = re.sub(r"[-_,]+", " ", name)
    name = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)  # camelCase
    name = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", name)  # IBMPlex -> IBM Plex
    name = re.sub(r"(?<=[A-Za-z])(?=\d)", " ", name)  # Sans3 -> Sans 3
    return name.split()


def normalize_font_name(pdf_font_name: str) -> tuple[str, int, bool]:
    """``"ABCDEF+Montserrat-SemiBoldItalic"`` -> ``("Montserrat", 600, True)``.

    Strips subset prefixes, PostScript suffixes (MT, PS, Std, Pro, LT), and
    splits style words into weight/italic.
    """
    toks = _tokens(pdf_font_name)
    if not toks:
        return (pdf_font_name.strip(), 400, False)
    toks = toks[:1] + [t for t in toks[1:] if t.lower() not in _JUNK]
    style: list[str] = []
    while len(toks) > 1 and toks[-1].lower() in _STYLE_ATOMS:
        if toks[-1].lower() == "roman" and toks[-2].lower() == "new":
            break
        style.insert(0, toks.pop())
    italic = any(t.lower() in _ITALIC_WORDS for t in style)
    word = "".join(t.lower() for t in style if t.lower() not in _ITALIC_WORDS)
    weight = _WEIGHTS.get(word, 400)
    return (" ".join(toks), weight, italic)


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _table() -> dict:
    return json.loads((Path(__file__).parent / "substitutes.json").read_text("utf-8"))


def _lookup(section: str, family: str) -> list[str]:
    key = sysidx.family_key(family)
    for name, values in _table()[section].items():
        if sysidx.family_key(name) == key:
            return list(values)
    return []


def guess_classification(family: str) -> str:
    low = family.lower()
    for cls in ("mono", "script", "slab", "display", "serif"):
        if any(k in low for k in _table()["classification_keywords"][cls]):
            return cls
    return "sans"


def _css_generic(classification: str) -> str:
    return _table()["css_generic"].get(classification, "sans-serif")


# --------------------------------------------------------------------------
# Resolution helpers
# --------------------------------------------------------------------------


def _cache_dir(config: BuildConfig) -> Path:
    return Path(config.font_cache_dir) if config.font_cache_dir else Path(user_cache_dir("rooted")) / "fonts"


def _find(
    name: str,
    weights: Sequence[int],
    italic: bool,
    config: BuildConfig,
    fetcher: Optional[FontFetcher],
    notes: list[str],
    *,
    download: bool = True,
) -> Optional[tuple[str, list[FontFace], str]]:
    """Locate ``name`` on the system, in the download cache, or on Google Fonts.

    Returns ``(family, faces, source)`` where source is ``system`` or ``google``.
    """
    cache = _cache_dir(config)
    try:
        faces = sysidx.select_faces(sysidx.system_index(cache).find(name), weights, italic)
        if faces:
            return faces[0].family, faces, "system"
    except Exception as e:  # noqa: BLE001
        notes.append(f"system font lookup failed: {e}")
    try:
        faces = sysidx.select_faces(sysidx.directory_index(cache).find(name), weights, italic)
        if faces:
            return faces[0].family, faces, "google"
    except Exception as e:  # noqa: BLE001
        notes.append(f"font cache lookup failed: {e}")
    if download and config.allow_font_download:
        fetch = fetcher or fetch_google_family
        try:
            paths = fetch(name, list(weights), italic, cache_subdir(cache, name))
        except Exception as e:  # noqa: BLE001
            notes.append(f"download of {name} failed: {type(e).__name__}")
            return None
        if paths:
            index = sysidx.FontIndex.build([], extra_files=paths)
            entries = index.find(name) or index.entries
            faces = sysidx.select_faces(entries, weights, italic)
            if faces:
                return faces[0].family, faces, "google"
    return None


def _try_embedded(
    family: str, weight: int, italic: bool, required_chars: str, config: BuildConfig,
    reference_pdf: Path, notes: list[str],
) -> Optional[FontResolution]:
    try:
        import pymupdf as fitz
    except Exception:  # noqa: BLE001
        return None
    key = sysidx.family_key(family)
    try:
        doc = fitz.open(str(reference_pdf))
    except Exception as e:  # noqa: BLE001
        notes.append(f"could not open reference PDF for embedded fonts: {type(e).__name__}")
        return None
    try:
        seen: set[int] = set()
        for page in doc:
            for item in page.get_fonts(full=True):
                xref, ext, _type, basefont = item[0], item[1], item[2], item[3]
                if xref in seen or ext not in ("ttf", "otf"):
                    continue
                seen.add(xref)
                fam, w, it = normalize_font_name(basefont)
                if sysidx.family_key(fam) != key or it != italic or w != weight:
                    continue
                try:
                    _name, _ext, _t, buf = doc.extract_font(xref)
                except Exception:  # noqa: BLE001
                    continue
                if not buf:
                    continue
                out_dir = Path(config.work_dir) / "fonts"
                out_dir.mkdir(parents=True, exist_ok=True)
                dest = out_dir / f"embedded_{re.sub(r'[^A-Za-z0-9]+', '_', basefont)}.{ext}"
                dest.write_bytes(buf)
                if not fontfiles.embedding_allowed(fontfiles.fs_type(dest)):
                    notes.append(f"embedded {basefont}: license flags (fsType) forbid reuse")
                    dest.unlink(missing_ok=True)
                    continue
                if not fontfiles.cmap_covers(dest, required_chars):
                    notes.append(f"embedded {basefont}: subset does not cover the document text")
                    dest.unlink(missing_ok=True)
                    continue
                return FontResolution(
                    requested=family, resolved_family=family, source="embedded", match="exact",
                    faces=[FontFace(family=family, path=str(dest), weight=weight, italic=italic)],
                    note="reused font embedded in the reference PDF",
                )
    finally:
        doc.close()
    return None


def resolve_font(
    requested: str,
    *,
    weights: Sequence[int],
    italic: bool,
    classification: Optional[str],
    hints: Sequence[str],
    required_chars: str,
    config: BuildConfig,
    reference_pdf: Optional[Path] = None,
    fetcher: Optional[FontFetcher] = None,
) -> FontResolution:
    """Resolve one family. Never raises for "not found": falls through to a
    generic pick and records ``match="generic"`` with a note."""
    family, name_weight, name_italic = normalize_font_name(requested)
    wts = sorted({int(w) for w in weights}) or [name_weight]
    want_italic = bool(italic or name_italic)
    cls = classification or guess_classification(family)
    notes: list[str] = []

    def done(resolved: str, faces: list[FontFace], source: str, match: str, note: str = "") -> FontResolution:
        if faces:
            gaps = fontfiles.missing_chars(Path(faces[0].path), required_chars)
            if gaps:
                notes.append(f"{len(gaps)} characters of the document are missing from {resolved}")
        text = "; ".join([n for n in [note, *notes] if n])
        return FontResolution(requested=family, resolved_family=resolved, source=source, match=match,
                              faces=faces, note=text)

    # 1. embedded
    if config.reuse_embedded_fonts and reference_pdf is not None:
        try:
            emb = _try_embedded(family, name_weight, name_italic, required_chars, config, Path(reference_pdf), notes)
        except Exception as e:  # noqa: BLE001
            notes.append(f"embedded font reuse failed: {type(e).__name__}")
            emb = None
        if emb is not None:
            return emb

    proprietary = bool(_lookup("metric_compatible", family) or _lookup("similar", family))

    # 2 + 3. exact family on the system / in cache / on Google Fonts
    hit = _find(family, wts, want_italic, config, fetcher, notes, download=not proprietary)
    if hit:
        return done(hit[0], hit[1], hit[2], "exact")

    # 4. curated substitutes
    for section, match in (("metric_compatible", "metric_compatible"), ("similar", "similar")):
        for cand in _lookup(section, family):
            hit = _find(cand, wts, want_italic, config, fetcher, notes)
            if hit:
                return done(hit[0], hit[1], hit[2], match, f"{family} is not available; substituted {hit[0]}")

    # 5. vision hints
    for cand in hints:
        hit = _find(cand, wts, want_italic, config, fetcher, notes)
        if hit:
            return done(hit[0], hit[1], hit[2], "similar", f"{family} is not available; vision suggested {hit[0]}")

    # 6. generic by classification
    for cand in _table()["generic"].get(cls, _table()["generic"]["sans"]):
        hit = _find(cand, wts, want_italic, config, fetcher, notes)
        if hit:
            return done(hit[0], hit[1], hit[2], "generic", f"{family} is not available; generic {cls} pick {hit[0]}")

    # last resort: any system font in the right class
    cache = _cache_dir(config)
    try:
        idx = sysidx.system_index(cache)
        for cand in _table()["system_last_resort"].get(cls, []):
            faces = sysidx.select_faces(idx.find(cand), wts, want_italic)
            if faces:
                return done(faces[0].family, faces, "generic", "generic",
                            f"{family} is not available and nothing was downloaded; using system {faces[0].family}")
    except Exception as e:  # noqa: BLE001
        notes.append(f"system fallback failed: {e}")
    return done(_css_generic(cls), [], "generic", "generic",
                f"{family} is not available and no usable font file was found; falling back to CSS {_css_generic(cls)}")


def _stage_face(face: FontFace, fonts_dir: Path) -> FontFace:
    """Copy a font file into ``<work_dir>/fonts`` and return a work-dir-relative face.

    A .ttc member is extracted to its own ``<stem>-<n>.ttf`` because browsers
    always load face 0 of a collection."""
    src = Path(face.path)
    if src.suffix.lower() == ".ttc":
        dest = fonts_dir / f"{src.stem}-{face.number}.ttf"
        if not dest.exists():
            from fontTools.ttLib import TTCollection

            tmp = dest.with_suffix(".tmp")
            coll = TTCollection(str(src))
            try:
                coll.fonts[face.number].save(str(tmp))
            finally:
                coll.close()
            tmp.replace(dest)
        return face.model_copy(update={"path": f"fonts/{dest.name}", "number": 0})
    if src.parent.resolve() == fonts_dir.resolve():
        return face.model_copy(update={"path": f"fonts/{src.name}"})
    dest = fonts_dir / src.name
    if dest.exists() and dest.stat().st_size != src.stat().st_size:
        dest = fonts_dir / f"{hashlib.sha256(str(src).encode()).hexdigest()[:8]}_{src.name}"
    if not dest.exists():
        shutil.copyfile(src, dest)  # copyfile: copy2 would copy macOS "restricted" flags (EPERM)
    return face.model_copy(update={"path": f"fonts/{dest.name}"})


def resolve_design_fonts(
    spec: DesignSpec,
    *,
    required_chars: str,
    config: BuildConfig,
    hints: Optional[dict[str, list[str]]] = None,
    fetcher: Optional[FontFetcher] = None,
) -> DesignSpec:
    """Return a copy of ``spec`` with ``fonts`` filled and every
    ``TypeStyle.family``/``fallback_stack`` set to resolved CSS families.
    ``hints`` maps source font name -> similar open fonts (from vision)."""
    hint_map = {sysidx.family_key(normalize_font_name(k)[0]): v for k, v in (hints or {}).items()}
    groups: dict[str, dict] = {}
    for st in spec.typography:
        requested = st.family or st.source_font or ""
        if not requested:
            continue
        fam = normalize_font_name(requested)[0]
        g = groups.setdefault(sysidx.family_key(fam), {"family": fam, "requested": requested, "weights": set(),
                                                       "italic": False, "sources": set()})
        g["weights"].add(int(st.weight))
        g["italic"] = g["italic"] or st.italic
        if st.source_font:
            g["sources"].add(st.source_font)
        if st.role == TypeRole.BODY:  # inline <b>/<i> in body text
            g["weights"].update({400, 700})
            g["italic"] = True

    fonts_dir = Path(config.work_dir) / "fonts"
    fonts_dir.mkdir(parents=True, exist_ok=True)
    resolutions: dict[str, FontResolution] = {}
    warnings = list(spec.warnings)
    for key, g in groups.items():
        hint_list: list[str] = list(hint_map.get(key, []))
        for src in g["sources"]:
            hint_list += hint_map.get(sysidx.family_key(normalize_font_name(src)[0]), [])
        res = resolve_font(
            g["requested"], weights=sorted(g["weights"]), italic=g["italic"], classification=None,
            hints=hint_list, required_chars=required_chars, config=config,
            reference_pdf=Path(spec.source_pdf) if spec.source_pdf else None, fetcher=fetcher,
        )
        res = res.model_copy(update={"faces": [_stage_face(f, fonts_dir) for f in res.faces]})
        resolutions[key] = res
        if res.match != "exact":
            warnings.append(f"Font '{g['family']}' -> '{res.resolved_family}' ({res.match}). {res.note}".strip())

    new_styles = []
    for st in spec.typography:
        requested = st.family or st.source_font or ""
        if not requested:
            new_styles.append(st)
            continue
        fam = normalize_font_name(requested)[0]
        res = resolutions[sysidx.family_key(fam)]
        stack: list[str] = []
        if res.match != "exact" and sysidx.family_key(fam) != sysidx.family_key(res.resolved_family):
            stack.append(fam)
        stack += [s for s in st.fallback_stack if s != res.resolved_family]
        generic = _css_generic(guess_classification(fam))
        if generic not in stack and res.resolved_family != generic:
            stack.append(generic)
        seen: set[str] = set()
        stack = [s for s in stack if not (s in seen or seen.add(s))]
        new_styles.append(st.model_copy(update={"family": res.resolved_family, "fallback_stack": stack}))

    return spec.model_copy(update={"typography": new_styles, "fonts": list(resolutions.values()),
                                   "warnings": warnings}, deep=True)
