"""Pytest conftest hook — exposes the shared ``run_program`` helper to e2e tests.

本物の OpenAI API を呼ぶ e2e(印 ``real_openai``)は、2 つが揃った時だけ走る: 印を名指して選ぶ(``pytest -m real_openai``)・
doeff.py の鍵が在る。以前は環境変数 RUN_OPENAI_E2E で切り替えていた(検の module が環境変数を直に読む — DOEFF004・
agora-redesign #3012)。費用の掛かる呼びを誤って撃たないため、鍵が在るだけでは走らせない。
"""
import sys
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent.parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from _runner import doeff_py_has_openai_key  # noqa: E402 — TESTS_DIR を sys.path に足した後に読む

REAL_OPENAI = "real_openai"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{REAL_OPENAI}: 本物の OpenAI API を呼ぶ e2e(-m {REAL_OPENAI} で選び、doeff.py の鍵が在る時だけ走る)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    chosen = REAL_OPENAI in (config.getoption("markexpr") or "")
    if chosen and doeff_py_has_openai_key():
        return
    skip = pytest.mark.skip(reason=f"True E2E requires the doeff.py OpenAI key and -m {REAL_OPENAI}")
    for item in items:
        if REAL_OPENAI in item.keywords:
            item.add_marker(skip)
