"""手元の runner sim-cluster の型の宣言(doeff_cluster/sim/local.pyi)の失敗ケース(#2374)。

local.hy は Hy の module で型の宣言が無かったので、担い手 SimWorker・process ごとの外の世界 ProcessOutside・入口 sim-cluster を
名指す使い手の strict に、書き手に直せない Unknown の赤(Type of "SimWorker" is unknown・Return type is unknown ほか)が出ていた。
→ local.pyi で宣言する。宣言を外すと 1 本目が赤になり、宣言が実装から離れると 2 本目が赤になる。

外の世界の欄(SimOutside の handlers・effects・per-process、ProcessOutside の handlers・effects、SimPlan の per-process)が型の引数の
無い list / tuple / Callable だと、使い手が欄を読んだ値が list[Unknown] などになり、使い手の strict に型逃げ無しには消せない赤が出た
(使い手の repo の速さを測る module で 5 行・8 件 — #4254・親 #3167)。→ 欄に型の引数を書く。外すと 1 本目(欄を読むテスト用の定義
probe-outside-fields)と 3 本目(.pyi の欄の注記)が赤になる。
"""

import ast
import inspect
import json
import shutil
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

# local は .hy の module — 先に hy を読んで import hook を有効にしてから読む(検だけを単独で走らせても読めるように)。
import hy  # noqa: F401
import pytest
from doeff_cluster.sim import local

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_cluster.sim.local [sim-cluster SimWorker SimOutside ProcessOutside SimProcess ProcessesOf KillWorker])
(import doeff_events [MemoryBroker])

