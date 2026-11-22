from __future__ import annotations

import pytest

from rooted.cache import WorkDir, hash_inputs
from rooted.errors import StageError
from rooted.models import Brief, Stage
from tests import factories


def test_hash_inputs_stable_and_sensitive(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("one")
    h1 = hash_inputs(f, "x", factories.sample_brief())
    assert h1 == hash_inputs(f, "x", factories.sample_brief())
    f.write_text("two")
    assert hash_inputs(f, "x", factories.sample_brief()) != h1
    assert hash_inputs("a", "b") != hash_inputs("ab")
    assert hash_inputs(Brief(target_pages=3)) != hash_inputs(Brief(target_pages=4))


def test_hash_inputs_missing_file_does_not_raise(tmp_path):
    assert hash_inputs(tmp_path / "nope.docx")


def test_save_load_roundtrip(tmp_path):
    wd = WorkDir(tmp_path).ensure()
    b = factories.sample_brief()
    path = wd.save("brief", b)
    assert path.name == "brief.json"
    assert wd.load("brief", Brief) == b
    assert wd.load("missing", Brief) is None
    for sub in ("assets", "thumbs", "visuals", "fonts", "llm_cache", "out"):
        assert (tmp_path / sub).is_dir()


def test_load_invalid_artifact_raises_stage_error(tmp_path):
    wd = WorkDir(tmp_path)
    (tmp_path / "brief.json").write_text("{not json")
    with pytest.raises(StageError):
        wd.load("brief", Brief)


def test_manifest_freshness(tmp_path):
    wd = WorkDir(tmp_path).ensure()
    assert wd.manifest().records == []
    assert not wd.is_fresh(Stage.BRIEF, "h")
    wd.save("brief", factories.sample_brief())
    wd.record(Stage.BRIEF, "h", "brief.json", 0.5)
    assert wd.is_fresh(Stage.BRIEF, "h")
    assert not wd.is_fresh(Stage.BRIEF, "other")
    rec = wd.manifest().get(Stage.BRIEF)
    assert rec.artifact_sha256 == wd.artifact_sha256("brief")
    # re-recording replaces, not duplicates
    wd.record(Stage.BRIEF, "h2", "brief.json", 0.1)
    assert len(wd.manifest().records) == 1
    (tmp_path / "brief.json").unlink()
    assert not wd.is_fresh(Stage.BRIEF, "h2")


def test_corrupt_manifest_is_treated_as_empty(tmp_path):
    (tmp_path / "manifest.json").write_text("garbage")
    assert WorkDir(tmp_path).manifest().records == []
