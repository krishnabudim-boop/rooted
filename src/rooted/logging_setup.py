"""Logging configuration (WP6): rich console handler + <work_dir>/rooted.log."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

_console = Console(stderr=True)
_OWNED = "_rooted_owned"


def get_console() -> Console:
    """Shared stderr console (progress, status, log lines)."""
    return _console


def setup_logging(level: str = "INFO", log_file: Path | None = None) -> None:
    """Configure the ``rooted`` logger. Idempotent: handlers from earlier
    calls are replaced. The file log always records DEBUG detail (LLM request
    ids, token usage); the console follows ``level``."""
    lvl = getattr(logging, str(level).upper(), logging.INFO)
    logger = logging.getLogger("rooted")
    for h in list(logger.handlers):
        if getattr(h, _OWNED, False):
            logger.removeHandler(h)
            h.close()
    logger.setLevel(logging.DEBUG)

    console_handler = RichHandler(console=_console, show_path=False, rich_tracebacks=False, markup=False)
    console_handler.setLevel(lvl)
    setattr(console_handler, _OWNED, True)
    logger.addHandler(console_handler)

    if log_file is not None:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        setattr(fh, _OWNED, True)
        logger.addHandler(fh)