(defk probe-workers [names]
  {:pre [(: names (get tuple #(str ...)))] :post [(: % (get list SimWorker))] :tags {:context "probe" :role "judgment"}}
  "名の列から担い手の列を作る(欄の型が読める)。"
  (lfor n names (SimWorker :name n :provides (frozenset ["net"]) :task-reserve 0 :prepare-seconds 1.0)))

(defk probe-per-process [effects]
  {:pre [(: effects (get tuple #(type ...)))] :post [(: % (get Callable #([str str] ProcessOutside)))] :tags {:context "probe" :role "foundation"}}
  "process ごとの外の世界を作る関数(SimOutside.per-process の形)。"
  (fn [job worker] (ProcessOutside :handlers #() :effects effects)))

(defk probe-scenario []
  {:pre [] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "筋書き: 担い手を落とし、process の列を読む(effect の答えの型が読める)。"
  (<- killed int (KillWorker "w1"))
  (<- processes (get tuple #(SimProcess ...)) (ProcessesOf "svc"))
  (+ killed (len processes) (sum (gfor p processes p.started-ms))))

(defk probe-run [system]
  {:pre [(: system System)] :post [(: % int)] :tags {:context "probe" :role "entry"}}
  "入口 sim-cluster が筋書きの答えの型をそのまま返す。"
  (<- workers (get list SimWorker) (probe-workers #("w1" "w2")))
  (<- per-process (get Callable #([str str] ProcessOutside)) (probe-per-process #(int)))
  (val outside (SimOutside :handlers [] :effects #() :per-process per-process))
  (<- answer int (sim-cluster system (probe-scenario) :workers (tuple workers) :outside outside :notice-broker (MemoryBroker)))
  answer)

(defk probe-outside-fields [outside]
  {:pre [(: outside SimOutside)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "外の世界の欄を読む(handler の列・effect の型のタプル・process ごとの外の世界を作る関数の答えの欄の型が読める)。"
  (val made (if (is outside.per-process None)
                (ProcessOutside :handlers #())
                (outside.per-process "svc" "w1")))
  (val handlers (+ (list made.handlers) outside.handlers))
  (val effects (+ made.effects outside.effects))
  (+ (len handlers) (sum (gfor e effects (len e.__name__)))))
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
def test_users_of_sim_worker_and_process_outside_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def _stub() -> ast.Module:
    return ast.parse(Path(inspect.getfile(local)).with_suffix(".pyi").read_text(encoding="utf-8"))


def test_the_stub_matches_local_hy() -> None:
    # 宣言の名は local.hy に在り、関数の引数の名と順・record と effect の欄の名と順は実装と同じ(宣言だけが先へ行かない)。
    stub = _stub()
    constants = [
        node.target.id
        for node in stub.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    functions = [node for node in stub.body if isinstance(node, ast.FunctionDef)]
    classes = {
        node.name: node
        for node in stub.body
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_")
    }
    declared = constants + [f.name for f in functions] + list(classes)
    assert [name for name in declared if not hasattr(local, name)] == []
    for function in functions:
        stub_parameters = [a.arg for a in function.args.args] + [
            a.arg for a in function.args.kwonlyargs
        ]
        assert stub_parameters == list(
            inspect.signature(getattr(local, function.name)).parameters
        ), function.name
    for name, node in classes.items():
        actual = getattr(local, name)
        stub_fields = [
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        ]
        if hasattr(actual, "__dataclass_fields__"):
            assert stub_fields == [f.name for f in fields(actual)], name


# 型の引数を書かないと使い手の strict で中身が Unknown になる総称の名。
GENERIC_NAMES = frozenset({"list", "tuple", "dict", "set", "frozenset", "Callable"})


def _bare_generics(annotation: ast.expr) -> list[str]:
    """注記の中で、型の引数を付けずに書かれた総称の名(`list`・`Callable | None` の Callable など)。"""
    subscripted = [node.value for node in ast.walk(annotation) if isinstance(node, ast.Subscript)]
    return [
        node.id
        for node in ast.walk(annotation)
        if isinstance(node, ast.Name)
        and node.id in GENERIC_NAMES
        and not any(node is value for value in subscripted)
    ]


def test_the_outside_fields_carry_type_arguments() -> None:
    # 失敗ケース(#4254): 外の世界の欄が型の引数の無い list / tuple / Callable だと、使い手が欄を読んだ値が Unknown を含む。
    stub = _stub()
    classes = {node.name: node for node in stub.body if isinstance(node, ast.ClassDef)}
    annotations = {
        f"{name}.{item.target.id}": item.annotation
        for name in ("SimOutside", "ProcessOutside", "SimPlan")
        for item in classes[name].body
        if isinstance(item, ast.AnnAssign)
        and isinstance(item.target, ast.Name)
        and (name != "SimPlan" or item.target.id == "per_process")
    }
    assert sorted(annotations) == [
        "ProcessOutside.effects",
        "ProcessOutside.handlers",
        "SimOutside.effects",
        "SimOutside.handlers",
        "SimOutside.per_process",
        "SimPlan.per_process",
    ]
    bare = {where: _bare_generics(annotation) for where, annotation in annotations.items()}
    assert {where: names for where, names in bare.items() if names} == {}


def test_the_stub_declares_every_name_other_modules_use() -> None:
    # 使い手(doeff-cluster の検)が import する名は全部宣言に在る(宣言に無い名は使い手の strict で unknown import symbol の赤)。
    # 契約の effect(shared/intent/cluster_control — #3029)は `from … import X as X` で読み直す(as つきの import は型の宣言での
    # 明示の読み直し — as の無い import は読み直しに数えない)。
    stub = _stub()
    declared = (
        {
            node.target.id
            for node in stub.body
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        }
        | {node.name for node in stub.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        | {
            alias.asname
            for node in stub.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
            if alias.asname is not None
        }
    )
    used = {
        "sim_cluster",
        "wall_sim_cluster",
        "SimWorker",
        "ProcessOutside",
        "SimOutside",
        "SimProcess",
        "SimLink",
        "SimReport",
        "ServiceReadiness",
        "SimPreparation",
        "SimCoordinatorRun",
        "CoordinatorStep",
        "CoordinatorSteps",
        "SimChild",
        "SimParts",
        "HostTruth",
        "ProcessesOf",
        "ReadCoordinator",
        "KillWorker",
        "StartWorker",
        "StopWorker",
        "StopCoordinator",
        "CrashCoordinator",
        "SharedRows",
        "DrainWorker",
        "ClientLink",
        "Redeclare",
        "FailRoute",
        "PreparationsOf",
        "WatchFailuresOf",
        "ReportsOf",
        "ReadinessOf",
        "SettleDeployment",
        "KubeCalls",
        "DeclareRollout",
        "CutWorker",
        "AwaitProcessStarted",
        "CoordinatorRuns",
        "Crash",
        "PartsOf",
        "HostTruthOf",
        "EndProcess",
        "coordinator_answers",
        "host_answers",
        "run_context_of",
        "send_request",
        "sim_process",
        "ended_process",
        "heartbeat",
        "note_watch",
        "SIM_START_MS",
        "SIM_URL",
        "SimDeadlockError",
        "SimDeadlock",
        "WaitSnapshot",
        "LiveProcess",
        "BusinessWait",
        "deadlock_of",
    }
    assert sorted(used - declared) == []
