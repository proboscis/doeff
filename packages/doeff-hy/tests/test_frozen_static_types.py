"""doeff_hy.frozen の型(frozen.pyi)の検(agora-redesign #2311・#2245)。

frozen.hy は Hy の module で、型の宣言(frozen.pyi)が無いと pyright は `FrozenMap` と凍らせる関数を Unknown として読み、
FrozenMap を返す定義の答え・FrozenMap の値の読みに、書き手に直せない reportUnknown* が連なる(agora の基点に同じ種類が
約 1,400 件)。

- 失敗ケース: 同じ小さな .hy を、frozen.hy だけを置いた写しと frozen.pyi も置いた写しの 2 通りで doeff-hy-check --strict に
  かける(写しは別の名の package — doeff_hy の名のままだと、pyright は写しに無い frozen.pyi を入れた doeff_hy から読む)。外すと FrozenMap と答えが Unknown の赤になり、置くと消える。置いた側では値の型の取り違え(int の値に文字列を
  足す)が赤になる(FrozenMap が値の型を運んでいる)。型引数を書かない `FrozenMap` は FrozenMap[object] で、型引数の
  書き忘れの赤にならない。
- 一致: stub が宣言する名は実行時の module に在り、関数の引数の名は実装と同じ。FrozenMap は実装と同じく
  `Mapping[str, V]`(型引数は値の型 1 つ)。
"""

import ast
import contextlib
import inspect
import io
import json
import shutil
import sys
import types
import typing
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
from doeff_hy import frozen

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

PACKAGE = Path(frozen.__file__).parent

MODULE = """\
(require doeff-hy.macros [defk <- val])
(import collections.abc [Mapping])
(import frozen_copy.frozen [FrozenMap frozen-map-of freeze-json frozen-json-object])

(val SHALLOW (frozen-map-of {"a" 1} "probe"))

(defk labels []
  {:pre [] :post [(: % FrozenMap)]}
  (FrozenMap {"a" "x"}))

(defk label-count [m]
  {:pre [(: m FrozenMap)] :post [(: % int)]}
  (len (lfor key m key)))

(defk first-label []
  {:pre [] :post [(: % str)]}
  (<- m (labels))
  (str (get m "a")))

(defk shallow-sum []
  {:pre [] :post [(: % int)]}
  (+ (get SHALLOW "a") 1))

(defk frozen-of [text]
  {:pre [(: text str)] :post [(: % str)]}
  (str (freeze-json text)))

(defk takes-json [v]
  {:pre [(: v (| str int float bool None (get Mapping #(str object)) (get tuple #(object ...))))] :post [(: % None)]}
  None)

(defk frozen-feeds-json []
  {:pre [] :post [(: % None)]}
  (<- (takes-json (freeze-json {"a" [1 2]})))
  (<- (takes-json (get (frozen-json-object {"b" "x"} "probe") "b"))))

(defk wrong-sum []
  {:pre [] :post [(: % int)]}
  (+ (get SHALLOW "a") "x"))
"""

#: frozen.pyi を外すと Unknown になる名。
UNKNOWN_NAMES: tuple[str, ...] = ('"FrozenMap"', '"frozen_map_of"', '"SHALLOW"', '"m"')


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


def _line_of(marker: str) -> int:
    """検体の中で marker を含む行の番号(1 から)。"""
    return next(n for n, line in enumerate(MODULE.splitlines(), start=1) if marker in line)


def _check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_stub: bool) -> Run:
    """frozen.hy(with_stub なら frozen.pyi も)の写しの package frozen_copy を import の根に置き、検体を doeff-hy-check --strict に
    かける。写しの dir は sys.path にも置く — doeff-hy-check が import 先を「.hy なので型が見えない」module と見分けるのは
    sys.path の .hy なので、agora が doeff の .hy の module を読む時と同じ扱いになる。"""
    from doeff_hy.static_check import main

    copy = tmp_path / ("stubbed" if with_stub else "bare") / "frozen_copy"
    copy.mkdir(parents=True)
    (copy / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(PACKAGE / "frozen.hy", copy / "frozen.hy")
    if with_stub:
        shutil.copy(PACKAGE / "frozen.pyi", copy / "frozen.pyi")
    monkeypatch.setattr(sys, "path", [str(copy.parent), *sys.path])
    root = tmp_path / "root"
    root.mkdir()
    (root / "probe.hy").write_text(MODULE, encoding="utf-8")
    (root / "pyrightconfig.json").write_text(json.dumps({"extraPaths": [str(copy.parent)]}), encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--strict", "--no-cache", str(root / "probe.hy")])
    text = out.getvalue()
    return Run(tuple(json.loads(text)) if text.strip() else ())


@needs_pyright
def test_without_the_stub_frozen_map_is_unknown(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    unknown = [e for e in _check(tmp_path, monkeypatch, with_stub=False).errors() if e[0].startswith("reportUnknown")]
    missing = [name for name in UNKNOWN_NAMES if not any(name in e[2] for e in unknown)]
    assert missing == [], unknown


@needs_pyright
def test_with_the_stub_nothing_is_unknown_and_a_wrong_value_type_is_red(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    errors = _check(tmp_path, monkeypatch, with_stub=True).errors()
    # 正しい使い(検体の頭から wrong-sum の前まで)には赤が無い — Unknown も、素の FrozenMap の型引数の書き忘れも。
    right = range(1, _line_of("(defk wrong-sum"))
    assert [e for e in errors if e[1] in right] == [], errors
    # frozen-map-of は写像の値の型を運ぶ: int の値に文字列を足すと赤。
    wrong = _line_of('(+ (get SHALLOW "a") "x")')
    assert [e for e in errors if e[1] == wrong and e[0] == "reportOperatorIssue"], errors


def test_the_stub_matches_frozen_hy() -> None:
    stub = ast.parse(PACKAGE.joinpath("frozen.pyi").read_text(encoding="utf-8"))
    classes = [node.name for node in stub.body if isinstance(node, ast.ClassDef)]
    functions = [node for node in stub.body if isinstance(node, ast.FunctionDef)]
    assert [name for name in [*classes, *(f.name for f in functions)] if not hasattr(frozen, name)] == []
    for function in functions:
        declared = [a.arg for a in function.args.args]
        assert declared == list(inspect.signature(getattr(frozen, function.name)).parameters), function.name
    # 実装の FrozenMap は Mapping[str, V](鍵は文字列・型引数は値の型 1 つ)— stub の基底と同じ形。
    (base,) = types.get_original_bases(frozen.FrozenMap)
    assert typing.get_origin(base) is Mapping
    key, value = typing.get_args(base)
    assert key is str and isinstance(value, typing.TypeVar)
    (frozen_map,) = [node for node in stub.body if isinstance(node, ast.ClassDef) and node.name == "FrozenMap"]
    assert ast.unparse(frozen_map.bases[0]) == "Mapping[str, _V]"
