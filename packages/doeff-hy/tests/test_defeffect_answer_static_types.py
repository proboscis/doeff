"""defeffect の :answer の型が型検査の展開に残ることの失敗ケース(agora-redesign #2322)。

defeffect は答えの型を `__doeff_answer__` に置く。型検査の展開(doeff-hy-check)は記帳の setattr(`__doeff_…__`)を外すので、
答えの型を setattr で置くと :answer の式が型検査から消え、答えの型のためだけに import した名が reportUnusedImport の赤になっていた
(消すと実行時に壊れるので書き手は直せない)。展開は class の本体の `__doeff_answer__: ClassVar[object]` に置く:

- :answer にだけ現れる import の名は使われた import と読まれる(赤が無い)。:answer に現れない import は赤のまま(検は緩めていない)。
- 素の総称(`dict`)・`Callable | None` を :answer に書いても、値の位置で型として評価された赤(reportMissingTypeArgument・
  reportOperatorIssue)を足さない。
- 実行時の `__doeff_answer__` は :answer の式そのもので、ClassVar の注記は dataclass の欄にならない。
"""

import ast
import contextlib
import dataclasses
import inspect
import io
import json
import shutil
import typing
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
import hy
import pytest

from doeff import EffectBase

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

# 答えの型 Decimal・Fraction・Callable は :answer にだけ現れる。PurePath は :answer に現れない(本当に使われない import)。
MODULE = """\
(require doeff-hy.macros [defeffect])
(import collections.abc [Callable])
(import decimal [Decimal])
(import fractions [Fraction])
(import pathlib [PurePath])

(defeffect ReadAmount
  "検体の読み(答えの型は import した名の union)。"
  {:fields [(: key str)]
   :answer (| Decimal Fraction None)
   :tags {:context "probe" :role "intent"}})

(defeffect ReadTable
  "検体の読み(素の総称と Callable | None の答え)。"
  {:fields []
   :answer (| dict Callable None)
   :tags {:context "probe" :role "intent"}})
"""


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


@pytest.fixture
def probe(tmp_path: Path) -> Path:
    (tmp_path / "probe.hy").write_text(MODULE, encoding="utf-8")
    return tmp_path


def _line_of(marker: str) -> int:
    """検体の中で marker を含む行の番号(1 から)。"""
    return next(n for n, line in enumerate(MODULE.splitlines(), start=1) if marker in line)


@needs_pyright
def test_answer_only_imports_are_used_and_unused_ones_stay_red(probe: Path) -> None:
    errors = _check(probe).errors()
    unused = [e for e in errors if e[0] == "reportUnusedImport"]
    # :answer にだけ現れる名は使われた import(直しを外すと Decimal・Fraction・Callable の 3 件が赤に戻る)。
    assert not [
        e for e in unused if any(f'"{n}"' in e[2] for n in ("Decimal", "Fraction", "Callable"))
    ], unused
    # :answer に現れない import は赤のまま(展開が import を一律に使った扱いにしていない)。
    assert [e for e in unused if e[1] == _line_of("[PurePath]") and '"PurePath"' in e[2]], unused


@needs_pyright
def test_answer_does_not_add_type_form_errors(probe: Path) -> None:
    # 素の dict・Callable | None の :answer は、値の位置で型として評価された赤を出さない。defeffect の行には赤が無い。
    errors = _check(probe).errors()
    lines = range(_line_of("(defeffect ReadAmount"), len(MODULE.splitlines()) + 1)
    assert not [e for e in errors if e[1] in lines], errors


def test_projection_keeps_the_answer_in_the_class_body() -> None:
    # 型検査の展開の中で __doeff_answer__ は class の本体の ClassVar[object] の注記つきの代入で、:answer の名を読む。
    # 記帳の setattr(型検査が外す)には置かない。
    from doeff_hy.static_view import static_view

    with static_view():
        module = hy.compiler.hy_compile(hy.read_many(MODULE), "__main__", import_stdlib=False)
    assert isinstance(module, ast.Module)
    source = ast.unparse(module)
    assert (
        "__doeff_answer__: _doeff_ClassVar[object] = _doeff_cast(object, (Decimal, Fraction, None))"
        in source
    )
    assert "setattr(ReadAmount, '__doeff_answer__'" not in source


def test_runtime_answer_is_the_declared_type_and_not_a_field() -> None:
    namespace: dict[str, object] = {}
    hy.eval(hy.models.Expression([hy.models.Symbol("do"), *hy.read_many(MODULE)]), namespace)
    read_amount = namespace["ReadAmount"]
    read_table = namespace["ReadTable"]
    assert isinstance(read_amount, type)
    assert issubclass(read_amount, EffectBase)
    assert dataclasses.is_dataclass(read_amount)
    assert isinstance(read_table, type)
    assert vars(read_amount)["__doeff_answer__"] == Decimal | Fraction | None
    assert vars(read_table)["__doeff_answer__"] == dict | Callable | None
    assert typing.get_origin(read_amount.__annotations__["__doeff_answer__"]) is typing.ClassVar
    assert tuple(f.name for f in dataclasses.fields(read_amount)) == ("key",)
    assert tuple(inspect.signature(read_amount).parameters) == ("key",)
    assert vars(read_amount(key="a")) == {"key": "a"}
