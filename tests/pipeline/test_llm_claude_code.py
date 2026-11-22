"""ClaudeCodeLLMClient with a fake runner and recorded JSONL; plus one live test."""

from __future__ import annotations

import base64
import io
import json
import subprocess
from types import SimpleNamespace

import anthropic
import pytest
from pydantic import BaseModel
from typer.testing import CliRunner

import rooted.cli as cli
from rooted import llm_claude_code as lcc
from rooted.errors import LLMError, LLMRefusalError, LLMTruncatedError, LLMValidationError
from rooted.llm import AnthropicLLMClient, CachingLLMClient, ImageInput, build_client
from rooted.llm_claude_code import ClaudeCodeLLMClient
from rooted.models import LLMConfig


class Out(BaseModel):
    answer: str


def _jsonl(**kw) -> str:
    base = {
        "type": "result", "subtype": "success", "is_error": False, "result": '{"answer":"hi"}',
        "structured_output": {"answer": "hi"}, "stop_reason": "end_turn", "api_error_status": None,
        "total_cost_usd": 0.0123, "duration_ms": 1500, "num_turns": 1,
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 2},
    }
    base.update(kw)
    events = [
        {"type": "system", "subtype": "init", "session_id": "s"},
        {"type": "assistant", "message": {"role": "assistant", "content": []}},
        {"type": "rate_limit_event"},
        base,
    ]
    return "not json noise\n" + "\n".join(json.dumps(e) for e in events) + "\n"


class FakeRunner:
    def __init__(self, *script):
        self.script = list(script)
        self.calls = []

    def __call__(self, cmd, *, input, env, cwd, timeout):
        self.calls.append(SimpleNamespace(cmd=cmd, input=input, env=env, cwd=cwd, timeout=timeout))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            item = SimpleNamespace(returncode=0, stdout=item, stderr="")
        return item


def _client(*script, **cfg):
    runner = FakeRunner(*script)
    sleeps: list[float] = []
    c = ClaudeCodeLLMClient(LLMConfig(**cfg), runner=runner, sleep=sleeps.append, claude_bin="/bin/claude")
    return c, runner, sleeps


def _call(c, **kw):
    return c.structured(task="brief", system="SYS", prompt="hello", output_model=Out, **kw)


def test_success_and_command():
    c, r, _ = _client(_jsonl(), model="claude-opus-5-5", effort="high")
    assert _call(c) == Out(answer="hi")
    call = r.calls[0]
    cmd = call.cmd
    assert cmd[:2] == ["/bin/claude", "-p"]
    flag = lambda f: cmd[cmd.index(f) + 1]  # noqa: E731
    assert flag("--input-format") == "stream-json" and flag("--output-format") == "stream-json"
    assert "--verbose" in cmd and "--no-session-persistence" in cmd and "--strict-mcp-config" in cmd
    assert flag("--model") == "claude-opus-5-5" and flag("--effort") == "high"
    assert flag("--system-prompt") == "SYS" and flag("--tools") == ""
    assert "--bare" not in cmd
    assert json.loads(flag("--json-schema")) == anthropic.transform_schema(Out)
    assert call.timeout == 900.0
    assert c.calls[0].usage["total_cost_usd"] == 0.0123


def test_effort_override_and_max_tokens_env():
    c, r, _ = _client(_jsonl())
    _call(c, effort="low", max_tokens=1234)
    assert r.calls[0].cmd[r.calls[0].cmd.index("--effort") + 1] == "low"
    assert r.calls[0].env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "1234"


