"""doeff-hy-check(doeff_hy.static_check)が pyright を 1 本の thread でしか撃てない失敗ケース(card acp:kanban-issue:ki-1475765a3f18)。

agora-controllers の日次の型の測り(hy-type-trend・範囲の .hy 2,551 本)は、展開の後の pyright 1 本が 1 core で約 2,250 秒かかり、
処理ステージの上限 3,300 秒の 80%(2,640 秒)に展開の秒を足すと収まらない(10-12 12:00 JST の回の atlas の Pod の読み: pyright の
process が 03:20:30〜03:57 UTC に CPU 1 core・後半は heap の上限 4,288 MiB に張りついて GC の thread が 1.6〜1.8 core)。pyright は
`--threads <数>` で検める file を子の process に分けて並べられるが、run_pyright の命令にはその口が無かった。

- thread の数は環境変数 DOEFF_HY_CHECK_PYRIGHT_THREADS で受ける(展開の子の数 DOEFF_HY_CHECK_EXPANDERS と同じく、入口の main だけが
  env_places から素の値を受けて判じる)。既定は 1 で、その時は今までと同じく `--threads` を渡さない。
- 1 以上の整数でない値は黙って既定へ落とさず断る(書き手の誤りを見えるようにする)。
"""

import contextlib
import io
import json
import shlex
import sys
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
from doeff_hy import static_check


def _recording_pyright(tmp_path: Path, log: Path) -> Path:
    """起こされた時の引数を log に 1 行で書き、診断の無い JSON を返す pyright の偽物(命令の形だけを見る)。"""
    pyright = tmp_path / "recording-pyright"
    pyright.write_text(
        f'#!/bin/sh\necho "$@" >> {shlex.quote(str(log))}\necho \'{{"generalDiagnostics": []}}\'\n',
        encoding="utf-8",
    )
    pyright.chmod(0o755)
    return pyright


def _checked_once(tmp_path: Path) -> list[str]:
    """.hy 1 本の根に入口の main を 1 度当て、pyright の偽物が受けた引数の並びを返す。"""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "probe.hy").write_text("(setv answer 1)\n", encoding="utf-8")
    log = tmp_path / "pyright.log"
    pyright = _recording_pyright(tmp_path, log)
    with contextlib.redirect_stdout(io.StringIO()) as out:
        rc = static_check.main(
            ["--root", str(root), "--json", "--no-cache", "--pyright", str(pyright), str(root / "probe.hy")]
        )
    assert rc == 0, out.getvalue()
    assert isinstance(json.loads(out.getvalue()), list)  # 偽物の答え(診断なし)を読めた — probe の setv の警告は数えない
    started = log.read_text(encoding="utf-8").splitlines()
    assert len(started) == 1, started
    return started[0].split()


def test_the_pyright_threads_default_to_one_and_are_set_by_the_environment() -> None:
    """thread の数は既定 1。環境変数 DOEFF_HY_CHECK_PYRIGHT_THREADS で替えられ、1 以上の整数でない値は黙って既定へ落とさず断る。"""
    assert static_check.pyright_threads(None) == 1
    assert static_check.pyright_threads("") == 1
    assert static_check.pyright_threads("2") == 2
    for wrong in ("0", "-1", "two", "1.5"):
        with pytest.raises(ValueError, match="DOEFF_HY_CHECK_PYRIGHT_THREADS"):
            static_check.pyright_threads(wrong)


def test_the_entry_passes_the_thread_count_to_pyright(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """環境変数が 2 以上の数を名指すと、入口の main は pyright を `--threads <数>` で撃つ。"""
    monkeypatch.setattr(sys, "path", list(sys.path))  # main は根を sys.path の頭に足す
    monkeypatch.setenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", "3")
    argv = _checked_once(tmp_path)
    assert "--threads" in argv, argv
    assert argv[argv.index("--threads") + 1] == "3", argv


def test_the_entry_without_the_setting_runs_pyright_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """設定が無い(既定 1)時は、今までと同じく `--threads` を渡さない(1 本の pyright)。"""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.delenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", raising=False)
    argv = _checked_once(tmp_path)
    assert "--threads" not in argv, argv


def test_the_entry_refuses_a_wrong_thread_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """1 以上の整数でない値は走れなかった(exit 2)— pyright を撃つ前に断り、変数の名を名指す。"""
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("DOEFF_HY_CHECK_PYRIGHT_THREADS", "0")
    assert static_check.main(["--root", str(tmp_path), "--no-cache"]) == 2
    assert "DOEFF_HY_CHECK_PYRIGHT_THREADS" in capsys.readouterr().err
