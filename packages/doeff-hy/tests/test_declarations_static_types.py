"""deff の :tags・:effects の展開と doeff_hy.declarations の型(declarations.pyi)の失敗ケース(agora-redesign #2335・#2324 の子)。

deff は定義の後に `setattr(名, '__doeff_tags__', doeff_hy.declarations.DefinitionTags(…))` と
`setattr(名, '__doeff_effects__', doeff_hy.declarations.effect_types(…))` を出す。型検査の展開は module の直下の記帳を外すが、
class の中の deff(method)の記帳は class の本体に残る。declarations.hy は Hy の module で型の宣言が無かったので、使う側の
method ごとに strict の reportUnknownMemberType が 1 組(`declarations`・`DefinitionTags`)出ていた(agora の
controllers/screen/tests/world.hy で method 32 本に 64 件)。→ declarations.pyi で宣言する。
"""

import ast
import contextlib
import inspect
import io
import json
import shutil
from dataclasses import dataclass, fields
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
from doeff_hy import declarations

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [deff defeffect])

(defeffect Ping
  "検体の effect。"
  {:answer int :tags {:context "probe" :role "intent"}})

(deff shout [text]
  {:pre [(: text str)] :post [(: % str)] :tags {:context "probe" :role "judgment"} :effects [Ping]}
  "module の直下の deff。"
  (.upper text))

(defclass Probe []
  "deff の method を持つ検体の class。"
  (deff greet [self name]
    {:pre [(: self Probe) (: name str)] :post [(: % str)] :tags {:context "probe" :role "judgment"} :effects [Ping]}
    "class の中の deff(method)。"
    (+ "hello " name))
  (deff plain [self]
    {:pre [(: self Probe)] :post [(: % int)] :tags {:context "probe" :role "judgment" :spells "json"}}
    "spells つきの method。"
    1))
"""

#: 前に deff の行に出ていた赤の文言(どれも書き手に直せない)。
FORMER_ERRORS: tuple[str, ...] = (
    'Type of "declarations" is unknown',
    'Type of "DefinitionTags" is unknown',
    'Type of "effect_types" is unknown',
)


@dataclass(frozen=True)
class Run:
    """doeff-hy-check を 1 回走らせた結果(JSON の診断)。"""

    diagnostics: tuple[dict[str, object], ...]

    def errors(self) -> list[tuple[str, int, str]]:
        return [
            (str(d["rule"]), int(str(d["line"])), str(d["message"]))
            for d in self.diagnostics
            if d["severity"] == "error"
        ]


def _check(root: Path) -> Run:
    from doeff_hy.static_check import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@needs_pyright
def test_deff_tags_and_effects_have_no_unreadable_red(tmp_path: Path) -> None:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    errors = _check(tmp_path).errors()
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert not [e for e in errors if any(m in e[2] for m in FORMER_ERRORS)], errors
    assert not [e for e in errors if "unknown" in e[2].lower() and "declarations" in e[2]], errors


def test_the_stub_matches_declarations_hy() -> None:
    # declarations.pyi が宣言する名は declarations.hy に在り、関数の引数の名と順と DefinitionTags の欄は実装と同じ
    # (宣言だけが先へ行かない)。
    stub = ast.parse(Path(declarations.__file__).with_suffix(".pyi").read_text(encoding="utf-8"))
    constants = [
        node.target.id
        for node in stub.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    functions = [node for node in stub.body if isinstance(node, ast.FunctionDef)]
    classes = [node.name for node in stub.body if isinstance(node, ast.ClassDef)]
    declared = constants + [f.name for f in functions] + classes
    assert [name for name in declared if not hasattr(declarations, name)] == []
    for function in functions:
        assert [a.arg for a in function.args.args] == list(
            inspect.signature(getattr(declarations, function.name)).parameters
        ), function.name
    assert classes == ["DefinitionTags"]
    assert [f.name for f in fields(declarations.DefinitionTags)] == ["context", "role", "spells", "reads"]
