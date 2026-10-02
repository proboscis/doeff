"""準備できたの報告の型の宣言(shared/intent/readiness_model.pyi・shared/protocol/readiness_handlers.pyi)の失敗ケース(#2777)。

readiness_model.hy と readiness_handlers.hy は Hy の module で型の宣言が無かったので、ReportReady を出す使い手と readiness-memory を
土台の組に並べる使い手の strict に、書き手に直せない Unknown の赤(Type of "ReportReady" is unknown・Type of "readiness_memory" is unknown)
が出ていた。→ 2 つの .pyi で宣言する。宣言を外すと 1 本目が赤になり、宣言が実装から離れると 2 本目・3 本目が赤になる
(test_host_contract_static_types.py と同じ形)。
"""

import ast
import dataclasses
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import hy  # noqa: F401  # .hy の module の import hook(2 つとも .hy の module — 検だけを単独で走らせても読めるように)
import pytest

from doeff_cluster.shared.intent import readiness_model
from doeff_cluster.shared.protocol import readiness_handlers

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <-])
(import doeff [Program EffectBase with-handlers])
(import doeff_cluster.shared.intent.readiness_model [ReportReady ROLE-STANDBY])
(import doeff_cluster.shared.protocol.readiness_handlers [readiness-memory])

(defk probe-report []
  {:pre [] :post [(: % None)] :tags {:context "probe" :role "program"}}
  "準備できたの報告の effect の型が読める(欄を名で渡す)。"
  (<- (ReportReady :ready True :reason "probe" :role ROLE-STANDBY))
  None)

(defk probe-recorded [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % int)] :tags {:context "probe" :role "foundation"}}
  "報告を積む答え手を並べた下で本文を走らせる(handler の型が読める)。"
  (setv #^ (get list (get dict #(str object))) reports [])
  (<- answer int (with-handlers [(readiness-memory reports)] body))
  answer)
"""

# 宣言の名の全部(module ごと)。宣言に名を足したら、ここにも足す(実装に在るかは _mismatch が検める)。
DECLARED = {
    readiness_model: {"ROLE_ACTIVE", "ROLE_STANDBY", "HANDOFF_TIMEOUT_SECONDS", "READINESS_KEYS", "REASON_KEPT_CHARS", "JsonField", "ReportReady"},
    readiness_handlers: {"readiness_memory", "readiness_http"},
}


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
def test_users_of_the_readiness_report_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def _stub(module: ModuleType) -> ast.Module:
    return ast.parse(Path(inspect.getfile(module)).with_suffix(".pyi").read_text(encoding="utf-8"))


def _mismatch(module: ModuleType, node: ast.stmt) -> str | None:
    """宣言の 1 文が実装と食い違えばその文(名・関数の引数・record の欄)、一致すれば None。"""
    match node:
        case ast.AnnAssign(target=ast.Name(id=name)) if not hasattr(module, name):
            return f"宣言の名 {name} が実装に無い"
        case ast.FunctionDef(name=name) if not hasattr(module, name):
            return f"宣言の関数 {name} が実装に無い"
        case ast.FunctionDef(name=name):
            declared = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            actual = list(inspect.signature(getattr(module, name)).parameters)
            return None if declared == actual else f"関数 {name} の引数 {declared} が実装 {actual} と違う"
        case ast.ClassDef(name=name) if not name.startswith("_"):
            declared = [
                s.target.id for s in node.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            ]
            actual = [f.name for f in dataclasses.fields(getattr(module, name))]
            return None if declared == actual else f"record {name} の欄 {declared} が実装 {actual} と違う"
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


@pytest.mark.parametrize("module", [readiness_model, readiness_handlers], ids=["readiness_model", "readiness_handlers"])
def test_the_stub_matches_the_hy_module(module: ModuleType) -> None:
    # 宣言の名は .hy に在り、関数の引数の名と順・record の欄の名と順は実装と同じ(宣言だけが先へ行かない)。
    stub = _stub(module)
    assert _mismatches(module, stub) == []
    assert _declared_names(stub) == DECLARED[module]


def test_a_dropped_report_field_is_found() -> None:
    # 失敗ケース: 宣言の ReportReady から欄を 1 つ外すと、食い違いとして名指される。
    stub = _stub(readiness_model)
    for node in stub.body:
        if isinstance(node, ast.ClassDef) and node.name == "ReportReady":
            node.body = [s for s in node.body if not (isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name) and s.target.id == "role")]
    assert _mismatches(readiness_model, stub) == ["record ReportReady の欄 ['ready', 'reason'] が実装 ['ready', 'reason', 'role'] と違う"]
