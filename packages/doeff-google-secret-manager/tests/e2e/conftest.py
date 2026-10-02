"""本物の Secret Manager を読む e2e の検(印 ``real_secret_manager``)は、印を名指して選んだ時(``pytest -m real_secret_manager``)
だけ走る。

以前は環境変数 SECRET_MANAGER_RUN_E2E で切り替えていた(検の module が環境変数を直に読む — DOEFF004・agora-redesign #3012)。
本物の Google Cloud に触るので、選ばなければ skip する。宛先(project・secret id・version)の環境変数は検が ReadEnvironment で読む。
"""

import pytest

REAL_SECRET_MANAGER = "real_secret_manager"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{REAL_SECRET_MANAGER}: 本物の Secret Manager を読む e2e の検(-m {REAL_SECRET_MANAGER} で選んだ時だけ走る)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if REAL_SECRET_MANAGER in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason=f"Secret Manager e2e runs only with -m {REAL_SECRET_MANAGER}")
    for item in items:
        if REAL_SECRET_MANAGER in item.keywords:
            item.add_marker(skip)
