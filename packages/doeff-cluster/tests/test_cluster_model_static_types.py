"""coordinator の型の宣言(doeff_cluster/coordinator/intent/cluster_model.pyi)の失敗ケース(#2447)。

cluster_model.hy は Hy の module で型の宣言が無かったので、Rollout の宣言の型 RolloutSpec を名指す使い手(使い手の repo の模擬の
世界の反例)の strict に、書き手に直せない Unknown の赤(Type of "RolloutSpec" is unknown ほか)が出た。→ cluster_model.pyi で
宣言する。宣言を外すと 1 本目が赤になり、宣言が実装から離れると 2 本目(欄の名と順・enum の値)が赤になる。
"""

import ast
import inspect
import json
import shutil
import subprocess
import sys
from dataclasses import fields
from enum import Enum
from pathlib import Path

import pytest

# cluster_model は .hy の module — 先に hy を読んで import hook を有効にしてから読む(検だけを単独で走らせても読めるように)。
import hy  # noqa: F401
from doeff_cluster.coordinator.intent import cluster_model as model

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState RolloutSpec RolloutTarget])

(defk probe-old-first [spec]
  {:pre [(: spec RolloutSpec)] :post [(: % RolloutTarget)] :tags {:context "probe" :role "judgment"}}
  "宣言の旧の相手を読む(欄の型が読める)。"
  spec.from-target)

(defk probe-rollouts [state]
  {:pre [(: state ClusterState)] :post [(: % (get list str))] :tags {:context "probe" :role "judgment"}}
  "状態の Rollout の新の相手の名を読む(状態の欄の要素の型が読める)。"
  (lfor r (.values state.rollouts) r.spec.to-target.name))

(defk probe-target [name]
  {:pre [(: name str)] :post [(: % RolloutTarget)] :tags {:context "probe" :role "judgment"}}
  "相手を作る(構成子の欄が読める)。"
  (RolloutTarget :kind "Service" :name name))
"""


def _errors(root: Path) -> list[tuple[str, int, str]]:
    # 型検査の道具は hook と同じく `python -m doeff_hy.static_check` で撃つ。
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
def test_users_of_rollout_spec_and_cluster_state_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def _stub() -> ast.Module:
    return ast.parse(Path(inspect.getfile(model)).with_suffix(".pyi").read_text(encoding="utf-8"))


def test_the_stub_matches_cluster_model_hy() -> None:
    # 宣言の名は cluster_model.hy に在り、dataclass の欄の名と順・enum の名と値は実装と同じ(宣言だけが先へ行かない)。
    stub = _stub()
    constants = [
        node.target.id
        for node in stub.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    classes = {node.name: node for node in stub.body if isinstance(node, ast.ClassDef)}
    assert [name for name in constants + list(classes) if not hasattr(model, name)] == []
    for name, node in classes.items():
        actual = getattr(model, name)
        stub_fields = [
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        ]
        if isinstance(actual, type) and issubclass(actual, Enum):
            members = [
                (item.targets[0].id, item.value.value)
                for item in node.body
                if isinstance(item, ast.Assign)
                and isinstance(item.targets[0], ast.Name)
                and isinstance(item.value, ast.Constant)
            ]
            assert members == [(m.name, m.value) for m in actual], name
        elif hasattr(actual, "__dataclass_fields__"):
            own = [f.name for f in fields(actual)]
            # IdleNextRequests は実行時は NextRequests の子 class — 宣言は自分の欄だけ(頭の註)。
            assert stub_fields == own[len(own) - len(stub_fields) :], name
            if name != "IdleNextRequests":
                assert stub_fields == own, name
        elif hasattr(actual, "_fields"):
            assert stub_fields == list(actual._fields), name


def test_the_stub_declares_every_name_cluster_model_defines() -> None:
    # 実装の公開の型と定数は全部宣言に在る(宣言に無い名は使い手の strict で unknown import symbol の赤)。
    stub = _stub()
    declared = {
        node.target.id
        for node in stub.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    } | {node.name for node in stub.body if isinstance(node, ast.ClassDef)}
    defined = {
        name
        for name, value in vars(model).items()
        if not name.startswith("_")
        and (
            (isinstance(value, type) and value.__module__ == model.__name__)
            or (name.isupper() and isinstance(value, (tuple, frozenset, dict)))
        )
    }
    assert sorted(defined - declared) == []
