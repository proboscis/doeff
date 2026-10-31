"""一番外の読み込み(module の本体の実行 — 中の import を含む)の間だけ GC の閾値を上げ、終わったら戻す(agora-redesign)
の検。

測り(2026-10-12・agora-controllers の test_emulated_screen.hy・CPython 3.14.3 free-threaded): file 全体の秒の中の GC は 7 回・
0.28〜0.38 秒で、全部 test module の読み込み 1 つの中だった。free-threaded の GC は前の GC から RSS が 10% 超増えると heap 全体を
掃くので、ごみが 1% 前後でも読み込みが heap を育てるたびに掃く。読み込みの間だけ閾値(前の GC から正味で増えてよい object の数
— 溜まりうるごみの数の上限でもある)を有限の値へ上げると 2 回・0.15 秒になった。

子の process で、包みを入れて一時の package を読み、module の本体が見た閾値と、読み込みの後の閾値を出す。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

#: 子の process — 包みを入れ、場面(argv[2])ごとに一時の package(argv[1])を読み、見た閾値を JSON で出す。module の本体は
#: 見た閾値を自分の名 ``threshold`` に置く(途中で落ちる module は例外に載せる)。
_CHILD = """\
import gc
import json
import os
import sys
import threading

import __main__
import doeff_hy_bytecode_guard

doeff_hy_bytecode_guard.install()
sys.path.insert(0, sys.argv[1])
scene = sys.argv[2]
ENTERED, RELEASE = threading.Event(), threading.Event()
before = gc.get_threshold()
result = {"before": before}


def seen(*names):
    return {name: sys.modules[f"pkg.{name}"].threshold for name in names}


def import_in_thread(name):
    worker = threading.Thread(target=__import__, args=(name,))
    worker.start()
    assert ENTERED.wait(30)
    return worker


if scene == "nested":
    import pkg.outer
    result["seen"] = {**seen("outer", "inner"), "outer-after-inner": pkg.outer.threshold_after_inner}
elif scene == "raises":
    try:
        import pkg.bad
    except RuntimeError as failure:
        result["seen"] = {"bad": failure.args[0]}
elif scene == "own":
    import pkg.own
elif scene == "off":
    gc.set_threshold(0, *before[1:])
    import pkg.inner
    result["seen"] = seen("inner")
elif scene == "two-threads":
    worker = import_in_thread("pkg.slow")
    import pkg.inner
    result["while_other_imports"] = gc.get_threshold()
    RELEASE.set()
    worker.join()
    result["seen"] = seen("slow", "inner")
elif scene == "fork":
    worker = import_in_thread("pkg.slow")
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.write(write_end, json.dumps(gc.get_threshold()).encode())
        os._exit(0)
    os.close(write_end)
    os.waitpid(pid, 0)
    result["child"] = json.loads(os.read(read_end, 1000))
    RELEASE.set()
    worker.join()
    result["seen"] = seen("slow")
result["after"] = gc.get_threshold()
print(json.dumps(result))
"""

#: 一時の package の module(名 → 本体)。
_MODULES = {
    "outer": "import gc\nthreshold = gc.get_threshold()\nimport pkg.inner\nthreshold_after_inner = gc.get_threshold()\n",
    "inner": "import gc\nthreshold = gc.get_threshold()\n",
    "bad": "import gc\nraise RuntimeError(gc.get_threshold())\n",
    "own": "import gc\ngc.set_threshold(500)\n",
    "slow": "import gc\nimport __main__\nthreshold = gc.get_threshold()\n__main__.ENTERED.set()\nassert __main__.RELEASE.wait(30)\n",
}


def _run(tmp_path: Path, scene: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """子の process で場面を 1 つ走らせ、見た閾値(before・seen・after ほか)を返す。子は検の process の環境を受け継ぎ、.pyc を
    書かない設定だけを足す(検の後に monkeypatch が戻す)。"""
    package = tmp_path / "pkg"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("")
    for name, body in _MODULES.items():
        (package / f"{name}.py").write_text(body)
    script = tmp_path / "child.py"
    script.write_text(_CHILD)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    completed = subprocess.run(
        [sys.executable, "-W", "ignore::DeprecationWarning", str(script), str(tmp_path), scene],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


def test_the_threshold_is_raised_while_the_outermost_import_runs_and_restored_after(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """一番外の読み込みの本体も、中の import の本体も、上げた閾値を見る(下の 2 つの欄は変えない)。終わったら前の閾値に戻る。"""
    result = _run(tmp_path, "nested", monkeypatch)
    before = result["before"]
    seen = result["seen"]
    raised = seen["outer"]
    assert raised[0] > before[0], f"読み込みの間の閾値が上がっていない: 前 {before} → 読み込みの間 {raised}"
    assert raised[1:] == before[1:]
    assert seen["inner"] == seen["outer-after-inner"] == raised
    assert result["after"] == before


def test_the_threshold_is_restored_when_the_import_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """読み込みが例外で終わっても、前の閾値に戻る。"""
    result = _run(tmp_path, "raises", monkeypatch)
    assert result["seen"]["bad"][0] > result["before"][0]
    assert result["after"] == result["before"]


def test_a_threshold_set_by_the_imported_module_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """読み込まれた module が自分で決めた閾値は、終わりに上書きしない。"""
    result = _run(tmp_path, "own", monkeypatch)
    assert result["after"][0] == 500


def test_a_zero_threshold_is_left_off_during_the_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """閾値 0(自動の GC を切ってある)は、読み込みの間も 0 のまま(切った GC を動かさない)。"""
    result = _run(tmp_path, "off", monkeypatch)
    assert result["seen"] == {"inner": [0, *result["before"][1:]]}
    assert result["after"][0] == 0


def test_the_threshold_stays_raised_until_the_last_of_two_concurrent_imports_ends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """閾値は process に 1 つ。別の thread の読み込みが続いている間に 1 つ目の読み込みが終わっても戻さず、最後の読み込みが
    終わった時に、どちらの読み込みよりも前の閾値へ戻る。"""
    result = _run(tmp_path, "two-threads", monkeypatch)
    raised = result["seen"]["slow"]
    assert raised[0] > result["before"][0]
    assert result["while_other_imports"] == result["seen"]["inner"] == raised
    assert result["after"] == result["before"]


def test_a_child_forked_while_another_thread_imports_gets_the_threshold_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """別の thread の読み込みの途中で fork した子は、その thread を持たない(読み込みは子の中で終わらない)ので、前の閾値に戻る。"""
    result = _run(tmp_path, "fork", monkeypatch)
    assert result["seen"]["slow"][0] > result["before"][0]
    assert result["child"] == result["before"]
    assert result["after"] == result["before"]
