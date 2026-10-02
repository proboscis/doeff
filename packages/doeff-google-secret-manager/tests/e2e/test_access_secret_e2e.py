"""End-to-end Secret Manager test that requires real Google Cloud access."""


import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src"
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))
SECRET_PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "doeff-secret" / "src"
if str(SECRET_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(SECRET_PACKAGE_ROOT))

from doeff_core_effects.handlers import (  # noqa: E402
    await_handler,
    lazy_ask,
    state,
    try_handler,
    writer,
)
from doeff_core_effects.os_process import subprocess_handler  # noqa: E402
from doeff_core_effects.process_effects import ReadEnvironment  # noqa: E402
from doeff_core_effects.scheduler import scheduled  # noqa: E402
from doeff_google_secret_manager import access_secret  # noqa: E402

from doeff import do, run, with_handlers  # noqa: E402

ENV_PROJECT = "SECRET_MANAGER_TEST_PROJECT"
ENV_SECRET_ID = "SECRET_MANAGER_TEST_SECRET_ID"
ENV_VERSION = "SECRET_MANAGER_TEST_SECRET_VERSION"


def _runner_settings() -> dict[str, str]:
    """The destinations the runner chose (project・secret id・version) — read through the ReadEnvironment effect and its real
    handler, not os.environ directly (DOEFF004 — agora-redesign #3012). Unset names are absent."""
    found = run(with_handlers([subprocess_handler], ReadEnvironment((ENV_PROJECT, ENV_SECRET_ID, ENV_VERSION))))
    return {entry.name: entry.value for entry in found}


@pytest.mark.e2e
@pytest.mark.real_secret_manager
def test_access_secret_real_secret():
    """Fetch a real secret using ADC — runs only with ``-m real_secret_manager`` (tests/e2e/conftest.py)."""

    settings = _runner_settings()
    project = settings.get(ENV_PROJECT) or "750196570112"
    secret_id = settings.get(ENV_SECRET_ID) or "gemini-api-key"
    version = settings.get(ENV_VERSION) or "latest"

    @do
    def flow():
        return (
            yield access_secret(
                secret_id,
                project=project,
                version=version,
                decode=True,
            )
        )

    result = run(
        scheduled(
            with_handlers(
                [await_handler(), lazy_ask(strict=True), try_handler, state(), writer],
                flow(),
            )
        )
    )

    assert isinstance(result, str)
    assert result.strip() != ""
