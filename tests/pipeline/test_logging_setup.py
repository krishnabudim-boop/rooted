from __future__ import annotations

import logging

from rooted.logging_setup import setup_logging


def test_setup_logging_is_idempotent_and_writes_file(tmp_path):
    log_file = tmp_path / "sub" / "rooted.log"
    setup_logging("WARNING", log_file)
    setup_logging("INFO", log_file)
    logger = logging.getLogger("rooted")

    def owned():
        return [h for h in logger.handlers if getattr(h, "_rooted_owned", False)]

    assert len(owned()) == 2
    logging.getLogger("rooted.test").info("hello request_id=req_9")
    for h in owned():
        h.flush()
    assert "req_9" in log_file.read_text("utf-8")
    setup_logging("INFO", None)
    assert len(owned()) == 1
