"""版の識別の型の宣言(foundation/process_versions.pyi)の失敗ケース(#2765・#2766)。

process_versions.hy は Hy の module で型の宣言が無かったので、process-versions を <- や ! で受ける使い手の strict に、書き手に直せない
Unknown の赤が出得る。→ .pyi で宣言する。宣言を外すと 1 本目が赤になり、宣言が実装から離れると 2 本目・3 本目が赤になる
(test_readiness_static_types.py と同じ形)。旧い current-versions は #2766 で消した。
"""

import ast
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import hy  # noqa: F401  # .hy の module の import hook(検だけを単独で走らせても読めるように)
import pytest

from doeff_cluster.foundation import process_versions

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <- val])
(import doeff_cluster.foundation.process_versions [process-versions])

(defk probe-bang [environ]
  {:pre [(: environ (get dict #(str str)))] :post [(: % str)] :tags {:context "probe" :role "entry"}}
  "式の中の ! で版の識別を読む(検の宣言の組み立てと同じ読み — 答えの型が読める)。"
  (val versions (! (process-versions environ)))
  (get versions "doeff"))

(defk probe-process [environ]
  {:pre [(: environ (get dict #(str str)))] :post [(: % str)] :tags {:context "probe" :role "foundation"}}
  "置き場を渡して版の識別を読む(defk の答えの型が読める)。"
  (<- versions (get dict #(str str)) (process-versions environ))
  (get versions "doeff"))
"""

# 宣言の名の全部。宣言に名を足したら、ここにも足す(実装に在るかは _mismatch が検める)。
DECLARED = {"RUNTIME_ENV_KEY_VAR", "process_versions", "this_process_versions", "this_process_environ"}


def _errors(root: Path) -> list[tuple[str, int, str]]:
    # 型検査の道具は hook と同じく `python -m doeff_hy.static_check` で撃つ(検の module が道具の中身を import しない)。
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "doeff_hy.static_check",
            "--root",
            str(root),
            "--json",
            "--strict",
            "--no-cache",
            str(root / "probe.hy"),
        ],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    text = done.stdout
    diagnostics = json.loads(text) if text.strip() else []
    return [
        (str(d["rule"]), int(str(d["line"])), str(d["message"]))
        for d in diagnostics
        if d["severity"] == "error"
    ]


@needs_pyright
def test_users_of_the_version_identity_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def _stub(module: ModuleType) -> ast.Module:
    return ast.parse(Path(inspect.getfile(module)).with_suffix(".pyi").read_text(encoding="utf-8"))


def _mismatch(module: ModuleType, node: ast.stmt) -> str | None:
    """宣言の 1 文が実装と食い違えばその文(名・関数の引数)、一致すれば None。"""
    match node:
        case ast.AnnAssign(target=ast.Name(id=name)) if not hasattr(module, name):
            return f"宣言の名 {name} が実装に無い"
        case ast.FunctionDef(name=name) if not hasattr(module, name):
            return f"宣言の関数 {name} が実装に無い"
        case ast.FunctionDef(name=name):
            declared = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            actual = list(inspect.signature(getattr(module, name)).parameters)
            return None if declared == actual else f"関数 {name} の引数 {declared} が実装 {actual} と違う"
        case _:
            return None


def _mismatches(module: ModuleType, stub: ast.Module) -> list[str]:
    """宣言の全部の食い違い(空なら一致)。"""
    return [found for node in stub.body if (found := _mismatch(module, node)) is not None]


def _declared_names(stub: ast.Module) -> set[str]:
    """宣言の公開の名の全部(_ で始まる型の補助は除く)。"""
    return {
        node.target.id
        for node in stub.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    } | {node.name for node in stub.body if isinstance(node, ast.FunctionDef | ast.ClassDef) and not node.name.startswith("_")}


def test_the_stub_matches_the_hy_module() -> None:
    # 宣言の名は .hy に在り、関数の引数の名と順は実装と同じ(宣言だけが先へ行かない)。
    stub = _stub(process_versions)
    assert _mismatches(process_versions, stub) == []
    assert _declared_names(stub) == DECLARED


def test_a_dropped_environ_parameter_is_found() -> None:
    # 失敗ケース: 宣言の process_versions から置き場の引数を外すと、食い違いとして名指される。
    stub = _stub(process_versions)
    for node in stub.body:
        if isinstance(node, ast.FunctionDef) and node.name == "process_versions":
            node.args.args = []
    assert _mismatches(process_versions, stub) == ["関数 process_versions の引数 [] が実装 ['environ'] と違う"]
