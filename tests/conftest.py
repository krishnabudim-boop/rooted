"""Shared pytest fixtures (architect-owned; do not edit in work packages).

Package-specific fixtures go in ``tests/<package>/conftest.py``.

Offline by default: ``FakeLLMClient`` serves canned responses per task. No
test may need ANTHROPIC_API_KEY unless marked ``@pytest.mark.live``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, Type, TypeVar, Union

import pytest
from pydantic import BaseModel, ValidationError

from rooted.errors import LLMError, LLMValidationError
from rooted.llm import ImageInput, LLMCall, LLMClient
from tests import factories

T = TypeVar("T", bound=BaseModel)

FIXTURES = Path(__file__).parent / "fixtures"

# A response may be: a model instance, a dict, a JSON string, a Path to a JSON
# file, an Exception instance (raised), a callable(call: LLMCall) -> any of the
# above, or a list of these (consumed one per call, in order).
Response = Union[BaseModel, dict, str, Path, Exception, Callable[[LLMCall], Any], list]


class FakeLLMClient:
    """Drop-in ``LLMClient`` for tests.

    >>> llm = FakeLLMClient({"brief": {"target_pages": 8}})
    >>> brief = parse_brief("8 pages", llm)
    >>> llm.calls[0].task == "brief"

    Unknown tasks fall back to ``tests/fixtures/llm/<task>.json`` if present,
    else raise LLMError (so a missing fixture fails loudly).
    """

    def __init__(self, responses: Optional[dict[str, Response]] = None, fixtures_dir: Path = FIXTURES / "llm"):
        self.responses: dict[str, Response] = dict(responses or {})
        self.fixtures_dir = fixtures_dir
        self.calls: list[LLMCall] = []
        self.images: list[Sequence[ImageInput]] = []

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
        call = LLMCall(task=task, system=system, prompt=prompt, output_model=output_model.__name__,
                       n_images=len(images), effort=effort)
        self.calls.append(call)
        self.images.append(images)

        resp = self._next(task)
        if callable(resp) and not isinstance(resp, (BaseModel, type)):
            resp = resp(call)
        if isinstance(resp, Exception):
            raise resp
        try:
            if isinstance(resp, output_model):
                return resp.model_copy(deep=True)
            if isinstance(resp, BaseModel):
                return output_model.model_validate(resp.model_dump(mode="json"))
            if isinstance(resp, Path):
                return output_model.model_validate_json(resp.read_text("utf-8"))
            if isinstance(resp, str):
                return output_model.model_validate_json(resp)
            return output_model.model_validate(resp)
        except ValidationError as e:
            raise LLMValidationError(f"{task}: fake response failed validation: {e}") from e

    def _next(self, task: str) -> Any:
        if task in self.responses:
            resp = self.responses[task]
            if isinstance(resp, list):
                if not resp:
                    raise LLMError(f"FakeLLMClient: responses for task {task!r} exhausted")
                return resp.pop(0)
            return resp
        path = self.fixtures_dir / f"{task}.json"
        if path.exists():
            return json.loads(path.read_text("utf-8"))
        raise LLMError(f"FakeLLMClient: no response configured for task {task!r}")

    def calls_for(self, task: str) -> list[LLMCall]:
        return [c for c in self.calls if c.task == task]


assert isinstance(FakeLLMClient(), LLMClient)  # protocol conformance


@pytest.fixture
def fake_llm() -> Callable[..., FakeLLMClient]:
    """Factory: ``llm = fake_llm({"content_plan": plan})``."""
    return FakeLLMClient


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    d = tmp_path / "work"
    for sub in ("assets", "thumbs", "visuals", "fonts", "llm_cache", "out"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def content_doc():
    return factories.sample_content_doc()


@pytest.fixture
def brief():
    return factories.sample_brief()


@pytest.fixture
def design_spec():
    return factories.sample_design_spec()


@pytest.fixture
def raw_stats():
    return factories.sample_raw_stats()


@pytest.fixture
def content_plan(content_doc):
    return factories.sample_content_plan(content_doc)


@pytest.fixture
def visual_set(content_doc):
    return factories.sample_visual_set(content_doc)


@pytest.fixture
def layout_plan():
    return factories.sample_layout_plan()


@pytest.fixture(autouse=True)
def _no_api_key(monkeypatch, request):
    """Guarantee offline tests: strip credentials unless the test is marked live."""
    if "live" not in request.keywords:
        for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            monkeypatch.delenv(var, raising=False)


def pytest_collection_modifyitems(config, items):
    run_live = "live" in (config.getoption("-m") or "")
    allow_net = os.environ.get("ROOTED_ALLOW_NETWORK") == "1"
    renderer_ok = _chromium_available()
    for item in items:
        if "live" in item.keywords and not run_live:
            item.add_marker(pytest.mark.skip(reason="live API test; run with -m live"))
        if "network" in item.keywords and not allow_net:
            item.add_marker(pytest.mark.skip(reason="set ROOTED_ALLOW_NETWORK=1"))
        if "render" in item.keywords and not renderer_ok:
            item.add_marker(pytest.mark.skip(reason="Playwright Chromium not available (playwright install chromium)"))


def _chromium_available() -> bool:
    try:
        from rooted.layout.render import chromium_available
    except Exception:
        return False
    return chromium_available()
