"""Hy の検の file の集め手の 1 点(ini の doeff_hy_test_files・agora-redesign #2591)。

conftest.py を 1 つも置かない project で、test を定義する .hy は関数ごとの item に、定義しない「上から順に実行する
script」の .hy は file ごと 1 つの item に集まり、script の例外は赤になること。外す一覧(doeff_hy_test_skips)の
file は import されずに理由つきの skip になること。ini を書かない repo(下流の既定)では何も集めないこと。

別の process で pytest を走らせる(pytester の subprocess)。記録のキャッシュと pyc は tmp の下へ書く。
"""

from dataclasses import dataclass
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]

_LOG = """(import os)
(with [log (open (get os.environ "RUN_LOG") "a")] (.write log (+ __name__ "\\n")))
"""

FILES = {
    "tests/test_functions.hy": "(defn test-one [] (assert True))\n(defn test-two [] (assert True))\n",
    "tests/test_script_ok.hy": _LOG + '(print "script ran")\n(assert (= (+ 1 1) 2))\n',
    "tests/test_skipped.hy": '(raise (RuntimeError "外したので実行されないはず"))\n',
    "tests/helper.hy": '(raise (RuntimeError "test_ で始まらない file は集めない"))\n',
}
SKIP_REASON = "持ち主へ回した — 理由の例"


@dataclass(frozen=True)
class Run:
    """1 回の pytest の走行: 終了コード・出力(stdout と stderr)の全文・その走行で実行された script の module の名。"""

    ret: int
    out: str
    ran: list[str]


def _project(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ini: str
) -> None:
    """FILES の木と ini を置き、キャッシュ・pyc・実行の記録の置き場を tmp の下へ向ける。"""
    for name, text in FILES.items():
        path = pytester.path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    pytester.makeini(
        f"[pytest]\ndoeff_adr_wiring = off\ndoeff_adr_items_cache = {tmp_path / 'items'}\n" + ini
    )
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pyc"))
    monkeypatch.setenv("RUN_LOG", str(tmp_path / "run.log"))


def _run(pytester: pytest.Pytester, tmp_path: Path, *args: str) -> Run:
    """別の process で pytest を走らせ、その走行で実行された script の名を読んで記録を空にする。"""
    result = pytester.runpytest_subprocess("-p", "no:cacheprovider", "-rs", *args, timeout=120)
    log = tmp_path / "run.log"
    ran = log.read_text(encoding="utf-8").split() if log.exists() else []
    log.write_text("", encoding="utf-8")
    return Run(result.ret, result.stdout.str() + "\n" + result.stderr.str(), ran)


_DECLARED = (
    "doeff_hy_test_files = test_*.hy\n"
    f"doeff_hy_test_skips =\n    tests/test_skipped.hy | {SKIP_REASON}\n"
)


def test_one_collector_takes_test_modules_and_scripts_without_any_conftest(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """test の関数は item ごと・script は file ごと 1 つ・外した file は import せず理由つきの skip。"""
    _project(pytester, tmp_path, monkeypatch, _DECLARED)

    cold = _run(pytester, tmp_path, "-v")
    assert "tests/test_functions.hy::test_one PASSED" in cold.out, cold.out
    assert "tests/test_functions.hy::test_two PASSED" in cold.out, cold.out
    assert "tests/test_script_ok.hy::script PASSED" in cold.out, cold.out
    assert SKIP_REASON in cold.out, cold.out
    assert "外したので実行されないはず" not in cold.out, cold.out
    assert "helper.hy" not in cold.out, cold.out
    assert cold.ret == 0, cold.out
    assert cold.ran == ["tests.test_script_ok"], cold.ran

    # 2 回目は記録から収集する — 収集では script を実行せず、script の item の実行で 1 度だけ実行する。
    collected = _run(pytester, tmp_path, "--collect-only", "-q")
    assert "tests/test_script_ok.hy::script" in collected.out, collected.out
    assert collected.ran == [], f"記録から収集したのに収集で script を実行した: {collected.ran}"
    warm = _run(pytester, tmp_path, "-v", "tests/test_script_ok.hy")
    assert "tests/test_script_ok.hy::script PASSED" in warm.out, warm.out
    assert warm.ran == ["tests.test_script_ok"], warm.ran


def test_a_script_that_raises_is_red(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """script の例外は赤 — 記録の無い 1 回目は収集の中で実行するので、その file の収集の赤になる。"""
    _project(pytester, tmp_path, monkeypatch, _DECLARED)
    (pytester.path / "tests" / "test_script_red.hy").write_text(
        '(raise (RuntimeError "script の赤"))\n', encoding="utf-8"
    )
    red = _run(pytester, tmp_path, "tests/test_script_red.hy")
    assert red.ret != 0, red.out
    assert "ERROR collecting tests/test_script_red.hy" in red.out, red.out
    assert "script の赤" in red.out, red.out


def test_a_script_that_fails_after_the_first_run_is_red_on_the_item(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """記録から収集した script も、実行で例外が出れば item が赤になる(緑の記録を持ち越さない)。"""
    _project(pytester, tmp_path, monkeypatch, _DECLARED)
    assert _run(pytester, tmp_path, "tests/test_script_ok.hy").ret == 0
    monkeypatch.setenv("RUN_LOG", str(tmp_path / "no-such-dir" / "run.log"))
    red = _run(pytester, tmp_path, "-v", "tests/test_script_ok.hy")
    assert red.ret != 0, red.out
    assert "FileNotFoundError" in red.out, red.out


def test_a_repo_without_the_ini_collects_no_hy_test_files(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """doeff_hy_test_files を書かない repo(下流の既定)では test_*.hy を集めない — 自前の conftest と二重に集めない。"""
    _project(pytester, tmp_path, monkeypatch, "")
    result = _run(pytester, tmp_path, "--collect-only", "-q")
    assert ".hy" not in result.out, result.out


def test_a_skip_row_without_a_reason_stops_the_run(
    pytester: pytest.Pytester, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """理由の無い除外は設定の誤りとして止める(黙って外さない)。"""
    _project(
        pytester,
        tmp_path,
        monkeypatch,
        "doeff_hy_test_files = test_*.hy\ndoeff_hy_test_skips =\n    tests/test_skipped.hy\n",
    )
    result = _run(pytester, tmp_path, "--collect-only", "-q")
    assert result.ret != 0, result.out
    assert "doeff_hy_test_skips" in result.out, result.out
