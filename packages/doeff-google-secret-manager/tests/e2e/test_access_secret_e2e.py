"""End-to-end Secret Manager test that requires real Google Cloud access."""


import os
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
from doeff_core_effects.scheduler import scheduled  # noqa: E402
from doeff_google_secret_manager import access_secret  # noqa: E402

from doeff import do, run, with_handlers  # noqa: E402

ENV_ENABLE = "SECRET_MANAGER_RUN_E2E"
ENV_PROJECT = "SECRET_MANAGER_TEST_PROJECT"
ENV_SECRET_ID = "SECRET_MANAGER_TEST_SECRET_ID"
ENV_VERSION = "SECRET_MANAGER_TEST_SECRET_VERSION"


@pytest.mark.e2e
def test_access_secret_real_secret():
    """Fetch a real secret using ADC when environment variables are provided."""

    if os.getenv(ENV_ENABLE) != "1":  # noqa: DOEFF004 - e2e の有効化と宛先は実行者が環境変数で渡す
        pytest.skip(
            f"Set {ENV_ENABLE}=1 to enable Secret Manager E2E test "
            f"(optionally configure {ENV_PROJECT}, {ENV_SECRET_ID}, {ENV_VERSION})."
        )

    project = os.getenv(ENV_PROJECT) or "750196570112"  # noqa: DOEFF004 - e2e の有効化と宛先は実行者が環境変数で渡す
    secret_id = os.getenv(ENV_SECRET_ID) or "gemini-api-key"  # noqa: DOEFF004 - e2e の有効化と宛先は実行者が環境変数で渡す
    version = os.getenv(ENV_VERSION) or "latest"  # noqa: DOEFF004 - e2e の有効化と宛先は実行者が環境変数で渡す

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
