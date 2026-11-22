"""Shared exception hierarchy. Owned by the architect (shared skeleton).

The CLI maps these to exit codes (see docs/PLAN.md, "Error handling")."""

from __future__ import annotations


class RootedError(Exception):
    """Base class. ``exit_code`` is used by the CLI."""

    exit_code = 1


class InputError(RootedError):
    """Bad/missing/unreadable input file or brief."""

    exit_code = 2


class StageError(RootedError):
    """A stage failed after retries. Carries the stage name."""

    exit_code = 3

    def __init__(self, stage: str, message: str):
        super().__init__(f"[{stage}] {message}")
        self.stage = stage


class LLMError(RootedError):
    """Any failure in the LLM wrapper."""

    exit_code = 4


class LLMRefusalError(LLMError):
    """Model (and any server-side fallback chain) refused."""

    def __init__(self, message: str, category: str | None = None):
        super().__init__(message)
        self.category = category


class LLMTruncatedError(LLMError):
    """stop_reason == max_tokens; output incomplete."""


class LLMValidationError(LLMError):
    """Output did not validate against the requested Pydantic model."""


class TraceabilityError(RootedError):
    """Content plan violates the no-drop / verbatim invariants and could not be repaired."""

    exit_code = 5


class RenderError(RootedError):
    """Renderer unavailable or failed (e.g. WeasyPrint native libs missing)."""

    exit_code = 6
