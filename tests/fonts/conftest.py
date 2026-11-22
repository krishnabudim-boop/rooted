"""Offline font fixtures: matplotlib's bundled DejaVu fonts stand in for system fonts."""

from __future__ import annotations

import shutil
from pathlib import Path

import matplotlib
import pytest
from fontTools.ttLib import TTFont

from rooted.fonts import system_index
from rooted.models import BuildConfig

MPL_TTF = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
DEJAVU = ["DejaVuSans.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans-Oblique.ttf", "DejaVuSans-BoldOblique.ttf",
          "DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSansMono.ttf"]


def make_renamed_font(src: Path, family: str, dest: Path, fs_type: int | None = None) -> Path:
    """Copy ``src`` with its family renamed (a stand-in for e.g. Montserrat)."""
    font = TTFont(str(src))
    for rec in font["name"].names:
        if rec.nameID in (1, 16):
            rec.string = family
        elif rec.nameID == 4:
            rec.string = f"{family} {rec.toUnicode().split(' ', 2)[-1]}"
    if fs_type is not None:
        font["OS/2"].fsType = fs_type
    dest.parent.mkdir(parents=True, exist_ok=True)
    font.save(str(dest))
    return dest


@pytest.fixture
def sysfonts(tmp_path, monkeypatch) -> Path:
    """A temp 'system font' directory holding the DejaVu family; replaces the real one."""
    d = tmp_path / "sysfonts"
    d.mkdir()
    for name in DEJAVU:
        shutil.copy(MPL_TTF / name, d / name)
    monkeypatch.setenv(system_index.ENV_DIRS, str(d))
    system_index._memo.clear()
    yield d
    system_index._memo.clear()


@pytest.fixture
def add_system_font(sysfonts):
    def add(family: str, src: str = "DejaVuSans.ttf", bold: bool = False) -> Path:
        system_index._memo.clear()
        name = f"{family.replace(' ', '')}-{'Bold' if bold else 'Regular'}.ttf"
        return make_renamed_font(MPL_TTF / (src if not bold else src.replace(".ttf", "-Bold.ttf")), family,
                                 sysfonts / name)

    return add


@pytest.fixture
def cfg(tmp_path) -> BuildConfig:
    return BuildConfig(
        content_path=tmp_path / "c.docx", reference_pdf=tmp_path / "r.pdf",
        work_dir=tmp_path / "work", font_cache_dir=tmp_path / "cache",
    )
