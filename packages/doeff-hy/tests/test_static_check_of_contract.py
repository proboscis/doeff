"""契約の型を `(of base arg …)` で書いた引数の、型検査のための注記(agora-redesign #2535)の失敗ケース。

`of` は macro で、契約の型を注記の文字列へ写す `_type-source` は macro を持たない hy-compile に式を渡していた。そのため
`(: rows (of tuple Row ...))` は呼び出し `of(tuple, Row, ...)` に compile され、注記にならず捨てられていた(引数は注記なし =
strict で「注記が無い・型が不明」の赤)。`(| (of tuple str ...) None)` は `of(tuple, str, ...) | None` という嘘の注記になっていた。
`(of …)` を同じ意味の `(get …)` へ写してから compile すれば:

- `(of …)`・入れ子・`|` の中の `(of …)` が、`get` で書いた時と同じ注記になる(単体)。
- strict で、`(of tuple str ...)` の引数を持つ defk に赤が 0 件。
- 注記は逃げていない: その引数に数の組を渡せば型の取り違えで赤。
"""

import contextlib
import io
import json
import shutil
from pathlib import Path

import hy
import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright が無い")

PROBE = """\
(require doeff-hy.macros [defk <- val])

(defk joined [names]
  {:pre [(: names (of tuple str ...))] :post [(: % str)]}
  (.join "," names))

(defk maybe-joined [names]
  {:pre [(: names (| (of tuple str ...) None))] :post [(: % str)]}
  (if (is names None) "" (.join "," names)))

(defk caller []
  {:pre [] :post [(: % str)]}
  (<- got str (joined {ARG}))
  got)
"""

BASE = {"ARG": '#("a" "b")'}


def _render(change: dict[str, str]) -> str:
    text = PROBE
    for key, value in (BASE | change).items():
        text = text.replace("{" + key + "}", value)
    return text


def _check(root: Path, text: str) -> list[dict[str, object]]:
    from doeff_hy.static_check import main

    (root / "probe.hy").write_text(text, encoding="utf-8")
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        main(["--root", str(root), "--json", "--no-cache", "--strict", str(root / "probe.hy")])
    printed = out.getvalue()
    found: list[dict[str, object]] = json.loads(printed) if printed.strip() else []
    return [d for d in found if d["severity"] == "error"]


def _line_of(text: str, fragment: str) -> int:
    return next(index for index, line in enumerate(text.splitlines(), 1) if fragment in line)


def test_of_forms_project_to_the_same_annotation_as_get() -> None:
    from doeff_hy.macros import _type_source

    cases = {
        "(of tuple str ...)": "tuple[str, ...]",
        "(of dict str int)": "dict[str, int]",
        "(of list (of tuple str int))": "list[tuple[str, int]]",
        "(| (of tuple str ...) None)": "tuple[str, ...] | None",
        "(get tuple #(str ...))": "tuple[str, ...]",
    }
    assert {src: _type_source(hy.read(src)) for src in cases} == cases
    # 呼び出しの残る式は注記にしない(嘘の注記より注記なし)。
    assert _type_source(hy.read("(| int (type x))")) is None


@needs_pyright
def test_strict_has_no_error_for_of_contracts(tmp_path: Path) -> None:
    assert _check(tmp_path, _render({})) == []


@needs_pyright
def test_of_contract_still_rejects_a_wrong_argument(tmp_path: Path) -> None:
    text = _render({"ARG": "#(1 2)"})
    errors = _check(tmp_path, text)
    line = _line_of(text, "(joined #(1 2))")
    assert [(e["rule"], e["line"]) for e in errors] == [("reportArgumentType", line)], errors
