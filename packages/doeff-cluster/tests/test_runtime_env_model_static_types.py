"""runtime_env_model.hy の型の宣言(runtime_env_model.pyi)の失敗ケース。

runtime_env_model.hy は Hy の module で型の宣言が無かったので、EnvVar・RuntimeEnv を使う側(SubmitDetached.environ を EnvVar の組で
組む呼び手)の strict に、書き手に直せない Unknown の赤(Type of "EnvVar" is unknown・Argument type is unknown ほか)が出ていた。
→ runtime_env_model.pyi で宣言する。宣言を外すと 1 本目が赤になり、宣言が実装から離れると 2 本目が赤になる。
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

import hy  # noqa: F401  # .hy の module(runtime_env_model)を読む import hook を有効にする
from doeff_cluster.shared.intent import runtime_env_model

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <-])
(import doeff_cluster.shared.intent.runtime_env_model [EnvVar RuntimeEnv RepoCheckout PythonProject env-key])

(defk probe-environ [names]
  {:pre [(: names (get tuple #(str ...)))] :post [(: % (get tuple #(EnvVar ...)))] :tags {:context "probe" :role "judgment"}}
  "名の列から子の環境変数の組を作る(SubmitDetached.environ の形)。"
  (tuple (gfor n names (EnvVar :name n :value "x"))))

(defk probe-key [env]
  {:pre [(: env RuntimeEnv)] :post [(: % str)] :tags {:context "probe" :role "judgment"}}
  "宣言の鍵を引く(defk の答えの型が読める)。"
  (<- key str (env-key env "linux-x86_64"))
  (val first (get env.repos 0))
  (+ key first.name env.project.python))
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
def test_users_of_env_var_and_runtime_env_get_no_unknown_types(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if "unknown" in e[2].lower()], errors


def test_the_stub_matches_runtime_env_model_hy() -> None:
    # 宣言の名は runtime_env_model.hy に在り、関数の引数の名と順・record の欄・enum の値は実装と同じ(宣言だけが先へ行かない)。
    stub = ast.parse(Path(runtime_env_model.__file__).with_suffix(".pyi").read_text(encoding="utf-8"))
    constants = [
        node.target.id
        for node in stub.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    functions = [node for node in stub.body if isinstance(node, ast.FunctionDef)]
    classes = {node.name: node for node in stub.body if isinstance(node, ast.ClassDef)}
    declared = constants + [f.name for f in functions] + list(classes)
    assert [name for name in declared if not hasattr(runtime_env_model, name)] == []
    for function in functions:
        assert [a.arg for a in function.args.args] == list(
            inspect.signature(getattr(runtime_env_model, function.name)).parameters
        ), function.name
    for name, node in classes.items():
        actual = getattr(runtime_env_model, name)
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
        if stub_values:
            assert stub_values == {member.name: member.value for member in actual}, name
        elif hasattr(actual, "__dataclass_fields__"):
            assert stub_fields == [f.name for f in fields(actual)], name
