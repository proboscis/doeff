"""coordinator の起動の読み直しと保存の行の読みの型の宣言(coordinator/entry/main.pyi・coordinator/protocol/cluster_json.pyi)の失敗ケース(#2760)。

main.hy と cluster_json.hy は Hy の module で型の宣言が無かったので、load-state・naming-from-json・task-record-from-json を使う使い手
(使い手の repo の模擬の世界の検)の strict に、書き手に直せない Unknown の赤(Type of "load_state" is unknown ほか)が出る。
#2760 で task-record-from-json を defk にし、#2751 で load-state を defk にしたので、使い手は答えの Program を <- で受ける形に
書き直す — その時に型が読めないと、使い手の型検査の門が新しい赤で止まる。→ 2 つの .pyi で宣言する。宣言を外すと 1 本目が赤になり、
宣言が実装から離れると 2 本目・3 本目が赤になる(test_readiness_static_types.py と同じ形)。
"""

import ast
import inspect
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import hy  # noqa: F401  # .hy の module の import hook(2 つとも .hy の module — 検だけを単独で走らせても読めるように)
import pytest
from doeff_cluster.coordinator.entry import main as coordinator_main
from doeff_cluster.coordinator.protocol import cluster_json

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <- val])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming ClusterState TaskRecord])
(import doeff_cluster.coordinator.protocol.store [DurableStore])
(import doeff_cluster.coordinator.protocol.cluster_json [naming-from-json task-record-from-json])
(import doeff_cluster.coordinator.entry.main [load-state])

(defk probe-boot [state-file store now]
  {:pre [(: state-file str) (: store DurableStore) (: now int)] :post [(: % ClusterState)] :tags {:context "probe" :role "program"}}
  "coordinator の起動の読み直しの答えを <- で受ける(使い手の模擬の世界の起動と同じ形)。"
  (<- state ClusterState (load-state state-file store now))
  state)

(defk probe-task [data]
  {:pre [(: data (get dict #(str object)))] :post [(: % TaskRecord)] :tags {:context "probe" :role "program"}}
  "保存の task の行の読みの答えを <- で受ける。"
  (<- task TaskRecord (task-record-from-json data))
  task)

(defk probe-naming [text]
  {:pre [(: text str)] :post [(: % str)] :tags {:context "probe" :role "program"}}
  "名の規則の読みの答えの欄が読める。"
  (val naming (naming-from-json text))
  naming.owner-scope)
"""

# 宣言の名の全部(module ごと)。宣言に名を足したら、ここにも足す(実装に在るかは _mismatch が検める)。
DECLARED = {
    coordinator_main: {"MODULE_TAGS", "board_file_rows", "legacy_state", "load_state", "main"},
    cluster_json: {
        "MODULE_TAGS",
        "RETIRED_NAMING_FIELDS",
        "handoff_watch_from_json",
        "naming_from_json",
        "task_record_to_json",
        "task_record_from_json",
        "stored_str",
        "stored_optional_str",
        "stored_int",
        "stored_optional_int",
        "stored_bool",
        "stored_optional_dict",
        "stored_items",
        "old_task_row_reason",
    },
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
def test_users_of_the_coordinator_boot_read_get_no_unknown_types(tmp_path: Path) -> None:
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
            return (
                None
                if declared == actual
                else f"関数 {name} の引数 {declared} が実装 {actual} と違う"
            )
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
    } | {
        node.name
        for node in stub.body
        if isinstance(node, ast.FunctionDef | ast.ClassDef) and not node.name.startswith("_")
    }


@pytest.mark.parametrize(
    "module", [coordinator_main, cluster_json], ids=["coordinator_entry_main", "cluster_json"]
)
def test_the_stub_matches_the_hy_module(module: ModuleType) -> None:
    # 宣言の名は .hy に在り、関数の引数の名と順は実装と同じ(宣言だけが先へ行かない)。
    stub = _stub(module)
    assert _mismatches(module, stub) == []
    assert _declared_names(stub) == DECLARED[module]


def test_a_dropped_boot_argument_is_found() -> None:
    # 失敗ケース: 宣言の load_state から引数を 1 つ外すと、食い違いとして名指される。
    stub = _stub(coordinator_main)
    for node in stub.body:
        if isinstance(node, ast.FunctionDef) and node.name == "load_state":
            node.args.args = [a for a in node.args.args if a.arg != "now"]
    assert _mismatches(coordinator_main, stub) == [
        "関数 load_state の引数 ['state_file', 'store'] が実装 ['state_file', 'store', 'now'] と違う"
    ]
