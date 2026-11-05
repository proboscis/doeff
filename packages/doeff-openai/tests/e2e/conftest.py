"""Pytest conftest hook — exposes the shared ``run_program`` helper to e2e tests.

e2e tests that call the real OpenAI API (marker ``real_openai``) run only when two conditions hold: the marker is selected by name (``pytest -m real_openai``) and
a doeff.py key exists. Previously this was switched by the environment variable RUN_OPENAI_E2E (the test module read the environment variable directly — DOEFF004).
To avoid firing costly calls by accident, they do not run merely because a key exists.
"""
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _runner import doeff_py_has_openai_key  # noqa: E402 — read after adding TESTS_DIR to sys.path

REAL_OPENAI = "real_openai"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{REAL_OPENAI}: e2e that calls the real OpenAI API (selected with -m {REAL_OPENAI}; runs only when a doeff.py key exists)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    chosen = REAL_OPENAI in (config.getoption("markexpr") or "")
    if chosen and doeff_py_has_openai_key():
        return
    skip = pytest.mark.skip(reason=f"True E2E requires the doeff.py OpenAI key and -m {REAL_OPENAI}")
    for item in items:
        if REAL_OPENAI in item.keywords:
            item.add_marker(skip)
