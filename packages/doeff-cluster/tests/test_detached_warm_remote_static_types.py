"""detached_model・warm_model・remote_model と構築関数(detached_rules・warm_rules・remote_rules)の型の宣言(.pyi)の失敗ケース(#2564)。

構築関数(submit-detached-task・warm-runtime-env・remote-job)の宣言は答えの型(DetachedSubmitAnswer・WarmAnswer)と欄の型
(VersionDiff ほか)を intent の Hy の module から読む。その module に型の宣言が無いと、構築関数を呼ぶ使い手の strict に、書き手に
直せない赤(Type of "submit_detached_task" is partially unknown・Argument type is partially unknown ほか)が出た(使い手の
repo の commit の hook が 7 件で止めた)。→ intent の 3 module に .pyi を置く。宣言を外すと 1 本目が赤になり、宣言が実装から離れると
2 本目が赤になる。
"""

import ast
import inspect
import json
import shutil
import subprocess
import sys
from dataclasses import fields
from pathlib import Path
from types import ModuleType

import pytest

import hy  # noqa: F401  # .hy の module を読む import hook を有効にする
from doeff_cluster.shared.core import detached_rules, remote_rules, warm_rules
from doeff_cluster.shared.intent import detached_model, remote_model, warm_model

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <-])
(import doeff_cluster.shared.intent.detached_model [DetachedSubmitted DetachedUnreachable DetachedSubmitAnswer])
(import doeff_cluster.shared.intent.warm_model [WarmAnswer WarmState WarmUnreachable])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv EnvVar])
(import doeff_cluster.shared.core.detached_rules [submit-detached-task])
(import doeff_cluster.shared.core.warm_rules [warm-runtime-env])
(import doeff_cluster.shared.core.remote_rules [remote-job])

(defk probe-body []
  {:pre [] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "送る Program の本体。"
  1)

(defk probe-submit [key needs]
  {:pre [(: key str) (: needs (get frozenset str))] :post [(: % bool)] :tags {:context "probe" :role "program"}}
  "切り離した task を構築関数で送り、答えの型で分ける。"
  (<- answer DetachedSubmitAnswer (submit-detached-task (probe-body) key :needs needs :environ #((EnvVar :name "A" :value "x"))))
  (match answer
    (DetachedSubmitted :created created) created
    (DetachedUnreachable) False))

(defk probe-warm [env needs]
  {:pre [(: env RuntimeEnv) (: needs (get frozenset str))] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "温める頼みを構築関数で出し、答えの型で分ける。"
  (<- answer WarmAnswer (warm-runtime-env env needs 60.0 "probe"))
  (match answer
    (WarmState :ready ready) (len ready)
    (WarmUnreachable) 0))

(defk probe-remote [needs]
  {:pre [(: needs (get frozenset str))] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "task を構築関数で走らせ、戻り値を program の型で受ける。"
  (<- value int (remote-job (probe-body) :needs needs))
  value)
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
def test_callers_of_the_builders_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


@pytest.mark.parametrize(
    "module",
    [detached_model, warm_model, remote_model, detached_rules, warm_rules, remote_rules],
    ids=lambda m: m.__name__,
)
def test_the_stub_matches_the_hy_module(module: ModuleType) -> None:
    # 宣言の名は .hy に在り、関数の引数の名と順(キーワード引数だけの物も含む)・dataclass の欄は実装と同じ(宣言だけが先へ行かない)。
    stub = ast.parse(Path(module.__file__ or "").with_suffix(".pyi").read_text(encoding="utf-8"))
    constants = [
        node.target.id
        for node in stub.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    functions = [node for node in stub.body if isinstance(node, ast.FunctionDef)]
    classes = {node.name: node for node in stub.body if isinstance(node, ast.ClassDef)}
    declared = constants + [f.name for f in functions] + list(classes)
    assert [name for name in declared if not hasattr(module, name)] == []
    for function in functions:
        assert [a.arg for a in function.args.args + function.args.kwonlyargs] == list(
            inspect.signature(getattr(module, function.name)).parameters
        ), function.name
    for name, node in classes.items():
        actual = getattr(module, name)
        stub_fields = [
            item.target.id
            for item in node.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        ]
        if hasattr(actual, "__dataclass_fields__"):
            assert stub_fields == [f.name for f in fields(actual)], name
