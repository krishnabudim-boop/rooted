"""Google Fonts download through the public google/fonts GitHub repository (WP1).

This is the default ``FontFetcher``. It is never called from tests unless they
are marked ``network``. Only OFL, Apache and UFL licensed directories are used;
the license text and a ``license.json`` are saved next to the files.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional, Sequence

_API = "https://api.github.com/repos/google/fonts/contents/{lic}/{slug}"
_LICENSE_DIRS = {"ofl": "OFL-1.1", "apache": "Apache-2.0", "ufl": "UFL-1.0"}
_UA = {"User-Agent": "rooted-font-fetcher"}
_TIMEOUT = 20


def family_slug(family: str) -> str:
    return re.sub(r"[^a-z0-9]", "", family.lower())


def cache_subdir(cache_dir: Path, family: str) -> Path:
    return Path(cache_dir) / family_slug(family)


def choose_files(listing: Sequence[dict], weights: Sequence[int], italic: bool) -> list[dict]:
    """Pick the .ttf entries of a GitHub directory listing worth downloading.

    Variable fonts (``Name[wght].ttf``) win when present: one upright file, plus
    the italic one if wanted. Otherwise the static file with the nearest weight
    is chosen for each requested weight.
    """
    from rooted.fonts.resolve import normalize_font_name  # local import: avoids a cycle

    ttfs = [f for f in listing if f.get("name", "").lower().endswith(".ttf") and f.get("download_url")]
    variable = [f for f in ttfs if "[" in f["name"]]
    if variable:
        up = [f for f in variable if "italic" not in f["name"].lower()]
        it = [f for f in variable if "italic" in f["name"].lower()]
        return up[:1] + (it[:1] if italic else [])
    parsed = []
    for f in ttfs:
        _fam, w, is_it = normalize_font_name(f["name"][:-4])
        parsed.append((w, is_it, f))
    chosen: list[dict] = []
    for want_it in ([False, True] if italic else [False]):
        pool = [(w, f) for w, it, f in parsed if it == want_it]
        for w in weights or [400]:
            if pool:
                best = min(pool, key=lambda p: (abs(p[0] - w), p[1]["name"]))[1]
                if best not in chosen:
                    chosen.append(best)
    return chosen


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 - fixed https hosts
        return resp.read()


def fetch_google_family(family: str, weights: Sequence[int], italic: bool, dest_dir: Path) -> list[Path]:
    """Download ``family`` into ``dest_dir``. Returns [] when Google does not have it."""
    slug = family_slug(family)
    dest_dir = Path(dest_dir)
    for lic, spdx in _LICENSE_DIRS.items():
        url = _API.format(lic=lic, slug=slug)
        try:
            listing = json.loads(_get(url))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                continue
            raise
        if not isinstance(listing, list):
            continue
        files = choose_files(listing, weights, italic)
        if not files:
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        out: list[Path] = []
        for f in files:
            target = dest_dir / Path(f["name"]).name
            if not target.exists():
                target.write_bytes(_get(f["download_url"]))
            out.append(target)
        license_file: Optional[str] = None
        for f in listing:
            if f.get("name") in ("OFL.txt", "LICENSE.txt", "UFL.txt") and f.get("download_url"):
                (dest_dir / f["name"]).write_bytes(_get(f["download_url"]))
                license_file = f["name"]
        (dest_dir / "license.json").write_text(
            json.dumps(
                {"family": family, "license": spdx, "license_file": license_file,
                 "source": f"https://github.com/google/fonts/tree/main/{lic}/{slug}", "files": [p.name for p in out]},
                indent=2,
            ),
            "utf-8",
        )
        return out
    return []
