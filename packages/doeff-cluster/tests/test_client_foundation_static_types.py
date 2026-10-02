"""手元の道具の口の型の宣言(doeff_cluster/client_foundation.pyi)の失敗ケース(#2782)。

口を組む部品(detached-cluster・DetachedSender・RouteCell・route-of・coordinator-route-options ほか)には型の宣言が無く、使い手の repo が
直に組むと strict の型検査が「型の分からない import」23 件で止めた。→ 部品を組んだ口 1 つ with-detached-client を .pyi で宣言する。
宣言を外すと 1 本目が赤になり、宣言が実装から離れると 2 本目・3 本目が赤になる(test_process_versions_static_types.py と同じ形)。
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

from doeff_cluster import client_foundation

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <-])
(import doeff_cluster.client_foundation [with-detached-client])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv])
(import doeff_cluster.shared.intent.detached_model [AwaitDetached DetachedAwaited])

(defk probe-await [coordinator revision env key]
  {:pre [(: coordinator str) (: revision str) (: env (| RuntimeEnv None)) (: key str)] :post [(: % DetachedAwaited)]
   :tags {:context "probe" :role "foundation"}}
  "名乗りを値で渡した口の下で待ち、本体の答えの型をそのまま受ける。"
  (<- outcome DetachedAwaited (with-detached-client coordinator revision env (AwaitDetached key)))
  outcome)
"""

# 宣言の名の全部。宣言に名を足したら、ここにも足す(実装に在るかは _mismatch が検める)。
DECLARED = {"with_detached_client"}


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
def test_users_of_the_client_get_no_unknown_types(tmp_path: Path) -> None:
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
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and not node.target.id.startswith("_")
    } | {node.name for node in stub.body if isinstance(node, ast.FunctionDef | ast.ClassDef) and not node.name.startswith("_")}


def test_the_stub_matches_the_hy_module() -> None:
    # 宣言の名は .hy に在り、関数の引数の名と順は実装と同じ(宣言だけが先へ行かない)。
    stub = _stub(client_foundation)
    assert _mismatches(client_foundation, stub) == []
    assert _declared_names(stub) == DECLARED


def test_a_dropped_identity_parameter_is_found() -> None:
    # 失敗ケース: 宣言の with_detached_client から名乗りの引数 runtime_env を外すと、食い違いとして名指される。
    stub = _stub(client_foundation)
    for node in stub.body:
        if isinstance(node, ast.FunctionDef) and node.name == "with_detached_client":
            node.args.args = [a for a in node.args.args if a.arg != "runtime_env"]
    assert _mismatches(client_foundation, stub) == [
        "関数 with_detached_client の引数 ['coordinator', 'revision', 'body'] が実装 "
        "['coordinator', 'revision', 'runtime_env', 'body'] と違う"
    ]
