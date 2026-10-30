"""Hy や httpx が無い環境で Hy の module・HTTP の handler を読むと、足す extra の名を言う文が出る事の失敗ケース(取り組み
doeff-publish の区切り 8・card ki-7310ee8e4fd0)。

`pip install doeff` は核の 4 個だけを入れ、Hy の言語(doeff-hy)は extra hy、HTTP の client の handler(Hy で書いてあり、httpx で
送る)は extra http で足す(tests/test_core_install_extras.py)。足していない利用者が `No module named 'doeff_hy'` だけを見て
止まらないよう、文の最後に `pip install "doeff[hy]"` などの入れ方の行を出す。文は doeff_core_effects/_extra_hints.py の表 1 つが持つ。

子の process に、塞ぐ名の import に ModuleNotFoundError を返す finder を最前に置いてから import を走らせる。CLI の doeff run の
場面は tests/cli/test_cli_without_hy.py。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHILD_DEADLINE_SECONDS = 60
NO_HY = ("hy", "hyrule", "doeff_hy", "doeff_hy_pytest")
NO_HTTPX = ("httpx",)

# 塞ぐ名(argv[1] の , 区切り)の import に ModuleNotFoundError を返す finder を最前に置き、argv[2] の文を実行して、出た
# ModuleNotFoundError の名と文を JSON で出す。塞ぐ前に読まれていると塞いだことにならないので 99、何も出なければ 98 で終える。
BLOCK_THEN_IMPORT = """
import json
import sys

blocked = tuple(sys.argv[1].split(","))
if any(name.split(".")[0] in blocked for name in sys.modules):
    sys.exit(99)


class _Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in blocked:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)


sys.meta_path.insert(0, _Blocked())

try:
    exec(sys.argv[2])
except ModuleNotFoundError as error:
    print(json.dumps({"name": error.name, "message": str(error)}))
    sys.exit(0)
sys.exit(98)
"""


@pytest.mark.parametrize(
    ("blocked", "statement", "extra"),
    [
        (NO_HY, "import doeff_core_effects._os_process", "hy"),
        (NO_HY, "from doeff_core_effects._cache import cache", "hy"),
        (NO_HY, "from doeff_core_effects import http_production_handler", "http"),
        (NO_HY, "from doeff_core_effects.http_handlers import http_fixture_handler", "http"),
        (NO_HY, "from doeff_core_effects import HttpRequest", "http"),
        (NO_HTTPX, "from doeff_core_effects import http_production_handler", "http"),
        (NO_HTTPX, "from doeff_core_effects.http_handlers import http_fixture_handler", "http"),
    ],
    ids=[
        "hy-module",
        "cache",
        "http-handler-without-hy",
        "http-handlers-module-without-hy",
        "http-effect-without-hy",
        "http-handler-without-httpx",
        "http-handlers-module-without-httpx",
    ],
)
def test_a_missing_extra_names_the_extra_to_install(
    deadline_scale_factor: float, blocked: tuple[str, ...], statement: str, extra: str
) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", BLOCK_THEN_IMPORT, ",".join(blocked), statement],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )

    assert completed.returncode == 0, (completed.returncode, completed.stderr)
    error = json.loads(completed.stdout)
    # 名は塞いだ物のまま(`except ModuleNotFoundError as e: if e.name == "httpx"` と読む呼び手を壊さない)。
    assert error["name"].split(".")[0] in blocked
    assert f"No module named {error['name']!r}" in error["message"]
    assert error["message"].endswith(f'pip install "doeff[{extra}]"'), error["message"]


def test_import_doeff_still_needs_neither_hy_nor_httpx(deadline_scale_factor: float) -> None:
    # 入れ方の行を出す仕掛けが、核の import で Hy か httpx を読み始めていない事。
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            BLOCK_THEN_IMPORT,
            ",".join(NO_HY + NO_HTTPX),
            "import doeff, doeff_core_effects, doeff_core_effects._extra_hints",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )

    assert completed.returncode == 98, (completed.stdout, completed.stderr)
