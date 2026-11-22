"""Typer CLI (WP6). Entry point: ``rooted = rooted.cli:app``.

    rooted build CONTENT.docx --model REF.pdf (--brief TEXT | --brief-file F)
                 [-o out.pdf] [--work-dir DIR] [--stage STAGE] [--from STAGE]
                 [--force] [--dry-run] [--renderer chromium|weasyprint]
                 [--backend claude-code|api] [--llm-model ID] [--effort LEVEL] [--no-fallbacks]
                 [--reuse-embedded-fonts] [--offline] [-v/-q]
    rooted inspect-design REF.pdf [--work-dir DIR]   # design stage only, prints summary
    rooted doctor                                    # checks Chromium, fonts cache, claude CLI login
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from rooted.cache import hash_inputs
from rooted.errors import InputError, RootedError
from rooted.logging_setup import get_console, setup_logging
from rooted.models import Brief, BuildConfig, LLMConfig, Stage

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Replicate a reference PDF's design with your own content.",
)

_RENDERERS = ("chromium", "weasyprint")
_EFFORTS = ("low", "medium", "high", "xhigh", "max")
_BACKENDS = ("claude-code", "api")
_BACKEND_HELP = "LLM backend: claude-code (default; your Claude Code login) or api (Anthropic API key)."


def _check_backend(backend: Optional[str]) -> None:
    if backend is not None and backend not in _BACKENDS:
        raise InputError(f"--backend must be one of {', '.join(_BACKENDS)}")


def _fail(err: Exception, verbose: bool = False) -> typer.Exit:
    console = get_console()
    code = err.exit_code if isinstance(err, RootedError) else 1
    console.print(f"[bold red]error:[/bold red] {err}")
    if verbose and not isinstance(err, RootedError):
        console.print_exception()
    return typer.Exit(code)


def _default_work_dir(content: Path, reference: Path) -> Path:
    return Path(".rooted") / hash_inputs(content, reference)[:8]


def _log_level(verbose: int, quiet: bool) -> str:
    if quiet:
        return "WARNING"
    return "DEBUG" if verbose >= 1 else "INFO"


@app.command()
def build(
    content: Path = typer.Argument(..., help="The .docx with your text."),
    model: Path = typer.Option(..., "--model", "-m", help="Reference PDF whose design is replicated."),
    brief: Optional[str] = typer.Option(None, "--brief", "-b", help="Short brief, e.g. '8 pages, navy and gold'."),
    brief_file: Optional[Path] = typer.Option(None, "--brief-file", help="Read the brief from a text file."),
    output: Path = typer.Option(Path("out.pdf"), "--output", "-o", help="Output PDF path."),
    work_dir: Optional[Path] = typer.Option(None, "--work-dir", help="Artifact dir (default .rooted/<hash>)."),
    stage: Optional[Stage] = typer.Option(None, "--stage", help="Stop after this stage."),
    from_stage: Optional[Stage] = typer.Option(None, "--from", help="Re-run this stage and everything after it."),
    force: bool = typer.Option(False, "--force", help="Ignore cached artifacts and re-run every stage."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only: no LLM calls, no PDF."),
    renderer: str = typer.Option("chromium", "--renderer", help="chromium (default) or weasyprint."),
    backend: Optional[str] = typer.Option(None, "--backend", help=_BACKEND_HELP),
    llm_model: Optional[str] = typer.Option(None, "--llm-model", help="Claude model id."),
    effort: Optional[str] = typer.Option(None, "--effort", help="Default effort: low|medium|high|xhigh|max."),
    no_fallbacks: bool = typer.Option(False, "--no-fallbacks", help="Disable server-side refusal fallbacks."),
    reuse_embedded_fonts: bool = typer.Option(
        False, "--reuse-embedded-fonts", help="Allow reusing fonts embedded in the reference (check licensing)."
    ),
    offline: bool = typer.Option(False, "--offline", help="Never download fonts."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True, help="Debug logging."),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Warnings and errors only."),
) -> None:
    """Build a PDF from CONTENT with the design of the reference PDF."""
    from rooted.pipeline import run_pipeline

    console = get_console()
    try:
        if brief is not None and brief_file is not None:
            raise InputError("use either --brief or --brief-file, not both")
        if renderer not in _RENDERERS:
            raise InputError(f"--renderer must be one of {', '.join(_RENDERERS)}")
        _check_backend(backend)
        if effort is not None and effort not in _EFFORTS:
            raise InputError(f"--effort must be one of {', '.join(_EFFORTS)}")
        if not content.is_file():
            raise InputError(f"content file not found: {content}")
        if not model.is_file():
            raise InputError(f"reference PDF not found: {model}")
        brief_text = brief or ""
        if brief_file is not None:
            try:
                brief_text = brief_file.read_text("utf-8")
            except OSError as e:
                raise InputError(f"cannot read brief file {brief_file}: {e}") from e

        llm_cfg = LLMConfig()
        if backend:
            llm_cfg.backend = backend  # type: ignore[assignment]
        if llm_model:
            llm_cfg.model = llm_model
        if effort:
            llm_cfg.effort = effort
        if no_fallbacks:
            llm_cfg.server_fallbacks = False

        config = BuildConfig(
            content_path=content,
            reference_pdf=model,
            brief_text=brief_text,
            output_path=output,
            work_dir=work_dir or _default_work_dir(content, model),
            stop_after=stage,
            from_stage=from_stage,
            force=force,
            dry_run=dry_run,
            llm=llm_cfg,
            renderer=renderer,
            reuse_embedded_fonts=reuse_embedded_fonts,
            allow_font_download=not offline,
            log_level=_log_level(verbose, quiet),
        )
        config.work_dir.mkdir(parents=True, exist_ok=True)
        setup_logging(config.log_level, config.work_dir / "rooted.log")
        console.print(f"work dir: {config.work_dir}")
        report = run_pipeline(config)
    except KeyboardInterrupt:
        console.print("[yellow]interrupted[/yellow]")
        raise typer.Exit(130) from None
    except Exception as e:  # noqa: BLE001 - mapped to exit codes
        raise _fail(e, verbose > 0) from e

    if report is None:
        return
    fit = report.fit
    table = Table(title="Build summary", show_header=False)
    table.add_row("output", report.output_pdf)
    target = fit.target_pages if fit.target_pages is not None else "natural"
    table.add_row("pages", f"{fit.achieved_pages} (target {target}, {fit.status})")
    table.add_row("fit iterations", str(len(fit.iterations)))
    table.add_row("generated text items", str(len(report.generated_text)))
    table.add_row("dropped/rewritten blocks", str(sum(t.status in ("dropped", "rewritten") for t in report.trace)))
    console.print(table)
    for w in report.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")


@app.command("inspect-design")
def inspect_design(
    reference: Path = typer.Argument(..., help="Reference PDF."),
    work_dir: Optional[Path] = typer.Option(None, "--work-dir", help="Artifact dir (default .rooted/inspect-<hash>)."),
    backend: Optional[str] = typer.Option(None, "--backend", help=_BACKEND_HELP),
    llm_model: Optional[str] = typer.Option(None, "--llm-model", help="Claude model id."),
    offline: bool = typer.Option(False, "--offline", help="Never download fonts."),
    verbose: int = typer.Option(0, "--verbose", "-v", count=True),
    quiet: bool = typer.Option(False, "--quiet", "-q"),
) -> None:
    """Run only the design stage and print the extracted DesignSpec summary."""
    import rooted.design as design_pkg
    import rooted.fonts as fonts_pkg
    from rooted.cache import WorkDir
    from rooted.llm import build_client
    from rooted.models import ColorRole, TypeRole  # noqa: F401

    console = get_console()
    try:
        if not reference.is_file():
            raise InputError(f"reference PDF not found: {reference}")
        wd_path = work_dir or Path(".rooted") / f"inspect-{hash_inputs(reference)[:8]}"
        _check_backend(backend)
        llm_cfg = LLMConfig(**({"model": llm_model} if llm_model else {}), **({"backend": backend} if backend else {}))
        config = BuildConfig(
            content_path=reference,  # unused by the design stage
            reference_pdf=reference,
            work_dir=wd_path,
            llm=llm_cfg,
            allow_font_download=not offline,
        )
        wd = WorkDir(wd_path).ensure()
        setup_logging(_log_level(verbose, quiet), wd.root / "rooted.log")
        llm = build_client(llm_cfg, wd.root / "llm_cache")
        with console.status("extracting design ..."):
            raw, notes, spec = design_pkg.extract_design(reference, wd.root, Brief(), llm, config)
            wd.save("design_raw", raw)
            if notes is not None:
                wd.save("design_vision", notes)
            from rooted.pipeline import _font_hints

            spec = fonts_pkg.resolve_design_fonts(spec, required_chars="", config=config, hints=_font_hints(raw, notes))
            wd.save("design", spec)
    except KeyboardInterrupt:
        raise typer.Exit(130) from None
    except Exception as e:  # noqa: BLE001
        raise _fail(e, verbose > 0) from e

    ps = spec.page_size
    console.print(
        f"[bold]{reference.name}[/bold]: {raw.page_count} pages, "
        f"page size {ps.name or ''} {ps.width_pt:.0f}x{ps.height_pt:.0f} pt"
    )
    m = spec.margins
    console.print(f"margins (t/r/b/l): {m.top:.0f}/{m.right:.0f}/{m.bottom:.0f}/{m.left:.0f} pt")
    if spec.style_summary:
        console.print(f"style: {spec.style_summary}")
    t = Table(title="Typography")
    for col in ("role", "family", "size pt", "weight", "color"):
        t.add_column(col)
    for s in spec.typography:
        color = getattr(s.color_role, "value", str(s.color_role))
        t.add_row(s.role.value, s.family, f"{s.size_pt:g}", str(s.weight), color)
    console.print(t)
    p = Table(title="Palette")
    p.add_column("role")
    p.add_column("hex")
    for c in spec.palette:
        p.add_row(c.role.value, c.hex)
    console.print(p)
    f = Table(title="Font mapping")
    for col in ("requested", "resolved", "match", "note"):
        f.add_column(col)
    for r in spec.fonts:
        f.add_row(
            str(getattr(r, "requested", "")), str(getattr(r, "resolved_family", "")),
            str(getattr(getattr(r, "match", ""), "value", getattr(r, "match", ""))), str(getattr(r, "note", "") or ""),
        )
    console.print(f)
    if spec.motifs:
        console.print("motifs: " + ", ".join(spec.motifs))
    for w in spec.warnings:
        console.print(f"[yellow]warning:[/yellow] {w}")
    console.print(f"artifacts: {wd.root}")


# --------------------------------------------------------------------------
# doctor
# --------------------------------------------------------------------------

OK, WARN, FAIL = "ok", "warn", "FAIL"


def _check_python() -> tuple[str, str]:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    return (OK if v >= (3, 11) else FAIL), f"Python {ver}" + ("" if v >= (3, 11) else " (need >= 3.11)")


def _check_imports() -> tuple[str, str]:
    import importlib

    missing = []
    for mod in ("anthropic", "pydantic", "typer", "rich", "docx", "pymupdf", "jinja2", "playwright", "matplotlib",
                "numpy", "fontTools", "PIL", "platformdirs"):
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod)
    if missing:
        return FAIL, "missing Python packages: " + ", ".join(missing) + " (pip install -e .)"
    return OK, "all required Python packages import"


def _check_chromium() -> tuple[str, str]:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:  # noqa: BLE001
        return FAIL, f"playwright not importable ({e}); pip install playwright"
    try:
        with sync_playwright() as p:
            exe = Path(p.chromium.executable_path)
    except Exception as e:  # noqa: BLE001
        return FAIL, f"could not query Playwright ({e}); run: playwright install chromium"
    if exe.exists():
        return OK, f"Chromium found: {exe}"
    return FAIL, "Playwright Chromium is not installed; run: playwright install chromium"


def _check_weasyprint() -> tuple[str, str]:
    try:
        import weasyprint  # noqa: F401
    except Exception as e:  # noqa: BLE001
        why = (str(e).splitlines() or [type(e).__name__])[0]
        return WARN, f"WeasyPrint unavailable (optional; chromium is the default renderer): {why}"
    return OK, "WeasyPrint available (optional renderer)"


def _check_font_cache() -> tuple[str, str]:
    try:
        import platformdirs

        d = Path(platformdirs.user_cache_dir("rooted")) / "fonts"
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write-test"
        probe.write_text("ok")
        probe.unlink()
    except Exception as e:  # noqa: BLE001
        return WARN, f"font cache not writable ({e}); use --offline or set a cache dir"
    return OK, f"font cache writable: {d}"


def _check_credentials() -> tuple[str, str]:
    """Informational: only the `--backend api` path needs an API credential."""
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return OK, "Anthropic credential found in the environment (used only with --backend api)"
    if shutil.which("ant"):
        return OK, "not needed for the claude-code backend; the `ant` CLI is installed for --backend api"
    return OK, "not needed for the claude-code backend (only for --backend api: set ANTHROPIC_API_KEY or `ant auth login`)"


def _check_claude_cli() -> tuple[str, str]:
    from rooted.llm_claude_code import claude_auth_status

    st = claude_auth_status()
    if not st.path:
        return WARN, "claude CLI not found; install Claude Code (https://claude.com/claude-code) or use --backend api"
    if st.error:
        return WARN, f"claude at {st.path} ({st.version}): could not read login status: {st.error}"
    if not st.logged_in:
        return FAIL, f"claude {st.version} found but not logged in; run `claude` and use /login"
    return OK, f"claude {st.version} at {st.path}; logged in ({st.auth_method})"


@app.command()
def doctor() -> None:
    """Check environment: Playwright Chromium, Python packages, font cache, Claude CLI login, API credentials."""
    console = get_console()
    checks = [
        ("python", _check_python),
        ("packages", _check_imports),
        ("chromium", _check_chromium),
        ("weasyprint", _check_weasyprint),
        ("font cache", _check_font_cache),
        ("claude cli", _check_claude_cli),
        ("api credentials", _check_credentials),
    ]
    table = Table(title="rooted doctor")
    table.add_column("check")
    table.add_column("status")
    table.add_column("detail", overflow="fold")
    failed = False
    for name, fn in checks:
        try:
            status, detail = fn()
        except Exception as e:  # noqa: BLE001
            status, detail = FAIL, str(e)
        failed |= status == FAIL
        color = {OK: "green", WARN: "yellow", FAIL: "red"}[status]
        table.add_row(name, f"[{color}]{status}[/{color}]", detail)
    console.print(table)
    raise typer.Exit(1 if failed else 0)


if __name__ == "__main__":  # pragma: no cover
    app()
