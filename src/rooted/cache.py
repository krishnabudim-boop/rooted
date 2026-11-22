"""Work-dir artifact store + manifest (WP6)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from rooted.errors import StageError
from rooted.models import Manifest, Stage, StageRecord

T = TypeVar("T", bound=BaseModel)

# Bump a stage's version when its output semantics change (invalidates cache).
STAGE_VERSIONS: dict[Stage, str] = {s: "1" for s in Stage}

SUBDIRS = ("assets", "thumbs", "visuals", "fonts", "llm_cache", "out")


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, "utf-8")
    os.replace(tmp, path)


class WorkDir:
    """Layout::

        <work_dir>/manifest.json
        <work_dir>/ingest.json brief.json design_raw.json design_vision.json
                   design.json content.json visuals.json layout.json render.json
        <work_dir>/assets/      docx images
        <work_dir>/thumbs/      reference page PNGs
        <work_dir>/visuals/     rendered SVGs
        <work_dir>/fonts/       resolved font files (or symlinks into the font cache)
        <work_dir>/llm_cache/   CachingLLMClient responses
        <work_dir>/out/         document.html, intermediate PDFs from fitting
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def ensure(self) -> WorkDir:
        """Create the directory skeleton."""
        self.root.mkdir(parents=True, exist_ok=True)
        for sub in SUBDIRS:
            (self.root / sub).mkdir(exist_ok=True)
        return self

    def path(self, name: str) -> Path:
        return self.root / (name if name.endswith(".json") else f"{name}.json")

    def save(self, name: str, model: BaseModel) -> Path:
        path = self.path(name)
        _atomic_write(path, model.model_dump_json(indent=2))
        return path

    def load(self, name: str, model_type: Type[T]) -> Optional[T]:
        """Return the artifact, None if absent. A present-but-invalid file
        (e.g. a bad hand edit) raises StageError naming the file."""
        path = self.path(name)
        if not path.exists():
            return None
        try:
            return model_type.model_validate_json(path.read_text("utf-8"))
        except (ValidationError, ValueError) as e:
            raise StageError(path.stem, f"{path} is not a valid {model_type.__name__}: {e}") from e

    def artifact_sha256(self, name: str) -> str:
        """sha256 of the artifact file on disk ('' when missing). Reflects hand edits."""
        path = self.path(name)
        return _file_sha256(path) if path.exists() else ""

    def manifest(self) -> Manifest:
        path = self.root / "manifest.json"
        if not path.exists():
            return Manifest()
        try:
            return Manifest.model_validate_json(path.read_text("utf-8"))
        except (ValidationError, ValueError):
            return Manifest()  # corrupt manifest: treat everything as stale

    def is_fresh(self, stage: Stage, input_hash: str) -> bool:
        rec = self.manifest().get(stage)
        return rec is not None and rec.input_hash == input_hash and self.path(rec.artifact).exists()

    def record(self, stage: Stage, input_hash: str, artifact: str, duration_s: float) -> None:
        man = self.manifest()
        rec = StageRecord(
            stage=stage,
            input_hash=input_hash,
            artifact=artifact,
            artifact_sha256=self.artifact_sha256(artifact),
            completed_at=datetime.now(UTC).isoformat(timespec="seconds"),
            duration_s=round(duration_s, 3),
        )
        man.records = [r for r in man.records if r.stage != stage] + [rec]
        _atomic_write(self.root / "manifest.json", man.model_dump_json(indent=2))

    def invalidate(self, stages: list[Stage]) -> None:
        """Drop manifest records for ``stages`` (artifacts are kept)."""
        man = self.manifest()
        man.records = [r for r in man.records if r.stage not in stages]
        _atomic_write(self.root / "manifest.json", man.model_dump_json(indent=2))


def hash_inputs(*parts: object) -> str:
    """sha256 over file bytes (Path), model JSON (BaseModel, sorted keys) and str."""
    h = hashlib.sha256()
    for part in parts:
        if isinstance(part, Path):
            if part.is_file():
                h.update(b"file:" + _file_sha256(part).encode())
            else:
                h.update(b"missing:" + part.name.encode())
        elif isinstance(part, BaseModel):
            h.update(b"model:" + json.dumps(part.model_dump(mode="json"), sort_keys=True).encode("utf-8"))
        elif isinstance(part, str):
            h.update(b"str:" + part.encode("utf-8"))
        elif isinstance(part, bytes):
            h.update(b"bytes:" + part)
        else:
            h.update(b"json:" + json.dumps(part, sort_keys=True, default=str).encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()
