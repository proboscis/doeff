"""doeff-hy-check に名指した path が、根の書き方(symlink を辿らない絶対 path)と食い違っても黙って捨てられないこと(card ki-ed254bf53094)。

失敗ケース(2026-10-11 agora-controllers の型の赤 22 件): 作業木の根 `/home/ubuntu/.worktrees/<木>` は `/work/toolset/worktrees/<木>` への
symlink で、`--root "$PWD"` は symlink の側の綴り、相対 path を繋ぐ `Path.cwd()` は辿った先の綴りになる。名指した file は「根の外」として
黙って捨てられ、赤 22 件の file で 0 件と答えた(絶対 path で渡すと 22 件)。

- 相対 path でも絶対 path でも、同じ file には同じ所見を返す(symlink の綴りの違いは根の中へ写す)。
- 本当に根の外の path を名指したら、黙って 0 件にせず、名指して断る(exit 2)。
"""

import contextlib
import io
import json
import shutil
from pathlib import Path
from typing import NamedTuple

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

#: 束縛し直しの赤 1 件と setv の警告 2 件を必ず出す検体(test_static_check.py の BINDINGS と同じ形)。
BINDINGS = """(require doeff-hy.macros [defk <-])

(defk rebinds [x]
  {:pre [(: x int)] :post [(: % int)]}
  (setv y x)
  (setv y (+ y 1))
  (val z (* y 2))
  z)
"""


class _Checked(NamedTuple):
    """main を 1 度走らせた結果: 終わりの code・所見(規則・重さ・行)・stderr。"""

    code: int
    found: list[tuple[str, str, int]]
    err: str


def _checked(argv: list[str]) -> _Checked:
    """main を 1 度走らせ、終わりの code・所見(規則・重さ・行)・stderr を返すため。"""
    from doeff_hy._static_check import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(argv)
    found = sorted((d["rule"], d["severity"], d["line"]) for d in json.loads(out.getvalue() or "[]"))
    return _Checked(code, found, err.getvalue())


@needs_pyright
def test_a_relative_path_under_a_symlinked_root_gives_the_same_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    real = tmp_path / "real"
    real.mkdir()
    (real / "bindings.hy").write_text(BINDINGS, encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    monkeypatch.chdir(linked)  # os.getcwd() は辿った先(real)を返す — 根は symlink の綴り(linked)のまま渡す
    absolute = _checked(["--root", str(linked), "--json", "--no-cache", str(linked / "bindings.hy")])
    relative = _checked(["--root", str(linked), "--json", "--no-cache", "bindings.hy"])
    assert ("doeff-hy-rebind", "error", 6) in absolute.found, absolute
    assert (relative.code, relative.found) == (absolute.code, absolute.found), (relative, absolute)


def test_a_named_path_outside_the_root_is_refused_not_dropped(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.hy"
    outside.write_text(BINDINGS, encoding="utf-8")
    code, found, err = _checked(["--root", str(root), "--json", "--no-cache", str(outside)])
    assert code == 2 and found == [], (code, found, err)
    assert str(outside) in err and "根の外" in err, err
