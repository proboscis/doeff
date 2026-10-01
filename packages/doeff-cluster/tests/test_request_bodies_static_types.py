"""coordinator/protocol/request_bodies.hy の型の宣言(request_bodies.pyi)の失敗ケース。

request_bodies.hy は Hy の module で型の宣言が無かったので、判断を直に呼ぶ検(responded)と調停ループの組(request-bodies)を使う
使い手の strict に、書き手に直せない Unknown の赤(Type of "responded" is unknown ほか)が出ていた(agora-redesign #2445)。
→ request_bodies.pyi で宣言する。宣言を外すと 1 本目が赤になり、宣言が実装から離れると 2 本目が赤になる。
"""

import ast
import inspect
import json
import shutil
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

import hy  # noqa: F401  # .hy の module(request_bodies)を読む import hook を有効にする
from doeff_cluster.coordinator.protocol import request_bodies as launch

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [deff])
(import doeff_cluster.coordinator.protocol.request_bodies [responded request-bodies])

(deff answered [#^ object state #^ object request]
  {:pre [] :post [(: % int)] :tags {:context "probe" :role "foundation"}}
  "判断を直に呼んで status を読む(検の道具の形)。"
  (get (responded state request 0 None) 1))

(setv STACK #(request-bodies))  ; 調停ループの組に置く
"""


def _errors(root: Path) -> list[tuple[str, int, str]]:
    # 型検査の道具は hook と同じく `python -m doeff_hy.static_check` で撃つ(検の module が道具の中身を import しない)。
    done = subprocess.run(
        [sys.executable, "-m", "doeff_hy.static_check", "--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")],
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
def test_users_of_responded_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def test_the_stub_matches_request_bodies_hy() -> None:
    # 宣言の名は request_bodies.hy に在り、関数の引数の名と順(位置と名指しの両方)・record の欄は実装と同じ(宣言だけが先へ行かない)。
    stub = ast.parse(Path(launch.__file__).with_suffix(".pyi").read_text(encoding="utf-8"))
    functions = [node for node in stub.body if isinstance(node, ast.FunctionDef)]
    classes = {node.name: node for node in stub.body if isinstance(node, ast.ClassDef)}
    values = [node.target.id for node in stub.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)]
    declared = [f.name for f in functions] + list(classes) + values
    assert [name for name in declared if not hasattr(launch, name)] == []
    for function in functions:
        assert [a.arg for a in function.args.args + function.args.kwonlyargs] == list(
            inspect.signature(getattr(launch, function.name)).parameters
        ), function.name
    for name, node in classes.items():
        stub_fields = [
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        ]
        assert stub_fields == [f.name for f in fields(getattr(launch, name))], name
