"""doeff_hy.wire の型(wire.pyi)の失敗ケース(agora-redesign #2282・#2279 の子)。

wire.hy は Hy の module で、型の宣言(wire.pyi)が無いと pyright は `parse`・`Malformed` を Unknown として読み、parse の答えを
使う所の赤が書き手に直せない形で連なる。宣言が在れば:

- `(<- row (parse Row raw))` の row は `Row | Malformed`。Malformed で絞った後の欄の読みは型どおり通り、
  型の取り違え(str の欄を int として足す)は赤になる。
- import した wire の名(parse・Malformed)に reportUnknown* が出ない。
"""

import contextlib
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest

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
