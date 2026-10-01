"""doeff_hy.wire の型(wire.pyi)の失敗ケース(agora-redesign #2282・#2279 の子)。

wire.hy は Hy の module で、型の宣言(wire.pyi)が無いと pyright は `parse`・`Malformed` を Unknown として読み、parse の答えを
使う所の赤が書き手に直せない形で連なる。宣言が在れば:

- `(<- row (parse Row raw))` の row は `Row | Malformed`。Malformed で絞った後の欄の読みは型どおり通り、
  型の取り違え(str の欄を int として足す)は赤になる。
- import した wire の名(parse・Malformed)に reportUnknown* が出ない。
"""

import contextlib
import dataclasses
import importlib
import inspect
import io
import json
import shutil
import sys
import typing
from dataclasses import dataclass
from pathlib import Path

import pytest

import doeff_hy  # noqa: F401  # Hy の import hook を有効にする
from doeff_hy.wire import WireShape

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

MODULE = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defwire])
(import doeff_hy.wire [Malformed parse])

(defwire Row
  "検体の行。"
  {:names :camel :unknown :ignore}
  (#^ str name)
  (#^ int count))

(defk name-of [raw]
  {:pre [(: raw str)] :post [(: % str)]}
  (<- row (parse Row raw))
  (if (isinstance row Malformed)
      "malformed"
      row.name))

(defk wrong-sum [raw]
  {:pre [(: raw str)] :post [(: % int)]}
  (<- row (parse Row raw))
  (if (isinstance row Malformed)
      0
      (+ row.name 1)))
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


@needs_pyright
def test_wire_names_are_not_unknown(probe: Path) -> None:
    # import した parse・Malformed と、parse の答えの row に「型が分からない」の赤が出ない(wire.pyi が無いと Unknown)。
    unknown = [e for e in _check(probe).errors() if e[0].startswith("reportUnknown")]
    assert not [e for e in unknown if any(n in e[2] for n in ('"parse"', '"Malformed"', '"row"'))], unknown


@needs_pyright
def test_a_parsed_field_used_as_the_wrong_type_is_red(probe: Path) -> None:
    errors = _check(probe).errors()
    # 正しい使い(Malformed で絞った後に str の欄を str として返す — 16 行目)は赤にならない。
    assert not [e for e in errors if e[1] == 16], errors
    # str の欄 name に 1 を足す(23 行目)と型の取り違えで赤。
    assert [e for e in errors if e[1] == 23 and e[0] == "reportOperatorIssue"], errors


# defwire の値を dump に渡す検体(agora-redesign #2296)。dump / dump-json の引数は WireValue(`__doeff_wire__: ClassVar[WireShape]`
# の Protocol)。defwire の展開は値を setattr で置き、型検査の展開はその記帳を外すので、型の本体に ClassVar の注記が無いと
# defwire の型は WireValue を満たさないと読まれる。素の defrecord の値(wire の形を持たない)は赤のまま(stub は緩めない)。
DUMP_MODULE = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])
(import doeff_hy.json_value [JsonValue])
(import doeff_hy.wire [dump dump-json])

(defwire Status
  "検体の状態。"
  {:names :camel}
  (#^ str phase))

(defwire Run
  "検体の行(:tags・:check・既定値つきの欄)。"
  {:tags {:context "probe" :role "type"}
   :names :snake
   :check [(> (len run-id) 0)]}
  (#^ str run-id)
  (#^ Status status)
  (setv #^ int tries 0))

(defrecord Plain
  "wire の形を持たない型。"
  {}
  (#^ str phase))

(defk status-json [phase]
  {:pre [(: phase str)] :post [(: % JsonValue)]}
  (<- out (dump (Status :phase phase)))
  out)

(defk run-text [run]
  {:pre [(: run Run)] :post [(: % str)]}
  (<- out (dump-json run))
  out)

(defk plain-json [phase]
  {:pre [(: phase str)] :post [(: % JsonValue)]}
  (<- out (dump (Plain :phase phase)))
  out)
"""


@pytest.fixture
def dump_probe(tmp_path: Path) -> Path:
    (tmp_path / "probe.hy").write_text(DUMP_MODULE, encoding="utf-8")
    return tmp_path


def _line_of(text: str, marker: str) -> int:
    """検体の中で marker を含む行の番号(1 から)。"""
    return next(n for n, line in enumerate(text.splitlines(), start=1) if marker in line)


@needs_pyright
def test_a_defwire_value_satisfies_wire_value(dump_probe: Path) -> None:
    errors = _check(dump_probe).errors()
    # defwire の値を dump・dump-json に渡す所に赤が無い(WireValue を満たす)。
    passed = (_line_of(DUMP_MODULE, "(dump (Status"), _line_of(DUMP_MODULE, "(dump-json run)"))
    assert not [e for e in errors if e[1] in passed], errors
    # 素の defrecord の値を dump に渡すと WireValue を満たさない赤のまま(stub を緩めていない)。
    plain = _line_of(DUMP_MODULE, "(dump (Plain")
    assert [e for e in errors if e[1] == plain and e[0] == "reportArgumentType" and "WireValue" in e[2]], errors
    # 型の本体の注記は欄と読まれない(構築子に __doeff_wire__ の引数を求めない)。defwire を 2 つ並べても展開の import が
    # 重ねの赤を出さない。
    assert not [e for e in errors if "__doeff_wire__" in e[2] or e[0] == "reportDuplicateImport"], errors


def test_the_wire_annotation_is_not_a_field() -> None:
    # 型の本体の ClassVar の注記は欄にならない: dataclass の欄・__match_args__・構築子の引数・pydantic の欄(wire の JSON)は
    # 書いた欄だけ。実行時の __doeff_wire__ は setattr が置いた WireShape。
    tests_dir = str(Path(__file__).resolve().parent)
    if tests_dir not in sys.path:
        sys.path.insert(0, tests_dir)
    row = importlib.import_module("defwire_deftests").LandingRow
    names = ("lane_id", "state", "landed_at", "ratio", "notes")
    assert tuple(f.name for f in dataclasses.fields(row)) == names
    assert row.__match_args__ == ()  # kw_only の dataclass は位置の欄を持たない
    assert tuple(inspect.signature(row).parameters) == names
    assert isinstance(row.__doeff_wire__, WireShape)
    assert typing.get_origin(row.__annotations__["__doeff_wire__"]) is typing.ClassVar
    assert tuple(row.__doeff_wire__.adapter.json_schema()["properties"]) == (
        "laneId",
        "state",
        "landedAt",
        "ratio",
        "notes",
    )
