"""Shared LLM-call helper with the bounded retry policy from PLAN section 8."""

from __future__ import annotations

from typing import Optional, Type, TypeVar

from pydantic import BaseModel

from rooted.errors import LLMTruncatedError, LLMValidationError
from rooted.llm import LLMClient

T = TypeVar("T", bound=BaseModel)

DEFAULT_MAX_TOKENS = 32000
MAX_TOKENS_CAP = 64000


def structured_with_retry(
    llm: LLMClient,
    *,
    task: str,
    system: str,
    prompt: str,
    output_model: Type[T],
    effort: Optional[str] = None,
    max_tokens: Optional[int] = None,
) -> T:
    """Call ``llm.structured``; retry once on truncation (``max_tokens`` x2,
    capped) and once on a validation failure (error appended to the prompt).
    Refusals and other errors propagate. At most 3 calls."""
    attempts = 0
    truncated_retry = False
    validation_retry = False
    cur_prompt = prompt
    cur_max = max_tokens
    while True:
        attempts += 1
        try:
            return llm.structured(
                task=task,
                system=system,
                prompt=cur_prompt,
                output_model=output_model,
                effort=effort,
                max_tokens=cur_max,
            )
        except LLMTruncatedError:
            if truncated_retry or attempts >= 3:
                raise
            truncated_retry = True
            cur_max = min((cur_max or DEFAULT_MAX_TOKENS) * 2, MAX_TOKENS_CAP)
        except LLMValidationError as e:
            if validation_retry or attempts >= 3:
                raise
            validation_retry = True
            cur_prompt = f"{prompt}\n\nYour previous output failed validation: {e}\nReturn corrected JSON."
