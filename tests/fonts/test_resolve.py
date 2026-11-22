from __future__ import annotations

import json
from pathlib import Path

import pymupdf as fitz
import pytest

from rooted.fonts import files as fontfiles
from rooted.fonts import resolve_design_fonts, resolve_font, system_index
from rooted.fonts.google import choose_files, fetch_google_family
from rooted.fonts.resolve import _lookup, guess_classification
from tests import factories
from tests.fonts.conftest import MPL_TTF, make_renamed_font


def _resolve(name, cfg, **kw):
    args = dict(weights=[400], italic=False, classification=None, hints=[], required_chars="Hello", config=cfg)
    args.update(kw)
    return resolve_font(name, **args)


# -- substitution table ------------------------------------------------------


@pytest.mark.parametrize("name,first", [
    ("Arial", "Arimo"), ("Helvetica", "Arimo"), ("Times", "Tinos"), ("Courier", "Cousine"),
    ("Calibri", "Carlito"), ("Cambria", "Caladea"), ("Georgia", "Gelasio"),
])
def test_metric_compatible_table(name, first):
    assert _lookup("metric_compatible", name)[0] == first


@pytest.mark.parametrize("name,first", [
    ("Helvetica Neue", "Inter"), ("Gotham", "Montserrat"), ("Proxima Nova", "Figtree"),
    ("Avenir", "Nunito Sans"), ("Futura", "Jost"), ("Frutiger", "Hind"), ("Myriad", "Source Sans 3"),
    ("Minion", "Crimson Pro"), ("Garamond", "EB Garamond"), ("Didot", "Playfair Display"),
    ("Bodoni", "Bodoni Moda"), ("Franklin Gothic", "Libre Franklin"), ("Univers", "Roboto"), ("DIN", "Barlow"),
])
def test_similar_table(name, first):
    assert _lookup("similar", name)[0] == first


def test_classification_guess():
    assert guess_classification("Courier New") == "mono"
    assert guess_classification("Garamond") == "serif"
    assert guess_classification("Gotham") == "sans"


# -- system index ------------------------------------------------------------


def test_system_index_finds_faces(sysfonts, cfg):
    idx = system_index.system_index(cfg.font_cache_dir)
    assert "DejaVu Sans" in idx.families()
    faces = system_index.select_faces(idx.find("dejavu  sans"), [400, 700], True)
    assert {(f.weight, f.italic) for f in faces} == {(400, False), (700, False), (400, True), (700, True)}
    assert all(Path(f.path).is_file() for f in faces)


def test_system_index_disk_cache(sysfonts, tmp_path):
    cache = tmp_path / "idx" / "system_index.json"
    a = system_index.FontIndex.build([sysfonts], cache_file=cache)
    assert cache.is_file() and json.loads(cache.read_text())
    b = system_index.FontIndex.build([sysfonts], cache_file=cache)
    assert a.entries == b.entries


def test_nearest_weight(sysfonts, cfg):
    idx = system_index.system_index(cfg.font_cache_dir)
    faces = system_index.select_faces(idx.find("DejaVu Sans"), [600], False)
    assert [(f.weight) for f in faces] == [700]


# -- resolve_font ------------------------------------------------------------


def test_exact_system(sysfonts, cfg):
    res = _resolve("DejaVuSans-Bold", cfg, weights=[400, 700])
    assert (res.match, res.source, res.resolved_family) == ("exact", "system", "DejaVu Sans")
    assert {f.weight for f in res.faces} == {400, 700}


def test_metric_compatible(sysfonts, add_system_font, cfg):
    add_system_font("Arimo")
    res = _resolve("ArialMT", cfg)
    assert (res.match, res.resolved_family, res.source) == ("metric_compatible", "Arimo", "system")
    assert res.requested == "Arial"


def test_similar_substitute(sysfonts, add_system_font, cfg):
    add_system_font("Montserrat")
    res = _resolve("Gotham-Medium", cfg)
    assert (res.match, res.resolved_family) == ("similar", "Montserrat")


def test_hint_used_before_generic(sysfonts, add_system_font, cfg):
    add_system_font("Zilla Slab")
    add_system_font("Source Sans 3")
    res = _resolve("Mystery Face", cfg, hints=["Zilla Slab"])
    assert (res.match, res.resolved_family) == ("similar", "Zilla Slab")


def test_generic_by_classification(sysfonts, add_system_font, cfg):
    add_system_font("Source Serif 4", "DejaVuSerif.ttf")
    add_system_font("Source Sans 3")
    assert _resolve("Mystery Face", cfg, classification="serif").resolved_family == "Source Serif 4"
    res = _resolve("Mystery Face", cfg)
    assert (res.match, res.resolved_family) == ("generic", "Source Sans 3")


def test_offline_last_resort_system_font(sysfonts, cfg):
    cfg = cfg.model_copy(update={"allow_font_download": False})
    called = []
    res = _resolve("Mystery Face", cfg, fetcher=lambda *a: called.append(a) or [])
    assert not called
    assert res.match == "generic" and res.source == "generic"
    assert res.resolved_family == "DejaVu Sans" and res.faces
    mono = _resolve("Obscure Mono", cfg)
    assert mono.resolved_family == "DejaVu Sans Mono"


