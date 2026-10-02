"""本物の Gemini API を呼ぶ live の検(印 ``real_gemini``)は、印を名指して選んだ時(``pytest -m real_gemini``)だけ走る。

以前は環境変数 DOEFF_GEMINI_RUN_E2E で切り替えていた(検の module が環境変数を直に読む — DOEFF004・agora-redesign #3012)。
費用の掛かる呼びを誤って撃たないため、選ばなければ skip する(ADC の資格が無い時の skip は検の中のまま)。
"""

import pytest

REAL_GEMINI = "real_gemini"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{REAL_GEMINI}: 本物の Gemini API を呼ぶ live の検(-m {REAL_GEMINI} で選んだ時だけ走る)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if REAL_GEMINI in (config.getoption("markexpr") or ""):
        return
    skip = pytest.mark.skip(reason=f"live Gemini e2e runs only with -m {REAL_GEMINI}")
    for item in items:
        if REAL_GEMINI in item.keywords:
            item.add_marker(skip)