def test_env_scrubbing_and_cwd(monkeypatch):
    for k in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("KEEP_ME", "1")
    c, r, _ = _client(_jsonl())
    _call(c)
    env = r.calls[0].env
    assert "KEEP_ME" in env and "PATH" in env
    for k in ("CLAUDECODE", "CLAUDE_PID", "CLAUDE_EFFORT", "CLAUDE_CODE_ENTRYPOINT", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        assert k not in env
    assert "rooted-claude-" in r.calls[0].cwd


def test_images_on_stdin():
    c, r, _ = _client(_jsonl())
    _call(c, images=[ImageInput(data=b"\x89PNGxx", label="Page 1")])
    lines = r.calls[0].input.strip().splitlines()
    assert len(lines) == 1
    msg = json.loads(lines[0])
    assert msg["type"] == "user" and msg["message"]["role"] == "user"
    blocks = msg["message"]["content"]
    assert blocks[0] == {"type": "text", "text": "Page 1"}
    assert blocks[1]["source"] == {"type": "base64", "media_type": "image/png", "data": base64.b64encode(b"\x89PNGxx").decode()}
    assert blocks[-1] == {"type": "text", "text": "hello"}


def test_refusal_and_truncation():
    c, _, _ = _client(_jsonl(stop_reason="refusal", structured_output=None))
    with pytest.raises(LLMRefusalError):
        _call(c)
    c, _, _ = _client(_jsonl(stop_reason="max_tokens", structured_output=None))
    with pytest.raises(LLMTruncatedError):
        _call(c)


def test_validation_failures():
    for kw in ({"structured_output": None}, {"structured_output": {"wrong": 1}}):
        c, _, _ = _client(_jsonl(**kw))
        with pytest.raises(LLMValidationError):
            _call(c)
    c, _, _ = _client(_jsonl(is_error=True, subtype="error_max_structured_output_retries", structured_output=None))
    with pytest.raises(LLMValidationError):
        _call(c)
    c, _, _ = _client(_jsonl(is_error=True, subtype="error_during_execution", result="weird", structured_output=None))
    with pytest.raises(LLMError) as ei:
        _call(c)
    assert not isinstance(ei.value, LLMValidationError)


def test_not_installed(monkeypatch):
    monkeypatch.setattr(lcc.shutil, "which", lambda _: None)
    c = ClaudeCodeLLMClient(LLMConfig(), runner=FakeRunner())
    with pytest.raises(LLMError, match="--backend api"):
        _call(c)
    c = ClaudeCodeLLMClient(LLMConfig(), runner=FakeRunner(FileNotFoundError()), claude_bin="/x/claude")
    with pytest.raises(LLMError, match="Install Claude Code"):
        _call(c)


def test_auth_errors_not_retried():
    c, r, _ = _client(_jsonl(is_error=True, result="Invalid API key - Please run /login", api_error_status=401,
                         structured_output=None))
    with pytest.raises(LLMError, match="/login"):
        _call(c)
    assert len(r.calls) == 1
    bad = SimpleNamespace(returncode=1, stdout="", stderr="Not logged in. Please run /login")
    c, _, _ = _client(bad)
    with pytest.raises(LLMError, match="/login"):
        _call(c)


@pytest.mark.parametrize("status,text", [
    (429, "rate limited"), (529, "Overloaded"), (500, "oops"), (None, "API Error: Request timed out"),
])
def test_transient_retried_then_success(status, text):
    fail = _jsonl(is_error=True, result=text, api_error_status=status, structured_output=None)
    c, r, sleeps = _client(fail, fail, _jsonl())
    assert _call(c) == Out(answer="hi")
    assert len(r.calls) == 3 and sleeps == [2.0, 4.0]


def test_timeout_retried_then_gives_up():
    t = subprocess.TimeoutExpired("claude", 900)
    c, r, sleeps = _client(t, t, t)
    with pytest.raises(LLMError, match="after 3 attempts"):
        _call(c)
    assert len(r.calls) == 3 and len(sleeps) == 2


def test_no_result_event():
    c, _, _ = _client(SimpleNamespace(returncode=1, stdout="", stderr="boom"))
    with pytest.raises(LLMError, match="boom"):
        _call(c)


# ---- backend selection / cache ------------------------------------------------

def test_default_backend_and_env(monkeypatch):
    monkeypatch.delenv("ROOTED_LLM_BACKEND", raising=False)
    assert LLMConfig().backend == "claude-code"
    monkeypatch.setenv("ROOTED_LLM_BACKEND", "api")
    assert LLMConfig().backend == "api"
    assert LLMConfig(backend="claude-code").backend == "claude-code"


def test_build_client_selection_and_cache_separation(tmp_path, monkeypatch):
    monkeypatch.delenv("ROOTED_LLM_BACKEND", raising=False)
    monkeypatch.setattr(anthropic, "Anthropic", lambda **k: object())
    cc = build_client(LLMConfig(), tmp_path)
    api = build_client(LLMConfig(backend="api"), tmp_path)
    assert isinstance(cc, CachingLLMClient) and isinstance(cc.inner, ClaudeCodeLLMClient)
    assert isinstance(api, CachingLLMClient) and isinstance(api.inner, AnthropicLLMClient)
    args = ("brief", "s", "p", Out, ())
    assert cc._key(*args) != api._key(*args)
    assert isinstance(build_client(LLMConfig(response_cache=False)), ClaudeCodeLLMClient)


# ---- doctor / cli ----------------------------------------------------------------

def _status(monkeypatch, stdout, version="2.1.330 (Claude Code)", which="/bin/claude"):
    monkeypatch.setattr(lcc.shutil, "which", lambda _: which)

    def run(cmd, **kw):
        out = version if "--version" in cmd else stdout
        return SimpleNamespace(returncode=0, stdout=out, stderr="")

    monkeypatch.setattr(lcc.subprocess, "run", lambda cmd, **kw: run(cmd))


def test_doctor_claude_check(monkeypatch):
    _status(monkeypatch, json.dumps({"loggedIn": True, "authMethod": "claude.ai"}))
    s, d = cli._check_claude_cli()
    assert s == cli.OK and "2.1.330" in d and "claude.ai" in d
    _status(monkeypatch, json.dumps({"loggedIn": False}))
    s, d = cli._check_claude_cli()
    assert s == cli.FAIL and "/login" in d
    _status(monkeypatch, "", which=None)
    s, d = cli._check_claude_cli()
    assert s == cli.WARN and "--backend api" in d


def test_doctor_credentials_informational(monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda _: None)
    s, d = cli._check_credentials()
    assert s == cli.OK and "not needed" in d


def test_cli_rejects_bad_backend(tmp_path):
    f = tmp_path / "a.docx"
    f.write_bytes(b"x")
    r = CliRunner().invoke(cli.app, ["build", str(f), "-m", str(f), "--backend", "nope"])
    assert r.exit_code == 2


# ---- live ----------------------------------------------------------------------------

def _tiny_png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (255, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


class Color(BaseModel):
    color: str


@pytest.mark.live
def test_live_claude_code_vision():
    c = ClaudeCodeLLMClient(LLMConfig(model="sonnet", effort="low", max_tokens=2000))
    out = c.structured(
        task="live", system="You answer with JSON only.", prompt="What is the dominant color of this image? One lowercase word.",
        output_model=Color, images=[ImageInput(data=_tiny_png())],
    )
    assert "red" in out.color.lower()
    print("cost", c.calls[0].usage.get("total_cost_usd"))
