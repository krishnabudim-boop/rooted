"""Claude Code CLI backend: calls ``claude -p`` using the user's own Claude login.

Same ``LLMClient`` protocol and exception semantics as ``AnthropicLLMClient``.
All calls use stream-json in and out (the only mode that accepts images), with
the prompt on stdin and the schema via ``--json-schema``; the final ``result``
event carries ``structured_output``.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence, Type

from pydantic import ValidationError

from rooted.errors import LLMError, LLMRefusalError, LLMTruncatedError, LLMValidationError
from rooted.llm import (
    RETRY_ATTEMPTS,
    RETRY_BASE_DELAY_S,
    RETRY_MAX_DELAY_S,
    ImageInput,
    LLMCall,
    T,
    _content_blocks,
)
from rooted.models import LLMConfig

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 900.0

INSTALL_HELP = (
    "The `claude` CLI (Claude Code) was not found on PATH. Install Claude Code "
    "(https://claude.com/claude-code) and run `claude` once to log in, or use `--backend api` with an Anthropic API key."
)
LOGIN_HELP = "Claude Code is not logged in: run `claude` and use /login (or use `--backend api`)."

_AUTH_MARKERS = ("not logged in", "please run /login", "/login", "invalid api key", "authentication_error",
                 "authentication failed", "invalid bearer token", "oauth token has expired")
_TRANSIENT_MARKERS = ("rate limit", "rate_limit", "overloaded", "529", "timed out", "timeout", "econnreset",
                      "connection error", "internal server error", "service unavailable", "bad gateway")

# Env vars removed from the child so the nested session uses the user's login.
_SCRUB_EXACT = {"CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"}


def scrubbed_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    return {k: v for k, v in env.items() if k not in _SCRUB_EXACT and not k.startswith("CLAUDE_CODE_")}


def _default_runner(cmd: list[str], *, input: str, env: dict, cwd: str, timeout: float) -> Any:
    return subprocess.run(cmd, input=input, env=env, cwd=cwd, timeout=timeout, capture_output=True, text=True, check=False)


class _Transient(Exception):
    def __init__(self, msg: str, retry_after: Optional[float] = None):
        super().__init__(msg)
        self.retry_after = retry_after


def parse_events(stdout: str) -> list[dict]:
    events = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    return events


class ClaudeCodeLLMClient:
    def __init__(
        self,
        config: LLMConfig | None = None,
        runner: Callable[..., Any] | None = None,
        sleep: Any = None,
        claude_bin: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ):
        self.config = config or LLMConfig()
        self._runner = runner or _default_runner
        self._sleep = sleep or time.sleep
        self._claude_bin = claude_bin
        self.timeout_s = timeout_s
        self.calls: list[LLMCall] = []

    # -- command ---------------------------------------------------------
    def _resolve_bin(self) -> str:
        if self._claude_bin:
            return self._claude_bin
        found = shutil.which("claude")
        if not found:
            raise LLMError(INSTALL_HELP)
        return found

    def build_command(self, *, system: str, output_model: Type[T], effort: Optional[str]) -> list[str]:
        import anthropic  # same schema transform as the API client

        schema = anthropic.transform_schema(output_model)
        cmd = [
            self._resolve_bin(), "-p",
            "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
            "--model", self.config.model,
            "--effort", effort or self.config.effort,
            "--system-prompt", system,
            "--tools", "",
            "--no-session-persistence",
            "--strict-mcp-config",
            "--json-schema", json.dumps(schema, sort_keys=True),
        ]
        return cmd

    def build_env(self, max_tokens: Optional[int] = None) -> dict[str, str]:
        env = scrubbed_env()
        if max_tokens:
            env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_tokens)
        return env

    @staticmethod
    def build_stdin(prompt: str, images: Sequence[ImageInput]) -> str:
        msg = {"type": "user", "message": {"role": "user", "content": _content_blocks(prompt, images)}}
        return json.dumps(msg) + "\n"

    # -- protocol --------------------------------------------------------
    def structured(
        self,
        *,
        task: str,
        system: str,
        prompt: str,
        output_model: Type[T],
        images: Sequence[ImageInput] = (),
        effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> T:
        cmd = self.build_command(system=system, output_model=output_model, effort=effort)
        stdin = self.build_stdin(prompt, images)
        env = self.build_env(max_tokens or self.config.max_tokens)
        log.info("llm call (claude-code) task=%s model=%s images=%d", task, self.config.model, len(images))

        result = self._with_retries(task, cmd, stdin, env)

        usage = result.get("usage") or {}
        self.calls.append(
            LLMCall(
                task=task, system=system, prompt=prompt, output_model=output_model.__name__,
                n_images=len(images), effort=effort or self.config.effort,
                usage={**usage, "total_cost_usd": result.get("total_cost_usd")},
            )
        )
        log.info(
            "llm done task=%s backend=claude-code stop=%s cost_usd=%s duration_ms=%s turns=%s "
            "input_tokens=%s output_tokens=%s cache_read=%s cache_write=%s",
            task, result.get("stop_reason"), result.get("total_cost_usd"), result.get("duration_ms"),
            result.get("num_turns"), usage.get("input_tokens"), usage.get("output_tokens"),
            usage.get("cache_read_input_tokens"), usage.get("cache_creation_input_tokens"),
        )

        stop = result.get("stop_reason")
        if stop == "refusal":
            raise LLMRefusalError(f"{task}: model refused: {result.get('result') or ''}".strip(), category=None)
        if stop == "max_tokens":
            raise LLMTruncatedError(f"{task}: output truncated at max_tokens={max_tokens or self.config.max_tokens}")
        if result.get("is_error"):
            msg = f"{task}: claude reported an error ({result.get('subtype')}): {result.get('result')}"
            if "structured_output" in str(result.get("subtype")):
                raise LLMValidationError(msg)
            raise LLMError(msg)
        so = result.get("structured_output")
        if so is None:
            raise LLMValidationError(f"{task}: no structured_output in claude response (stop_reason={stop})")
        try:
            return output_model.model_validate(so)
        except ValidationError as e:
            raise LLMValidationError(f"{task}: response failed validation: {e}") from e

    # -- running ---------------------------------------------------------
    def _with_retries(self, task: str, cmd: list[str], stdin: str, env: dict) -> dict:
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return self._run_once(task, cmd, stdin, env)
            except _Transient as err:
                if attempt == RETRY_ATTEMPTS:
                    raise LLMError(f"{task}: claude CLI failed after {RETRY_ATTEMPTS} attempts: {err}") from err
                delay = err.retry_after if err.retry_after is not None else RETRY_BASE_DELAY_S * 2 ** (attempt - 1)
                delay = min(delay, RETRY_MAX_DELAY_S)
                log.warning("llm task=%s attempt %d/%d failed (%s); retrying in %.1fs", task, attempt, RETRY_ATTEMPTS, err, delay)
                self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _run_once(self, task: str, cmd: list[str], stdin: str, env: dict) -> dict:
        with tempfile.TemporaryDirectory(prefix="rooted-claude-") as cwd:
            try:
                proc = self._runner(cmd, input=stdin, env=env, cwd=cwd, timeout=self.timeout_s)
            except FileNotFoundError as e:
                raise LLMError(INSTALL_HELP) from e
            except subprocess.TimeoutExpired as e:
                raise _Transient(f"timed out after {self.timeout_s:.0f}s") from e
        stdout = getattr(proc, "stdout", "") or ""
        stderr = getattr(proc, "stderr", "") or ""
        rc = getattr(proc, "returncode", 0)
        results = [e for e in parse_events(stdout) if e.get("type") == "result"]
        result = results[-1] if results else None

        if result is None:
            text = (stderr or stdout).strip()
            self._classify(task, text, None)
            raise LLMError(f"{task}: claude CLI exited with code {rc} and no result: {text[:500]}")

        status = result.get("api_error_status")
        text = str(result.get("result") or "") + " " + stderr
        if result.get("is_error") and result.get("stop_reason") not in ("refusal", "max_tokens"):
            self._classify(task, text, status)
        return result

    def _classify(self, task: str, text: str, status: Any) -> None:
        low = text.lower()
        if status in (401, 403) or any(m in low for m in _AUTH_MARKERS):
            raise LLMError(f"{task}: {LOGIN_HELP} ({text.strip()[:200]})")
        if isinstance(status, int) and (status == 429 or status >= 500):
            raise _Transient(f"API status {status}")
        if any(m in low for m in _TRANSIENT_MARKERS):
            raise _Transient(text.strip()[:200])


def claude_auth_status(runner: Callable[..., Any] | None = None) -> SimpleNamespace:
    """Used by ``rooted doctor``: returns ns(path, version, logged_in, auth_method, error)."""
    run = runner or (lambda cmd, **kw: subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False, **kw))
    path = shutil.which("claude")
    out = SimpleNamespace(path=path, version=None, logged_in=False, auth_method=None, error=None)
    if not path:
        out.error = "claude CLI not found on PATH"
        return out
    try:
        v = run([path, "--version"], env=scrubbed_env())
        out.version = (v.stdout or "").strip()
        a = run([path, "auth", "status"], env=scrubbed_env())
        data = json.loads(a.stdout or "{}")
        out.logged_in = bool(data.get("loggedIn"))
        out.auth_method = data.get("authMethod")
    except Exception as e:  # noqa: BLE001
        out.error = str(e)
    return out
