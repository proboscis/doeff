"""LeaseOp の答え(shared/intent/semaphore_model)と drain の頼み(worker/intent/drain_model・worker/protocol/drain_requests)の型の宣言
(.pyi)の失敗ケース(#2523・#2541 の .pyi の漏れの直し)。

3 つの module は Hy の module で型の宣言が無かったので、LeaseAnswer で答えを受ける・AskDrain に drain-request で答える使い手の strict に、
書き手に直せない Unknown の赤(Type of "LeaseAnswer" is unknown・Type of "AskDrain" is unknown・Type of "drain_request" is unknown ほか)が
出た(使い手の repo の模擬の世界の付け替えが commit の型の門で止まった)。→ .pyi で宣言する。宣言を外すと 1 本目が赤になり、宣言が
実装から離れると 2 本目が赤になる(local.pyi の検 test_local_static_types.py と同じ形)。
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

import hy  # noqa: F401  # Hy の module を import する前に hy の import hook を有効にする(検だけを単独で走らせても読めるように)
import pytest

from doeff_cluster.shared.intent import semaphore_model
from doeff_cluster.worker.intent import drain_model
from doeff_cluster.worker.protocol import drain_requests

MODULES: dict[str, ModuleType] = {
    "semaphore_model": semaphore_model,
    "drain_model": drain_model,
    "drain_requests": drain_requests,
}

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

PROBE = """\
(require doeff-hy.macros [defk <-])
(import doeff_cluster.shared.intent.semaphore_model [LeaseOp LeaseAnswer LeaseStanding HELD])
(import doeff_cluster.worker.intent.drain_model [AskDrain CoordinatorCall])
(import doeff_cluster.worker.protocol.drain_requests [drain-request])

(defk probe-lease [name token]
  {:pre [(: name str) (: token str)] :post [(: % bool)] :tags {:context "probe" :role "program"}}
  "lease を取り、答えの欄を型のまま読む。"
  (<- answer LeaseAnswer (LeaseOp name "claim" token 1 15000))
  (<- standing str (LeaseStanding name))
  (and answer.ok (= answer.ttl-ms 15000) (= standing HELD)))

(defk probe-drain [ask]
  {:pre [(: ask AskDrain)] :post [(: % str)] :tags {:context "probe" :role "protocol"}}
  "drain の頼みを本番の綴りで要求の形にし、path を読む。"
  (val request (drain-request ask.name ask.ttl-seconds ask.own-boot))
  (get request 1))
"""


def _errors(root: Path) -> list[tuple[str, int, str]]:
    # 型検査の道具は hook と同じく `python -m doeff_hy.static_check` で撃つ(test_local_static_types.py と同じ)。
    done = subprocess.run(
        [sys.executable, "-m", "doeff_hy.static_check", "--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    text = done.stdout
    diagnostics = json.loads(text) if text.strip() else []
    return [(str(d["rule"]), int(str(d["line"])), str(d["message"])) for d in diagnostics if d["severity"] == "error"]


@needs_pyright
def test_users_of_lease_answer_and_ask_drain_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(PROBE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


@pytest.mark.parametrize("name", sorted(MODULES))
def test_each_stub_matches_its_hy_module(name: str) -> None:
    # 宣言の名は実装に在り、関数の引数の名と順・dataclass の欄の名と順は実装と同じ(宣言だけが先へ行かない)。
    module = MODULES[name]
    stub = ast.parse(Path(inspect.getfile(module)).with_suffix(".pyi").read_text(encoding="utf-8"))
    constants = [node.target.id for node in stub.body if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)]
    functions = [node for node in stub.body if isinstance(node, ast.FunctionDef)]
    classes = {node.name: node for node in stub.body if isinstance(node, ast.ClassDef) and not node.name.startswith("_")}
    declared = constants + [f.name for f in functions] + list(classes)
    assert [n for n in declared if not hasattr(module, n)] == [], name
    for function in functions:
        stub_parameters = [a.arg for a in function.args.args] + [a.arg for a in function.args.kwonlyargs]
        actual = getattr(module, function.name)
        # handler の値(coordinator-calls)は引数を取って handler を返す関数 — 実装の引数の名と順で比べる。
        assert stub_parameters == list(inspect.signature(actual).parameters), (name, function.name)
    for class_name, node in classes.items():
        actual = getattr(module, class_name)
        stub_fields = [item.target.id for item in node.body if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)]
        if hasattr(actual, "__dataclass_fields__"):
            assert stub_fields == [f.name for f in fields(actual)], (name, class_name)