def test_nothing_available_never_raises(tmp_path, monkeypatch, cfg):
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(system_index.ENV_DIRS, str(empty))
    system_index._memo.clear()

    def boom(*a):
        raise OSError("offline")

    res = _resolve("Whatever", cfg, fetcher=boom)
    assert res.match == "generic" and res.faces == [] and res.resolved_family == "sans-serif"
    assert "offline" in res.note or "OSError" in res.note


def test_google_fetch_and_cache(sysfonts, cfg):
    calls = []

    def fetcher(family, weights, italic, dest):
        calls.append((family, tuple(weights), italic, dest))
        return [make_renamed_font(MPL_TTF / "DejaVuSans.ttf", family, dest / "Fancy-Regular.ttf")]

    res = _resolve("FancyFont-Regular", cfg, fetcher=fetcher)
    assert (res.match, res.source, res.resolved_family) == ("exact", "google", "Fancy Font")
    assert len(calls) == 1 and calls[0][0] == "Fancy Font"
    assert str(cfg.font_cache_dir) in str(res.faces[0].path)
    # second call is served from the cache, even with downloads disabled
    offline = cfg.model_copy(update={"allow_font_download": False})
    res2 = _resolve("Fancy Font", offline, fetcher=fetcher)
    assert res2.source == "google" and len(calls) == 1


def test_proprietary_family_not_requested_from_google(sysfonts, cfg):
    calls = []
    _resolve("Gotham", cfg, fetcher=lambda family, *a: calls.append(family) or [])
    assert "Gotham" not in calls and "Montserrat" in calls  # substitute is tried, original is not


def test_google_download_disabled(sysfonts, cfg):
    cfg = cfg.model_copy(update={"allow_font_download": False})
    calls = []
    _resolve("Fancy Font", cfg, fetcher=lambda *a: calls.append(a) or [])
    assert not calls


# -- fsType / cmap / embedded reuse ------------------------------------------


@pytest.mark.parametrize("fs,ok", [(0, True), (8, True), (2, False), (4, False), (0x200, False), (None, False),
                                   (8 | 0x100, True)])
def test_embedding_allowed(fs, ok):
    assert fontfiles.embedding_allowed(fs) is ok


def test_cmap_coverage():
    path = MPL_TTF / "DejaVuSans.ttf"
    assert fontfiles.cmap_covers(path, "Hello, world 123")
    assert not fontfiles.cmap_covers(path, "Hello 中")
    assert fontfiles.missing_chars(path, "a中 \n") == {"中"}


def _pdf_with_font(tmp_path: Path, fontfile: Path, name: str) -> Path:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_font(fontname=name, fontfile=str(fontfile))
    page.insert_text((72, 72), "Hello", fontname=name, fontsize=12)
    out = tmp_path / "ref.pdf"
    doc.save(str(out))
    return out


def _embedded_cfg(cfg, pdf):
    return cfg.model_copy(update={"reuse_embedded_fonts": True, "reference_pdf": pdf})


def test_embedded_reuse_allowed(tmp_path, cfg):
    ttf = make_renamed_font(MPL_TTF / "DejaVuSans.ttf", "Licensed Sans", tmp_path / "ls.ttf", fs_type=0)
    pdf = _pdf_with_font(tmp_path, ttf, "LicensedSans")
    c = _embedded_cfg(cfg, pdf)
    res = _resolve("LicensedSans", c, reference_pdf=pdf)
    assert res.source == "embedded" and res.match == "exact"
    assert fontfiles.cmap_covers(Path(res.faces[0].path), "Hello")


def test_embedded_reuse_off_by_default(tmp_path, sysfonts, cfg):
    ttf = make_renamed_font(MPL_TTF / "DejaVuSans.ttf", "Licensed Sans", tmp_path / "ls.ttf", fs_type=0)
    pdf = _pdf_with_font(tmp_path, ttf, "LicensedSans")
    res = _resolve("LicensedSans", cfg, reference_pdf=pdf)
    assert res.source != "embedded"


def test_embedded_reuse_refused_by_fstype(tmp_path, sysfonts, cfg):
    ttf = make_renamed_font(MPL_TTF / "DejaVuSans.ttf", "Locked Sans", tmp_path / "ls.ttf", fs_type=2)
    pdf = _pdf_with_font(tmp_path, ttf, "LockedSans")
    res = _resolve("LockedSans", _embedded_cfg(cfg, pdf), reference_pdf=pdf)
    assert res.source != "embedded" and "fsType" in res.note


def test_embedded_reuse_refused_when_text_not_covered(tmp_path, sysfonts, cfg):
    ttf = make_renamed_font(MPL_TTF / "DejaVuSans.ttf", "Licensed Sans", tmp_path / "ls.ttf", fs_type=0)
    pdf = _pdf_with_font(tmp_path, ttf, "LicensedSans")
    res = _resolve("LicensedSans", _embedded_cfg(cfg, pdf), reference_pdf=pdf, required_chars="Hello 中")
    assert res.source != "embedded" and "cover" in res.note


