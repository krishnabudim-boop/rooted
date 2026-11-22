"""AnthropicLLMClient / CachingLLMClient hardening, offline with a fake SDK client."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import anthropic
import pytest
from pydantic import BaseModel

from rooted.errors import LLMError, LLMRefusalError, LLMTruncatedError, LLMValidationError
from rooted.llm import AUTH_HELP, AnthropicLLMClient, CachingLLMClient, ImageInput
from rooted.models import LLMConfig
from tests.conftest import FakeLLMClient

try:  # the SDK's HTTP layer is httpx (older) or httpx2 (newer)
    import httpx
except ImportError:  # pragma: no cover
    import httpx2 as httpx


class Out(BaseModel):
    answer: str


def _msg(text='{"answer": "hi"}', stop="end_turn", **kw):
    usage = SimpleNamespace(to_dict=lambda: {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 2})
    content = [SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text=text)] if text is not None else []
    return SimpleNamespace(content=content, stop_reason=stop, usage=usage, _request_id="req_1", **kw)


class FakeSDK:
    """Mimics client.messages.stream / client.beta.messages.stream."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []  # (path, kwargs)
        self.messages = SimpleNamespace(stream=lambda **k: self._stream("plain", k))
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=lambda **k: self._stream("beta", k)))

    @contextmanager
    def _stream(self, path, kwargs):
        self.calls.append((path, kwargs))
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        yield SimpleNamespace(get_final_message=lambda: item)


def _resp(status, headers=None):
    return httpx.Response(status, request=httpx.Request("POST", "http://x"), headers=headers or {})


def _status_err(cls, status, headers=None):
    return cls("boom", response=_resp(status, headers), body=None)


def _client(script, **cfg):
    sdk = FakeSDK(script)
    sleeps = []
    c = AnthropicLLMClient(LLMConfig(**cfg), client=sdk, sleep=sleeps.append)
    return c, sdk, sleeps


def _call(c, **kw):
    return c.structured(task="brief", system="sys", prompt="p", output_model=Out, **kw)


def test_success_path_params_and_usage():
    c, sdk, _ = _client([_msg()])
    out = _call(c, images=[ImageInput(b"x", label="Page 1")], effort="low")
    assert out == Out(answer="hi")
    path, kw = sdk.calls[0]
    assert path == "beta" and kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["model"] == "claude-opus-5-5"
    assert "thinking" not in kw and "temperature" not in kw
    assert kw["output_config"]["effort"] == "low"
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["messages"][-1]["role"] == "user"  # no prefill
    assert c.calls[0].usage["input_tokens"] == 10


def test_no_fallbacks_uses_plain_stream():
    c, sdk, _ = _client([_msg()], server_fallbacks=False)
    _call(c)
    assert sdk.calls[0][0] == "plain" and "fallbacks" not in sdk.calls[0][1]


def test_rate_limit_retries_with_retry_after_then_succeeds():
    err = _status_err(anthropic.RateLimitError, 429, {"retry-after": "7"})
    c, sdk, sleeps = _client([err, err, _msg()])
    assert _call(c) == Out(answer="hi")
    assert sleeps == [7.0, 7.0] and len(sdk.calls) == 3


def test_backoff_exponential_for_5xx_and_connection_errors():
    errs = [
        _status_err(anthropic.InternalServerError, 529),
        anthropic.APIConnectionError(request=httpx.Request("POST", "http://x")),
    ]
    c, _, sleeps = _client([*errs, _msg()])
    _call(c)
    assert sleeps == [2.0, 4.0]


def test_retries_exhausted_raises_llm_error():
    err = _status_err(anthropic.RateLimitError, 429)
    c, sdk, _ = _client([err, err, err])
    with pytest.raises(LLMError, match="rate limited"):
        _call(c)
    assert len(sdk.calls) == 3


def test_timeout_message():
    err = anthropic.APITimeoutError(request=httpx.Request("POST", "http://x"))
    c, _, _ = _client([err, err, err])
    with pytest.raises(LLMError, match="timed out"):
        _call(c)


@pytest.mark.parametrize(
    "cls,status,needle",
    [
        (anthropic.AuthenticationError, 401, "ant auth login"),
        (anthropic.PermissionDeniedError, 403, "permission"),
        (anthropic.NotFoundError, 404, "not found"),
        (anthropic.BadRequestError, 400, "bad request"),
    ],
)
def test_non_retryable_errors_fail_fast(cls, status, needle):
    c, sdk, sleeps = _client([_status_err(cls, status)])
    with pytest.raises(LLMError, match=needle):
        _call(c)
    assert len(sdk.calls) == 1 and sleeps == []


