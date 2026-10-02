"""計器の橋と報告の型の宣言(shared/protocol/meter_report.pyi・shared/intent/metrics_model.pyi・shared/intent/protocol.pyi)の失敗ケース
(#2810)。

meter_report.hy・metrics_model.hy・protocol.hy は Hy の module で型の宣言が無かったので、service の土台が本体を with-meter-report で包む所・
報告 ReportMetrics に答える所・coordinator の GET /metrics の答え PlainText を読む所の strict に、書き手に直せない Unknown の赤
(Type of "with_meter_report" is unknown・Type of "ReportMetrics" is unknown・Type of "PlainText" is unknown)が出ていた。→ 3 つの .pyi で
宣言する。宣言を外すと 1 本目が赤になり、宣言が実装から離れると 2 本目・3 本目が赤になる(test_readiness_static_types.py と同じ形 —
dataclass でない class は、親の class の並びで照らす)。
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

import hy  # noqa: F401  # .hy の module の import hook(3 つとも .hy の module — 検だけを単独で走らせても読めるように)
import pytest
from doeff_cluster.shared.intent import metrics_model, protocol
from doeff_cluster.shared.protocol import meter_report

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk defhandler <-])
(import doeff [Program EffectBase with-handlers])
(import doeff_core_effects.meter_effects [MeterSettings])
(import doeff_core_effects.memory_meter [memory-meter-handler])
(import doeff_cluster.shared.intent.metrics_model [ReportMetrics])
(import doeff_cluster.shared.intent.protocol [PlainText])
(import doeff_cluster.shared.protocol.meter_report [with-meter-report])

(defk probe-reported [body]
  {:pre [(: body (| Program EffectBase))] :post [(: % int)] :tags {:context "probe" :role "foundation"}}
  "計器の答え手を外側に置き、本体を橋で包む(橋の答えが本体の答えの型のまま読める)。"
  (<- answer int (with-handlers [(memory-meter-handler (MeterSettings))] (with-meter-report body)))
  answer)

(defhandler dropped-reports []
  "報告を落とす答え手(報告の effect の型が読める)。"
  {:tags {:context "probe" :role "foundation"}}
  (ReportMetrics []
    (resume None)))

(defk probe-lines [answer]
  {:pre [(: answer PlainText)] :post [(: % int)] :tags {:context "probe" :role "judgment"}}
  "GET /metrics の答えの本文の行の数(答えの欄の型が読める)。"
  (len (.splitlines answer.text)))
"""

# 宣言の名の全部(module ごと)。宣言に名を足したら、ここにも足す(実装に在るかは _mismatch が検める)。
DECLARED = {
    meter_report: {"DEFAULT_REPORT_SECONDS", "report_metrics_of", "report_meter_once", "report_meter_every", "with_meter_report"},
    metrics_model: {"ReportMetrics", "ReadProcessGauges"},
    protocol: {
        "PROTOCOL_FORMAT",
        "WATCH_MAX_SECONDS",
        "ClusterTiming",
        "Request",
        "BodyInvalid",
        "PlainText",
        "NextRequests",
        "Reply",
        "CoordinatorStopRequested",
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
def test_users_of_the_meter_bridge_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def _stub(module: ModuleType) -> ast.Module:
    return ast.parse(Path(inspect.getfile(module)).with_suffix(".pyi").read_text(encoding="utf-8"))


def _class_mismatch(module: ModuleType, node: ast.ClassDef) -> str | None:
    """宣言の class 1 つが実装と食い違えば理由、一致すれば None — dataclass は欄の名と順、ほか(例外)は親の class の名で照らす。"""
    actual = getattr(module, node.name)
    if dataclasses.is_dataclass(actual):
        declared = [s.target.id for s in node.body if isinstance(s, ast.AnnAssign) and isinstance(s.target, ast.Name)]
        fields = [f.name for f in dataclasses.fields(actual)]
        return None if declared == fields else f"record {node.name} の欄 {declared} が実装 {fields} と違う"
    bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
    parents = [b.__name__ for b in actual.__bases__]
    return None if bases == parents else f"class {node.name} の親 {bases} が実装 {parents} と違う"


def _mismatch(module: ModuleType, node: ast.stmt) -> str | None:
    """宣言の 1 文が実装と食い違えばその文(名・関数の引数・class)、一致すれば None。"""
    match node:
        case ast.AnnAssign(target=ast.Name(id=name)) if not hasattr(module, name):
            return f"宣言の名 {name} が実装に無い"
        case ast.FunctionDef(name=name) if not hasattr(module, name):
            return f"宣言の関数 {name} が実装に無い"
        case ast.ClassDef(name=name) if not name.startswith("_") and not hasattr(module, name):
            return f"宣言の class {name} が実装に無い"
        case ast.FunctionDef(name=name):
            declared = [a.arg for a in node.args.args] + [a.arg for a in node.args.kwonlyargs]
            actual = list(inspect.signature(getattr(module, name)).parameters)
            return None if declared == actual else f"関数 {name} の引数 {declared} が実装 {actual} と違う"
        case ast.ClassDef(name=name) if not name.startswith("_"):
            return _class_mismatch(module, node)
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


@pytest.mark.parametrize("module", [meter_report, metrics_model, protocol], ids=["meter_report", "metrics_model", "protocol"])
def test_the_stub_matches_the_hy_module(module: ModuleType) -> None:
    # 宣言の名は .hy に在り、関数の引数の名と順・record の欄の名と順・例外の親は実装と同じ(宣言だけが先へ行かない)。
    stub = _stub(module)
    assert _mismatches(module, stub) == []
    assert _declared_names(stub) == DECLARED[module]


def test_a_dropped_argument_and_a_wrong_parent_are_found() -> None:
    # 失敗ケース: 宣言の with-meter-report から引数 seconds を外す・BodyInvalid の親を RuntimeError にすると、食い違いとして名指される。
    stub = _stub(meter_report)
    for node in stub.body:
        if isinstance(node, ast.FunctionDef) and node.name == "with_meter_report":
            node.args.args = [a for a in node.args.args if a.arg != "seconds"]
            node.args.defaults = []
    assert _mismatches(meter_report, stub) == ["関数 with_meter_report の引数 ['body'] が実装 ['body', 'seconds'] と違う"]
    stub = _stub(protocol)
    for node in stub.body:
        if isinstance(node, ast.ClassDef) and node.name == "BodyInvalid":
            node.bases = [ast.Name(id="RuntimeError")]
    assert _mismatches(protocol, stub) == ["class BodyInvalid の親 ['RuntimeError'] が実装 ['ValueError'] と違う"]
