from __future__ import annotations

from pathlib import Path

import pytest

from tests.design.reference_pdf import build_reference_pdf


@pytest.fixture(scope="session")
def reference_pdf(tmp_path_factory) -> Path:
    return build_reference_pdf(tmp_path_factory.mktemp("ref") / "reference.pdf")


@pytest.fixture(scope="session")
def raw(reference_pdf):
    from rooted.design import extract_raw

    return extract_raw(reference_pdf)
