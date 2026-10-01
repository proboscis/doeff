"""worker_model.hy と job_model.hy の型の宣言(worker_model.pyi・job_model.pyi)の失敗ケース(#2435)。

2 つは Hy の module で型の宣言が無かったので、手元の runner sim-cluster の :policy に渡す WorkerPolicy や、観測(WorldView・
CodeView・ProcessView)を組む使い手の strict に、書き手に直せない Unknown の赤(Type of "WorkerPolicy" is unknown・Argument type
is unknown ほか)が出ていた。→ 2 つの .pyi で宣言する。宣言を外すと 1 本目が赤になり、宣言が実装から離れると 2 本目が、使い手が
import する名が宣言から欠けると 3 本目が赤になる。
"""

import ast
import json
import re
import shutil
import subprocess
import sys
import types
from dataclasses import fields
from enum import Enum
from pathlib import Path
from typing import get_args

import hy  # .hy の module(worker_model・job_model)を読む import hook を有効にする(名の mangle にも使う)
import pytest
from doeff_cluster.shared.intent import job_model
from doeff_cluster.worker.intent import worker_model

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULES = (worker_model, job_model)

MODULE = """\
(require doeff-hy.macros [defk <-])
(import doeff_cluster.shared.intent.job_model [JobSpec JobPhase])
(import doeff_cluster.shared.intent.service_model [System])
(import doeff_cluster.sim.local [sim-cluster])
(import doeff_cluster.worker.intent.worker_model [WorkerPolicy WorldView CodeView CodeState ProcessView JobStatus
                                                  ObserveWorld ReadDesired DesiredJobs DesiredUnreadable])

(defk probe-policy [grace]
  {:pre [(: grace int)] :post [(: % WorkerPolicy)] :tags {:context "probe" :role "judgment"}}
  "停止の猶予だけを変えた判断の設定を作る(sim-cluster の :policy に渡す形)。"
  (WorkerPolicy :stop-grace-ms grace :tick-seconds 0.25))

(defk probe-world [spec]
  {:pre [(: spec JobSpec)] :post [(: % WorldView)] :tags {:context "probe" :role "judgment"}}
  "観測を組む(欄の型が読める)。"
  (WorldView #((CodeView spec.revision CodeState.READY :path "/tree"))
             #((ProcessView spec.name spec 1 42 0))))

(defk probe-status [spec]
  {:pre [(: spec JobSpec)] :post [(: % JobStatus)] :tags {:context "probe" :role "judgment"}}
  "状態の行を組む(段階の Enum が読める)。"
  (JobStatus spec.name JobPhase.RUNNING spec.revision spec.revision 42 1))

(defk probe-observe []
  {:pre [] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "筋書き: 観測と宣言を読む(effect の答えの型が読める)。"
  (<- world WorldView (ObserveWorld))
  (<- desired (| DesiredJobs DesiredUnreadable) (ReadDesired))
  (+ (len world.codes) (sum (gfor p world.processes p.pid))
     (match desired
       (DesiredJobs :jobs jobs) (len jobs)
       _ 0)))

(defk probe-run [system]
  {:pre [(: system System)] :post [(: % int)] :tags {:context "probe" :role "entry"}}
  "入口 sim-cluster の :policy に WorkerPolicy を渡す。"
  (<- policy WorkerPolicy (probe-policy 3000))
  (<- answer int (sim-cluster system (probe-observe) :policy policy))
  answer)
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
def test_users_of_worker_policy_and_world_view_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def _stub(module: types.ModuleType) -> ast.Module:
    return ast.parse(Path(str(module.__file__)).with_suffix(".pyi").read_text(encoding="utf-8"))


def _union_names(node: ast.expr) -> list[str]:
    # `A | B | C` の宣言を名の列に(左から)。
    if isinstance(node, ast.BinOp):
        return _union_names(node.left) + _union_names(node.right)
    assert isinstance(node, ast.Name), ast.dump(node)
    return [node.id]


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.__name__.rsplit(".", 1)[-1])
def test_the_stub_matches_the_hy_module(module: types.ModuleType) -> None:
    # 宣言の名は .hy に在り、record と effect の欄の名と順・enum の値・和の型の要素は実装と同じ(宣言だけが先へ行かない)。
    stub = _stub(module)
    classes = {node.name: node for node in stub.body if isinstance(node, ast.ClassDef)}
    unions = {
        node.targets[0].id: node.value
        for node in stub.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    assert [name for name in [*classes, *unions] if not hasattr(module, name)] == []
    for name, node in classes.items():
        actual = getattr(module, name)
        stub_fields = [
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        ]
        stub_values = {
            item.targets[0].id: item.value.value
            for item in node.body
            if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant)
        }
        if issubclass(actual, Enum):
            assert stub_values == {member.name: member.value for member in actual}, name
        else:
            assert stub_fields == [f.name for f in fields(actual)], name
        for method in (item for item in node.body if isinstance(item, ast.FunctionDef)):
            assert hasattr(actual, method.name), (name, method.name)
    for name, value in unions.items():
        assert _union_names(value) == [t.__name__ for t in get_args(getattr(module, name))], name


def _declared(module: types.ModuleType) -> set[str]:
    stub = _stub(module)
    return {
        node.name for node in stub.body if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    } | {
        node.targets[0].id
        for node in stub.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }


def test_the_stubs_declare_every_name_the_package_imports() -> None:
    # doeff-cluster の Hy の file(src と検)が import する名は全部宣言に在る(宣言に無い名は使い手の strict で unknown import symbol の赤)。
    package = Path(__file__).resolve().parents[1]
    for module in MODULES:
        form = re.compile(r"\(import " + re.escape(module.__name__) + r" \[([^\]]*)\]")
        used: set[str] = set()
        for path in sorted(package.rglob("*.hy")):
            for match in form.finditer(path.read_text(encoding="utf-8")):
                words = re.sub(r":as \S+", "", match.group(1)).split()
                used |= {hy.mangle(word) for word in words}
        assert used, module.__name__
        assert sorted(used - _declared(module)) == [], module.__name__
