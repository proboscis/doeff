"""The e2e test that reads the real Secret Manager (marker ``real_secret_manager``) runs only when the marker is selected by name (``pytest -m real_secret_manager``).

Previously this was switched by the environment variable SECRET_MANAGER_RUN_E2E (the test module read the environment variable directly — DOEFF004).
It touches the real Google Cloud, so it is skipped unless selected. The environment variables for the target (project, secret id, version) are read by the test via ReadEnvironment.
"""

import pytest

REAL_SECRET_MANAGER = "real_secret_manager"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{REAL_SECRET_MANAGER}: e2e test that reads the real Secret Manager (runs only when selected with -m {REAL_SECRET_MANAGER})",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if REAL_SECRET_MANAGER in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason=f"Secret Manager e2e runs only with -m {REAL_SECRET_MANAGER}")
    for item in items:
        if REAL_SECRET_MANAGER in item.keywords:
            item.add_marker(skip)
