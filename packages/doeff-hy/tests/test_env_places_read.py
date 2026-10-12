"""doeff-hy-check の置き場の読み(doeff_hy.env_places)が、process 全般の答え手の module doeff_core_effects.os_process を
読み込まない事(card acp:kanban-issue:ki-efa3d187b136)。

env_places は環境変数 2 つ($DOEFF_HY_CHECK_CACHE・$XDG_CACHE_HOME — 今は pyright の thread の数 $DOEFF_HY_CHECK_PYRIGHT_THREADS も)を
読むだけなのに、ReadEnvironment に答える答え手として
os_process の subprocess_handler(子 process・thread・signal の答え手を全部持つ大きな Hy の module)を読み込み、その Hy の bytecode の
確かめが doeff-hy-check の子 process 1 回ごとに走っていた(暖まった状態で 1.8〜1.9 秒・作業木を切った直後は 4.7 秒 — 型検査の検は
1 件で子 process を 4〜8 回起こす)。読みは新しい Python の process で確かめる(同じ process の前の検が読み込んだ module を数えない)。
"""

import subprocess
import sys

import pytest

PROBE: str = (
    "import sys\n"
    "from doeff_hy import env_places\n"
    "print(env_places.check_cache_setting())\n"
    "print(env_places.cache_home())\n"
    "print(env_places.pyright_threads_setting())\n"
    "print('doeff_core_effects.os_process' in sys.modules)\n"
)


def probe() -> list[str]:
    """子の Python(この process の環境を受け継ぐ — Hy の bytecode の置き場なども親と同じ)で PROBE を走らせた出力の行。"""
    done = subprocess.run([sys.executable, "-c", PROBE], capture_output=True, text=True, check=True, timeout=120)
    return done.stdout.strip().splitlines()


def test_reading_the_cache_places_does_not_load_the_process_handlers() -> None:
    lines = probe()
    assert lines[-1] == "False", lines


def test_the_cache_places_still_follow_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOEFF_HY_CHECK_CACHE", "/kept/check")
    monkeypatch.setenv("XDG_CACHE_HOME", "/kept/xdg")
    named = probe()
    assert named[:2] == ["/kept/check", "/kept/xdg"], named
    monkeypatch.setenv("DOEFF_HY_CHECK_CACHE", "")
    unset = probe()
    assert unset[0] == "None", unset


def test_the_pyright_threads_setting_follows_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pyright の thread の数の素の値も、doeff-hy-check の入口が os.environ を直に読まずにこの module から受ける(展開の子の数の
    上限と同じ — card ki-1475765a3f18)。値の判じ(1 以上の整数か)は呼び手の pyright_threads。"""
    monkeypatch.setenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", "2")
    named = probe()
    assert named[2] == "2", named
    monkeypatch.setenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", "")
    unset = probe()
    assert unset[2] == "None", unset