# -- resolve_design_fonts ----------------------------------------------------


def test_design_fonts_exact(sysfonts, add_system_font, cfg):
    add_system_font("Montserrat")
    add_system_font("Montserrat", bold=True)
    spec = factories.sample_design_spec()
    out = resolve_design_fonts(spec, required_chars="Hello", config=cfg)
    assert out is not spec and spec.fonts == []
    assert [f.resolved_family for f in out.fonts] == ["Montserrat"]
    assert out.fonts[0].match == "exact"
    assert all(s.family == "Montserrat" for s in out.typography)
    for face in out.fonts[0].faces:
        assert not Path(face.path).is_absolute()
        assert (cfg.work_dir / face.path).is_file()


def test_design_fonts_substitution_and_stack(sysfonts, add_system_font, cfg):
    add_system_font("Montserrat")
    spec = factories.sample_design_spec()
    typo = [s.model_copy(update={"family": "Gotham", "fallback_stack": ["Arial", "sans-serif"]})
            for s in spec.typography]
    spec = spec.model_copy(update={"typography": typo})
    out = resolve_design_fonts(spec, required_chars="Hello", config=cfg, hints={"Gotham": ["Nunito Sans"]})
    assert out.fonts[0].match == "similar" and out.fonts[0].requested == "Gotham"
    st = out.typography[0]
    assert st.family == "Montserrat" and st.fallback_stack[0] == "Gotham"
    assert st.fallback_stack[-1] == "sans-serif"
    assert any("Gotham" in w for w in out.warnings)


def test_design_fonts_unknown_never_raises(sysfonts, cfg):
    cfg = cfg.model_copy(update={"allow_font_download": False})
    spec = factories.sample_design_spec()
    upd = {"family": "Zzyzx Grotesk", "source_font": "ABCDEF+Zzyzx-Bold"}
    typo = [s.model_copy(update=upd) for s in spec.typography]
    out = resolve_design_fonts(spec.model_copy(update={"typography": typo}), required_chars="Hello", config=cfg)
    res = out.fonts[0]
    assert res.match == "generic" and res.faces
    assert (cfg.work_dir / res.faces[0].path).is_file()
    assert out.typography[0].family == res.resolved_family


def test_design_fonts_json_roundtrip(sysfonts, cfg):
    out = resolve_design_fonts(factories.sample_design_spec(), required_chars="Hello", config=cfg)
    type(out).model_validate_json(out.model_dump_json())


# -- google helpers ----------------------------------------------------------


def _listing(*names):
    return [{"name": n, "download_url": f"https://x/{n}"} for n in names]


def test_choose_files_variable():
    ls = _listing("Inter[opsz,wght].ttf", "Inter-Italic[opsz,wght].ttf", "OFL.txt")
    assert [f["name"] for f in choose_files(ls, [400], False)] == ["Inter[opsz,wght].ttf"]
    assert len(choose_files(ls, [400], True)) == 2


def test_choose_files_static():
    ls = _listing("Foo-Regular.ttf", "Foo-Bold.ttf", "Foo-Italic.ttf", "Foo-BoldItalic.ttf", "Foo-Light.ttf")
    got = {f["name"] for f in choose_files(ls, [400, 700], True)}
    assert got == {"Foo-Regular.ttf", "Foo-Bold.ttf", "Foo-Italic.ttf", "Foo-BoldItalic.ttf"}


@pytest.mark.network
def test_live_google_download(tmp_path):
    paths = fetch_google_family("Inter", [400, 700], False, tmp_path)
    assert paths and all(p.is_file() for p in paths)
    assert (tmp_path / "license.json").is_file()


def test_select_faces_keeps_ttc_member_number():
    from rooted.fonts.system_index import select_faces

    E = fontfiles.FontEntry
    entries = [
        E("Foo", "Foo", "Regular", 400, False, "/x/Foo.ttc", 0, False, 0, 0),
        E("Foo", "Foo", "Bold", 700, False, "/x/Foo.ttc", 2, False, 0, 0),
    ]
    faces = {f.weight: f for f in select_faces(entries, [400, 700], False)}
    assert (faces[400].number, faces[700].number) == (0, 2)


def test_stage_face_extracts_ttc_member(tmp_path):
    from fontTools.ttLib import TTCollection, TTFont

    from rooted.fonts.resolve import _stage_face
    from rooted.models import FontFace

    ttc = tmp_path / "Pair.ttc"
    coll = TTCollection()
    coll.fonts = [TTFont(str(MPL_TTF / "DejaVuSans.ttf")), TTFont(str(MPL_TTF / "DejaVuSans-Bold.ttf"))]
    coll.save(str(ttc))
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    staged = _stage_face(FontFace(family="Pair", path=str(ttc), weight=700, number=1), fonts_dir)
    assert staged.path == "fonts/Pair-1.ttf" and staged.number == 0
    out = TTFont(str(tmp_path / staged.path))
    assert out["OS/2"].usWeightClass == 700
    assert fontfiles.embedding_allowed(fontfiles.fs_type(tmp_path / staged.path))
