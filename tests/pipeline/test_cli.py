from __future__ import annotations

import pytest
from typer.testing import CliRunner

import rooted.pipeline as pipeline
from rooted.cli import app
from rooted.errors import LLMError, RenderError, StageError, TraceabilityError
from rooted.models import Stage

runner = CliRunner()


def _args(inputs, tmp_path, *extra):
    docx, pdf = inputs
    return [
        "build",
        str(docx),
        "--model",
        str(pdf),
        "-o",
        str(tmp_path / "o.pdf"),
        "--work-dir",
        str(tmp_path / "w"),
        *extra,
    ]


@pytest.fixture
def captured(monkeypatch):
    seen = {}

    def fake(config, llm=None):
        seen["config"] = config
        return None

    monkeypatch.setattr(pipeline, "run_pipeline", fake)
    return seen


def test_help_lists_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for name in ("build", "inspect-design", "doctor"):
        assert name in r.output


def test_option_parsing(inputs, tmp_path, captured):
    r = runner.invoke(
        app,
        _args(
            inputs,
            tmp_path,
            "--brief",
            "8 pages",
            "--stage",
            "content",
            "--from",
            "design",
            "--force",
            "--dry-run",
            "--renderer",
            "weasyprint",
            "--llm-model",
            "claude-x",
            "--effort",
            "low",
            "--no-fallbacks",
            "--reuse-embedded-fonts",
            "--offline",
            "-q",
        ),
    )
    assert r.exit_code == 0, r.output
    c = captured["config"]
    assert c.brief_text == "8 pages"
    assert c.stop_after == Stage.CONTENT and c.from_stage == Stage.DESIGN
    assert c.force and c.dry_run and c.renderer == "weasyprint"
    assert c.llm.model == "claude-x" and c.llm.effort == "low" and not c.llm.server_fallbacks
    assert c.reuse_embedded_fonts and not c.allow_font_download
    assert c.log_level == "WARNING"


def test_defaults(inputs, tmp_path, captured):
    docx, pdf = inputs
    r = runner.invoke(app, ["build", str(docx), "--model", str(pdf), "--work-dir", str(tmp_path / "w")])
    assert r.exit_code == 0, r.output
    c = captured["config"]
    assert c.renderer == "chromium" and c.brief_text == "" and c.llm.server_fallbacks and c.allow_font_download


def test_brief_file(inputs, tmp_path, captured):
    bf = tmp_path / "brief.txt"
    bf.write_text("12 pages please", "utf-8")
    r = runner.invoke(app, _args(inputs, tmp_path, "--brief-file", str(bf)))
    assert r.exit_code == 0, r.output
    assert captured["config"].brief_text == "12 pages please"


def test_brief_and_brief_file_are_exclusive(inputs, tmp_path, captured):
    bf = tmp_path / "b.txt"
    bf.write_text("x")
    r = runner.invoke(app, _args(inputs, tmp_path, "--brief", "a", "--brief-file", str(bf)))
    assert r.exit_code == 2
    assert "config" not in captured


@pytest.mark.parametrize("extra", [["--renderer", "pango"], ["--effort", "ultra"], ["--brief-file", "/nonexistent"]])
def test_bad_options_exit_2(inputs, tmp_path, captured, extra):
    assert runner.invoke(app, _args(inputs, tmp_path, *extra)).exit_code == 2


def test_missing_files_exit_2(tmp_path, captured):
    r = runner.invoke(app, ["build", str(tmp_path / "no.docx"), "--model", str(tmp_path / "no.pdf")])
    assert r.exit_code == 2
    assert "not found" in r.output


def test_invalid_stage_rejected(inputs, tmp_path):
    assert runner.invoke(app, _args(inputs, tmp_path, "--stage", "bogus")).exit_code == 2


@pytest.mark.parametrize(
    "exc,code",
    [
        (StageError("design", "x"), 3),
        (LLMError("x"), 4),
        (TraceabilityError("x"), 5),
        (RenderError("x"), 6),
        (RuntimeError("boom"), 1),
    ],
)
def test_exit_code_mapping(inputs, tmp_path, monkeypatch, exc, code):
    def boom(config, llm=None):
        raise exc

    monkeypatch.setattr(pipeline, "run_pipeline", boom)
    r = runner.invoke(app, _args(inputs, tmp_path))
    assert r.exit_code == code
    assert "error" in r.output.lower()


def test_build_end_to_end_with_fake_stages(inputs, tmp_path, stages):
    r = runner.invoke(app, _args(inputs, tmp_path, "--brief", "8 pages"))
    assert r.exit_code == 0, r.output
    assert (tmp_path / "o.pdf").exists()
    assert (tmp_path / "w" / "rooted.log").exists()
    assert "Build summary" in r.output


def test_doctor_runs():
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code in (0, 1)
    assert "chromium" in r.output.lower()
    assert "pango" not in r.output.lower()


def test_doctor_reports_missing_chromium(monkeypatch):
    import rooted.cli as cli

    monkeypatch.setattr(
        cli,
        "_check_chromium",
        lambda: (cli.FAIL, "Playwright Chromium is not installed; run: playwright install chromium"),
    )
    r = runner.invoke(app, ["doctor"])
    assert r.exit_code == 1
    assert "playwright install chromium" in r.output.replace("\n", " ")


def test_inspect_design_prints_summary(inputs, tmp_path, stages):
    _, pdf = inputs
    r = runner.invoke(app, ["inspect-design", str(pdf), "--work-dir", str(tmp_path / "w")])
    assert r.exit_code == 0, r.output
    assert "Typography" in r.output and "Palette" in r.output
    assert (tmp_path / "w" / "design.json").exists()


def test_inspect_design_missing_file(tmp_path):
    assert runner.invoke(app, ["inspect-design", str(tmp_path / "x.pdf")]).exit_code == 2
