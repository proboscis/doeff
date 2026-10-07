"""宿の契約の型の宣言(doeff_cluster/foundation/host_contract.pyi)の失敗ケース(#2197)。

host_contract.hy は Hy の module で型の宣言が無かったので、土台の組に environ の答え手を並べる使い手の strict に Unknown の赤が出ていた
(host-reader は shared/entry/host_reader に在る — ここに 1 版残した古い host-reader は 2026-10-03 に消した・#2167)。
→ host_contract.pyi で宣言する。宣言を外すと 1 本目が赤になり、宣言が実装から離れると 2 本目・3 本目が赤になる。
host_contract.pyi は手書きだったが、#3014 で足した this-program-path が載らず、道具で作り直した host_reader.pyi の import が宣言の無い名を
指したので、道具の出力(python -m doeff_hy.static_stub --write --replace)に置き換えた(以後の食い違いは test_generated_stubs も赤にする)。
"""

import ast
import dataclasses
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path

# host_contract は .hy の module — 先に hy を読んで import hook を有効にしてから読む(検だけを単独で走らせても読めるように)。
import hy  # noqa: F401
import pytest
from doeff_cluster.foundation import host_contract

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <-])
(import doeff [Program EffectBase with-handlers])
(import doeff_cluster.foundation.host_contract [environ-table-reader HOST-CONTRACT])
(import doeff_cluster.shared.entry.host_reader [host-reader])

(defk probe-host [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % int)] :tags {:context "probe" :role "foundation"}}
  "宿の答え手 2 つを並べた下で本文を走らせる(handler の型が読める)。"
  (<- answer int (with-handlers [host-reader (environ-table-reader {"NAME" "value"})] body))
  answer)

(defk probe-key []
  {:pre [] :post [(: % str)] :tags {:context "probe" :role "judgment"}}
  "契約の鍵の欄の型が読める。"
  HOST-CONTRACT.run-context-key)
"""


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
def test_users_of_the_host_handlers_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def _stub() -> ast.Module:
    return ast.parse(Path(inspect.getfile(host_contract)).with_suffix(".pyi").read_text(encoding="utf-8"))


def _mismatch(node: ast.stmt) -> str | None:
    """宣言の 1 文が実装と食い違えばその文(名・関数の引数・record の欄)、一致すれば None。"""
    match node:
        case ast.AnnAssign(target=ast.Name(id=name)) if not hasattr(host_contract, name):
            return f"宣言の名 {name} が実装に無い"
        case ast.FunctionDef(name=name) if not hasattr(host_contract, name):
            return f"宣言の関数 {name} が実装に無い"
        case ast.FunctionDef(name=name):
            declared = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            actual = list(inspect.signature(getattr(host_contract, name)).parameters)
            return None if declared == actual else f"関数 {name} の引数 {declared} が実装 {actual} と違う"
        case ast.ClassDef(name=name) if not name.startswith("_"):
            declared = [
                s.target.id for s in node.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)
            ]
            actual = [f.name for f in dataclasses.fields(getattr(host_contract, name))]
            return None if declared == actual else f"record {name} の欄 {declared} が実装 {actual} と違う"
        case _:
            return None


def _mismatches(stub: ast.Module) -> list[str]:
    """宣言の全部の食い違い(空なら一致)。"""
    return [found for node in stub.body if (found := _mismatch(node)) is not None]


def test_the_stub_matches_host_contract_hy() -> None:
    # 宣言の名は host_contract.hy に在り、関数の引数の名と順・record の欄の名と順は実装と同じ(宣言だけが先へ行かない)。
    stub = _stub()
    assert _mismatches(stub) == []
    declared = {
        node.target.id
        for node in stub.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    } | {node.name for node in stub.body if isinstance(node, ast.FunctionDef | ast.ClassDef) and not node.name.startswith("_")}
    assert declared == {"HostContract", "HOST_CONTRACT", "SIM_PASSABLE", "this_program_path", "environ_table_reader", "os_environ_reader", "environ_reader"}


def test_a_dropped_record_field_is_found() -> None:
    # 失敗ケース: 宣言の HostContract から欄を 1 つ外すと、食い違いとして名指される。
    stub = _stub()
    for node in stub.body:
        if isinstance(node, ast.ClassDef) and node.name == "HostContract":
            node.body = [
                s for s in node.body if not (isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name) and s.target.id == "versions_key")
            ]
    assert _mismatches(stub) == ["record HostContract の欄 ['run_context_key', 'program_key', 'program_env', 'notice_env'] が実装 "
                                 "['run_context_key', 'program_key', 'versions_key', 'program_env', 'notice_env'] と違う"]