def test_fallback_rejected_retries_once_without_beta():
    err = _status_err(anthropic.BadRequestError, 400)
    err.args = ("fallbacks not supported",)
    err.message = "fallbacks not supported"
    c, sdk, _ = _client([err, _msg()])
    assert _call(c) == Out(answer="hi")
    assert [p for p, _ in sdk.calls] == ["beta", "plain"]


def test_missing_credentials_friendly_error():
    c, _, _ = _client([TypeError("Could not resolve authentication method. Expected one of api_key ...")])
    with pytest.raises(LLMError) as ei:
        _call(c)
    assert str(ei.value) == AUTH_HELP
    assert "ant auth login" in AUTH_HELP and "ANTHROPIC_API_KEY" in AUTH_HELP


def test_refusal():
    c, _, _ = _client(
        [_msg(text=None, stop="refusal", stop_details=SimpleNamespace(explanation="nope", category="cyber"))]
    )
    with pytest.raises(LLMRefusalError) as ei:
        _call(c)
    assert ei.value.category == "cyber"


def test_max_tokens_truncation():
    c, _, _ = _client([_msg(text='{"answ', stop="max_tokens")])
    with pytest.raises(LLMTruncatedError):
        _call(c, max_tokens=100)


def test_context_window_exceeded():
    c, _, _ = _client([_msg(text=None, stop="model_context_window_exceeded")])
    with pytest.raises(LLMError, match="context window"):
        _call(c)


def test_invalid_json_and_missing_text():
    c, _, _ = _client([_msg(text='{"wrong": 1}')])
    with pytest.raises(LLMValidationError):
        _call(c)
    c, _, _ = _client([_msg(text=None)])
    with pytest.raises(LLMError, match="no text"):
        _call(c)


# ---- disk cache ----


def test_cache_hit_miss_and_key_sensitivity(tmp_path):
    inner = FakeLLMClient({"brief": [Out(answer="a"), Out(answer="b"), Out(answer="c")]})
    c = CachingLLMClient(inner, tmp_path / "cache", model_name="m")
    kw = dict(task="brief", system="s", prompt="p", output_model=Out)
    assert c.structured(**kw).answer == "a"
    assert c.structured(**kw).answer == "a"
    assert (c.hits, c.misses) == (1, 1)
    assert c.structured(**{**kw, "prompt": "q"}).answer == "b"
    assert c.structured(**{**kw, "effort": "low"}).answer == "c"
    assert len(inner.calls) == 3
    assert not list((tmp_path / "cache").glob("*.tmp"))


def test_cache_image_hash_in_key(tmp_path):
    inner = FakeLLMClient({"brief": [Out(answer="a"), Out(answer="b")]})
    c = CachingLLMClient(inner, tmp_path)
    kw = dict(task="brief", system="s", prompt="p", output_model=Out)
    assert c.structured(**kw, images=[ImageInput(b"1")]).answer == "a"
    assert c.structured(**kw, images=[ImageInput(b"2")]).answer == "b"


def test_corrupt_cache_entry_is_recomputed(tmp_path):
    inner = FakeLLMClient({"brief": [Out(answer="a"), Out(answer="fresh")]})
    c = CachingLLMClient(inner, tmp_path)
    kw = dict(task="brief", system="s", prompt="p", output_model=Out)
    c.structured(**kw)
    (next(tmp_path.glob("brief-*.json"))).write_text("{garbage")
    assert c.structured(**kw).answer == "fresh"


def test_errors_are_not_cached(tmp_path):
    inner = FakeLLMClient({"brief": [LLMError("x"), Out(answer="ok")]})
    c = CachingLLMClient(inner, tmp_path)
    kw = dict(task="brief", system="s", prompt="p", output_model=Out)
    with pytest.raises(LLMError):
        c.structured(**kw)
    assert c.structured(**kw).answer == "ok"


def _fb_msg(*blocks):
    usage = SimpleNamespace(to_dict=lambda: {"input_tokens": 1, "output_tokens": 1})
    content = [SimpleNamespace(type="fallback") if b is None else SimpleNamespace(type="text", text=b) for b in blocks]
    return SimpleNamespace(content=content, stop_reason="end_turn", usage=usage, _request_id="r")


def test_mid_stream_fallback_joins_text_blocks():
    c, _, _ = _client([_fb_msg('{"answ', None, 'er": "hi"}')])
    assert _call(c).answer == "hi"


def test_mid_stream_fallback_uses_text_after_last_fallback():
    c, _, _ = _client([_fb_msg('{"answer": "par', None, '{"answer": "full"}')])
    assert _call(c).answer == "full"


def test_mid_stream_fallback_unparseable_raises():
    c, _, _ = _client([_fb_msg("garbage", None, "more garbage")])
    with pytest.raises(LLMValidationError):
        _call(c)
