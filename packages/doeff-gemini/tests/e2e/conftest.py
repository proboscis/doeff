"""Live tests that call the real Gemini API (marker ``real_gemini``) run only when the marker is selected by name (``pytest -m real_gemini``).

Previously this was switched by the environment variable DOEFF_GEMINI_RUN_E2E (the test module read the environment variable directly — DOEFF004).
To avoid firing costly calls by accident, they are skipped unless selected (the skip when ADC credentials are missing stays inside the test).
"""

import pytest

REAL_GEMINI = "real_gemini"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{REAL_GEMINI}: live test that calls the real Gemini API (runs only when selected with -m {REAL_GEMINI})",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if REAL_GEMINI in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason=f"live Gemini e2e runs only with -m {REAL_GEMINI}")
    for item in items:
        if REAL_GEMINI in item.keywords:
            item.add_marker(skip)
