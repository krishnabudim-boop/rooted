"""Contract tests for the shared skeleton (architect-owned).

If one of these fails, a shared file was changed incompatibly."""

from __future__ import annotations

import importlib

import pytest

from rooted import models as m
from rooted.errors import LLMRefusalError
from rooted.llm import TASK_BRIEF, TASK_CONTENT_PLAN, CachingLLMClient
from tests import factories
from tests.conftest import FakeLLMClient

STUB_MODULES = [
    "rooted.ingest", "rooted.fonts", "rooted.content", "rooted.design", "rooted.visuals",
    "rooted.layout", "rooted.pipeline", "rooted.cache", "rooted.llm", "rooted.errors",
]


@pytest.mark.parametrize("name", STUB_MODULES)
def test_modules_import(name):
    importlib.import_module(name)


@pytest.mark.parametrize("model", m.LLM_OUTPUT_MODELS)
def test_llm_models_have_strict_schemas(model):
    anthropic = pytest.importorskip("anthropic")
    schema = anthropic.transform_schema(model)
    text = str(schema)
    assert "additionalProperties" in text
    # dict-typed fields would produce a schema-valued additionalProperties
    assert "'additionalProperties': {" not in text


def test_factories_roundtrip():
    for obj in (factories.sample_content_doc(), factories.sample_brief(), factories.sample_design_spec(),
                factories.sample_raw_stats(), factories.sample_content_plan(), factories.sample_visual_set(),
                factories.sample_layout_plan()):
        again = type(obj).model_validate_json(obj.model_dump_json())
        assert again == obj


def test_sample_plan_covers_every_block_once():
    doc = factories.sample_content_doc()
    plan = factories.sample_content_plan(doc)
    placed = list(plan.cover.consumed_block_ids)
    for s in plan.sections:
        placed += s.block_ids
        for c in s.callouts:
            placed += c.block_ids
    placed += [d.block_id for d in plan.dropped_blocks]
    assert sorted(placed) == sorted(doc.block_ids)


def test_excerpts_exact():
    doc = factories.sample_content_doc()
    plan = factories.sample_content_plan(doc)
    for s in plan.sections:
        for q in s.pull_quotes:
            assert doc.block(q.excerpt.block_id).text[q.excerpt.start:q.excerpt.end] == q.excerpt.text
        for k in s.key_stats:
            assert k.value in k.source.text


def test_design_spec_lookups():
    spec = factories.sample_design_spec()
    assert spec.style(m.TypeRole.H1).size_pt == 20
    assert spec.color(m.ColorRole.ACCENT) == "#C9A227"
    assert spec.color(m.ColorRole.ACCENT_2) == "#C9A227"  # falls back to accent
    assert spec.template(m.PageType.COVER) is not None
    assert m.PaletteColor(role=m.ColorRole.TEXT, hex="abc").hex == "#AABBCC"


def test_fake_llm_serves_and_records():
    brief = factories.sample_brief()
    llm = FakeLLMClient({TASK_BRIEF: brief.model_dump(mode="json"), TASK_CONTENT_PLAN: LLMRefusalError("no")})
    out = llm.structured(task=TASK_BRIEF, system="s", prompt="p", output_model=m.Brief)
    assert out.target_pages == 8
    assert llm.calls[0].task == TASK_BRIEF
    with pytest.raises(LLMRefusalError):
        llm.structured(task=TASK_CONTENT_PLAN, system="s", prompt="p", output_model=m.ContentPlan)


def test_caching_client(tmp_path):
    inner = FakeLLMClient({TASK_BRIEF: [factories.sample_brief()]})  # only one response available
    cached = CachingLLMClient(inner, tmp_path)
    a = cached.structured(task=TASK_BRIEF, system="s", prompt="p", output_model=m.Brief)
    b = cached.structured(task=TASK_BRIEF, system="s", prompt="p", output_model=m.Brief)
    assert a == b and len(inner.calls) == 1


class _FakeStream:
    def __init__(self, message, sink, kwargs):
        self.message, self.sink, self.kwargs = message, sink, kwargs

    def __enter__(self):
        self.sink.append(self.kwargs)
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class _FakeSDK:
    """Mimics client.messages.stream / client.beta.messages.stream."""

    def __init__(self, message):
        from types import SimpleNamespace

        self.sent: list[dict] = []
        stream = lambda **kw: _FakeStream(message, self.sent, kw)  # noqa: E731
        self.messages = SimpleNamespace(stream=stream)
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=stream))


def _message(text, stop="end_turn"):
    from types import SimpleNamespace

    return SimpleNamespace(stop_reason=stop, stop_details=None, usage=None,
                           content=[SimpleNamespace(type="text", text=text)])


def test_anthropic_client_call_shape():
    pytest.importorskip("anthropic")
    from rooted.llm import AnthropicLLMClient, ImageInput

    sdk = _FakeSDK(_message(factories.sample_brief().model_dump_json()))
    client = AnthropicLLMClient(m.LLMConfig(), client=sdk)
    out = client.structured(task=TASK_BRIEF, system="sys", prompt="p", output_model=m.Brief,
                            images=[ImageInput(data=b"\x89PNG", label="Page 1")])
    assert out.target_pages == 8
    kw = sdk.sent[0]
    assert kw["model"] == "claude-opus-5-5"
    assert kw["fallbacks"] == "default" and kw["betas"] == ["server-side-fallback-2026-07-01"]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["output_config"]["effort"] == "high"
    assert "thinking" not in kw  # always-on adaptive on Opus 5.5
    assert [b["type"] for b in kw["messages"][0]["content"]] == ["text", "image", "text"]


def test_anthropic_client_refusal_and_truncation():
    pytest.importorskip("anthropic")
    from rooted.errors import LLMTruncatedError
    from rooted.llm import AnthropicLLMClient

    for stop, exc in (("refusal", LLMRefusalError), ("max_tokens", LLMTruncatedError)):
        client = AnthropicLLMClient(m.LLMConfig(server_fallbacks=False), client=_FakeSDK(_message("{}", stop)))
        with pytest.raises(exc):
            client.structured(task=TASK_BRIEF, system="s", prompt="p", output_model=m.Brief)
