"""The ONE place Rooted talks to Claude.

Every stage receives an ``LLMClient`` (dependency injection) and calls
``structured(...)``. Tests pass ``FakeLLMClient`` (tests/conftest.py); the CLI
builds ``AnthropicLLMClient`` (optionally wrapped in ``CachingLLMClient``).

The interface (``LLMClient``, ``ImageInput``, task names) is frozen shared
skeleton. The implementation below is a short working version; WP6 owns
hardening it (see docs/PLAN.md).

SDK call pattern (verified against the bundled claude-api reference, Nov 2026):

* Model ``claude-opus-5-5``: thinking is always on (adaptive). Do NOT send
  ``thinking={"type": "disabled"}`` or ``budget_tokens`` (400). Control depth
  with ``output_config={"effort": ...}``.
* Structured output: ``output_config={"format": {"type": "json_schema",
  "schema": anthropic.transform_schema(Model)}, "effort": ...}``; the first
  text block is guaranteed-valid JSON -> ``Model.model_validate_json``. (Plain
  ``client.messages.parse(output_format=Model)`` is equivalent on the non-beta
  path; we use the explicit form so the beta/fallback path is identical.)
* Streaming: ``with client.messages.stream(...) as s: msg = s.get_final_message()``
  (avoids the SDK's long-request guard and HTTP idle timeouts).
* Server-side refusal fallbacks: ``client.beta.messages.stream(...,
  betas=["server-side-fallback-2026-07-01"], fallbacks="default")``. A final
  ``stop_reason == "refusal"`` means the whole chain refused.
* Prompt caching: system prompt as a text block with
  ``cache_control={"type": "ephemeral"}``; keep system prompts byte-stable
  (no timestamps, sorted JSON).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol, Sequence, Type, TypeVar, runtime_checkable

from pydantic import BaseModel, ValidationError

from rooted.errors import LLMError, LLMRefusalError, LLMTruncatedError, LLMValidationError
from rooted.models import LLMConfig

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Task names: used for logging, response caching and FakeLLMClient fixture lookup.
TASK_BRIEF = "brief"
TASK_CONTENT_PLAN = "content_plan"
TASK_DESIGN_VISION = "design_vision"
TASK_VISUAL_PLAN = "visual_plan"
ALL_TASKS = (TASK_BRIEF, TASK_CONTENT_PLAN, TASK_DESIGN_VISION, TASK_VISUAL_PLAN)


@dataclass(frozen=True)
class ImageInput:
    """A PNG/JPEG to show the model (vision)."""

    data: bytes
    media_type: str = "image/png"
    label: str = ""  # emitted as a text block before the image, e.g. "Page 3"

    @classmethod
    def from_path(cls, path: Path | str, label: str = "") -> ImageInput:
        p = Path(path)
        mt = "image/jpeg" if p.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        return cls(data=p.read_bytes(), media_type=mt, label=label)


@dataclass
class LLMCall:
    """Record of one call (FakeLLMClient keeps these for assertions)."""

    task: str
    system: str
    prompt: str
    output_model: str
    n_images: int
    effort: Optional[str]
    usage: dict = field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    """What every stage depends on. Implementations must be safe to call
    sequentially; concurrency is not required."""

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
        """Return a validated ``output_model`` instance.

        Raises LLMRefusalError, LLMTruncatedError, LLMValidationError, LLMError.
        """
        ...


def _content_blocks(prompt: str, images: Sequence[ImageInput]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    for img in images:
        if img.label:
            blocks.append({"type": "text", "text": img.label})
        blocks.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": img.media_type,
                    "data": base64.standard_b64encode(img.data).decode("ascii"),
                },
            }
        )
    blocks.append({"type": "text", "text": prompt})
    return blocks


AUTH_HELP = (
    "No Anthropic credentials found. Run `ant auth login`, or set the ANTHROPIC_API_KEY environment variable "
    "(get a key at https://console.anthropic.com/)."
)

# Backoff for retryable errors, on top of the SDK's own max_retries.
RETRY_ATTEMPTS = 3
RETRY_BASE_DELAY_S = 2.0
RETRY_MAX_DELAY_S = 60.0


def _retry_after(exc: Any) -> Optional[float]:
    """Seconds from a ``retry-after`` header, if the exception carries one."""
    try:
        return float(exc.response.headers.get("retry-after"))
    except (AttributeError, TypeError, ValueError):
        return None


def _usage_dict(message: Any) -> dict:
    usage = getattr(message, "usage", None)
    if usage is None:
        return {}
    if hasattr(usage, "to_dict"):
        try:
            return dict(usage.to_dict())
        except Exception:  # pragma: no cover - defensive
            return {}
    return {}


class AnthropicLLMClient:
    """Real client. Credentials are resolved by the zero-arg SDK client
    (``ANTHROPIC_API_KEY``, ``ant auth login`` profile, ...)."""

    def __init__(self, config: LLMConfig | None = None, client: Any = None, sleep: Any = None):
        self.config = config or LLMConfig()
        if client is None:
            import anthropic  # local import: tests never need the SDK configured

            client = anthropic.Anthropic(timeout=self.config.timeout_s, max_retries=self.config.max_retries)
        self._client = client
        self._sleep = sleep or time.sleep
        self.calls: list[LLMCall] = []

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
        import anthropic

        cfg = self.config
        params: dict[str, Any] = dict(
            model=cfg.model,
            max_tokens=max_tokens or cfg.max_tokens,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": _content_blocks(prompt, images)}],
            output_config={
                "effort": effort or cfg.effort,
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(output_model)},
            },
        )
        log.info("llm call task=%s model=%s images=%d", task, cfg.model, len(images))
        use_fallbacks = cfg.server_fallbacks
        try:
            message = self._call_with_retries(task, params, use_fallbacks)
        except anthropic.BadRequestError as e:
            # If the fallback beta is not available for this model/account, retry once without it.
            if use_fallbacks and "fallback" in str(e).lower():
                log.warning("server-side fallbacks rejected (%s); retrying without", e)
                message = self._guarded(task, lambda: self._call_with_retries(task, params, False))
            else:
                raise LLMError(f"{task}: bad request: {e}") from e
        except TypeError as e:
            # The SDK raises TypeError when no credential could be resolved.
            if "authentication" in str(e).lower():
                raise LLMError(AUTH_HELP) from e
            raise
        except Exception as e:
            raise self._translate(task, e) from e

        usage = _usage_dict(message)
        self.calls.append(
            LLMCall(
                task=task,
                system=system,
                prompt=prompt,
                output_model=output_model.__name__,
                n_images=len(images),
                effort=effort or cfg.effort,
                usage=usage,
            )
        )
        log.info(
            "llm done task=%s request_id=%s stop=%s input_tokens=%s output_tokens=%s cache_read=%s cache_write=%s",
            task,
            getattr(message, "_request_id", None),
            message.stop_reason,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
            usage.get("cache_read_input_tokens"),
            usage.get("cache_creation_input_tokens"),
        )

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise LLMRefusalError(
                f"{task}: model refused: {getattr(details, 'explanation', '')}",
                category=getattr(details, "category", None),
            )
        if message.stop_reason == "max_tokens":
            raise LLMTruncatedError(f"{task}: output truncated at max_tokens={params['max_tokens']}")
        if message.stop_reason == "model_context_window_exceeded":
            raise LLMError(f"{task}: input exceeds the model's context window")
        # A mid-stream decline keeps the partial text, inserts a ``fallback`` block, and the fallback
        # model continues; so the answer is every text block in order, or failing that the text after
        # the last fallback block.
        texts: list[str] = []
        after_fallback: list[str] = []
        for b in message.content:
            if b.type == "fallback":
                after_fallback = []
            elif b.type == "text":
                texts.append(b.text)
                after_fallback.append(b.text)
        if not texts:
            raise LLMError(f"{task}: no text block in response (stop_reason={message.stop_reason})")
        try:
            return output_model.model_validate_json("".join(texts))
        except ValidationError as e:
            if len(after_fallback) < len(texts):
                try:
                    return output_model.model_validate_json("".join(after_fallback))
                except ValidationError:
                    pass
            raise LLMValidationError(f"{task}: response failed validation: {e}") from e

    def _guarded(self, task: str, fn: Any) -> Any:
        try:
            return fn()
        except Exception as e:
            raise self._translate(task, e) from e

    def _translate(self, task: str, e: Exception) -> Exception:
        """Map SDK exceptions (most specific first) to LLMError with actionable text."""
        import anthropic

        if isinstance(e, LLMError):
            return e
        if isinstance(e, TypeError) and "authentication" in str(e).lower():
            return LLMError(AUTH_HELP)
        if isinstance(e, anthropic.AuthenticationError):
            return LLMError(f"{task}: authentication failed ({e}). {AUTH_HELP}")
        if isinstance(e, anthropic.PermissionDeniedError):
            return LLMError(f"{task}: this credential lacks permission for model {self.config.model}: {e}")
        if isinstance(e, anthropic.NotFoundError):
            return LLMError(f"{task}: model or endpoint not found ({self.config.model}): {e}")
        if isinstance(e, anthropic.BadRequestError):
            return LLMError(f"{task}: bad request: {e}")
        if isinstance(e, anthropic.RateLimitError):
            return LLMError(f"{task}: rate limited after {RETRY_ATTEMPTS} attempts: {e}")
        if isinstance(e, anthropic.APITimeoutError):
            return LLMError(f"{task}: request timed out after {self.config.timeout_s:.0f}s: {e}")
        if isinstance(e, anthropic.APIConnectionError):
            return LLMError(f"{task}: network error, check your connection: {e}")
        if isinstance(e, anthropic.APIStatusError):
            return LLMError(f"{task}: API error {e.status_code}: {e}")
        if isinstance(e, anthropic.APIError):
            return LLMError(f"{task}: API error: {e}")
        return LLMError(f"{task}: unexpected error: {e}")

    def _call_with_retries(self, task: str, params: dict[str, Any], use_fallbacks: bool) -> Any:
        """Retry rate limits, timeouts, connection drops and 5xx/529 with
        exponential backoff (honoring retry-after). Everything else propagates."""
        import anthropic

        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                return self._stream(params, use_fallbacks=use_fallbacks)
            except (anthropic.RateLimitError, anthropic.APITimeoutError, anthropic.APIConnectionError) as e:
                err: Exception = e
            except anthropic.APIStatusError as e:
                if e.status_code < 500:
                    raise
                err = e
            if attempt == RETRY_ATTEMPTS:
                raise err
            delay = _retry_after(err)
            if delay is None:
                delay = RETRY_BASE_DELAY_S * 2 ** (attempt - 1)
            delay = min(delay, RETRY_MAX_DELAY_S)
            log.warning(
                "llm task=%s attempt %d/%d failed (%s); retrying in %.1fs",
                task, attempt, RETRY_ATTEMPTS, type(err).__name__, delay,
            )
            self._sleep(delay)
        raise AssertionError("unreachable")  # pragma: no cover

    def _stream(self, params: dict[str, Any], *, use_fallbacks: bool) -> Any:
        if use_fallbacks:
            with self._client.beta.messages.stream(
                **params, betas=[self.config.fallback_beta], fallbacks="default"
            ) as stream:
                return stream.get_final_message()
        with self._client.messages.stream(**params) as stream:
            return stream.get_final_message()


class CachingLLMClient:
    """Disk cache keyed by sha256(task, model, system, prompt, image hashes,
    output schema, effort, max_tokens). Lets ``--from <stage>`` re-runs skip
    identical calls. Corrupt entries are ignored; writes are atomic."""

    def __init__(self, inner: LLMClient, cache_dir: Path, model_name: str = ""):
        self.inner = inner
        self.cache_dir = Path(cache_dir)
        self.model_name = model_name
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.hits = 0
        self.misses = 0

    def _key(
        self,
        task: str,
        system: str,
        prompt: str,
        output_model: Type[BaseModel],
        images: Sequence[ImageInput],
        effort: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        h = hashlib.sha256()
        parts = (
            task,
            self.model_name,
            system,
            prompt,
            json.dumps(output_model.model_json_schema(), sort_keys=True),
            str(effort),
            str(max_tokens),
        )
        for part in parts:
            h.update(part.encode("utf-8"))
            h.update(b"\x00")
        for img in images:
            h.update(hashlib.sha256(img.data).digest())
        return h.hexdigest()

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
        key = self._key(task, system, prompt, output_model, images, effort, max_tokens)
        path = self.cache_dir / f"{task}-{key[:24]}.json"
        if path.exists():
            try:
                result = output_model.model_validate_json(path.read_text("utf-8"))
            except (ValidationError, OSError, ValueError):
                log.warning("llm cache entry %s unreadable; recomputing", path.name)
            else:
                self.hits += 1
                log.info("llm cache hit task=%s", task)
                return result
        self.misses += 1
        result = self.inner.structured(
            task=task, system=system, prompt=prompt, output_model=output_model,
            images=images, effort=effort, max_tokens=max_tokens,
        )
        tmp = path.with_suffix(".tmp")
        tmp.write_text(result.model_dump_json(indent=2), "utf-8")
        tmp.replace(path)
        return result


def build_client(config: LLMConfig, cache_dir: Path | None = None) -> LLMClient:
    """Factory used by the pipeline."""
    client: LLMClient = AnthropicLLMClient(config)
    if config.response_cache and cache_dir is not None:
        client = CachingLLMClient(client, cache_dir, model_name=config.model)
    return client
