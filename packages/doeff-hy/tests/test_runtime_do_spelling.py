"""doeff-hy の macro の source(`.hy` の form)の中で、実行時の `do` の置き場 `doeff.do` を綴る所が、補助の表の `do` の行
(macros.hy の `_runtime-do`)の 1 か所だけである事の検。

表の検(test_helper_imports_table.py)は一覧 `_DEFINITIONS` に載せた macro の展開だけを見るので、新しい macro が `doeff.do` を
自分で綴ると、一覧に足すまで見えない。ここは `.hy` の form を読み、文字 `"doeff.do"` と `doeff.do` を import する form を、
それを含む top の定義の名ごとに数える。docstring と註の中の言及は数えない(docstring は文全体が 1 つの文字なので
`"doeff.do"` と等しくならず、註は form にならない)。`.py` の module の import(lazy.py の `from doeff.do import do`)は展開ではない
ので数えない。
"""

from collections.abc import Iterator
from pathlib import Path

import doeff_hy
import hy

_SOURCE = Path(doeff_hy.__file__).parent
_IMPORT = hy.models.Symbol("import")
_RUNTIME_DO_MODULE = ("'(. doeff do)", "'doeff.do")


def _forms(form: object) -> Iterator[object]:
    yield form
    if isinstance(form, hy.models.Sequence):
        for item in form:
            yield from _forms(item)


def _top_name(top: object) -> str:
    """top の定義(`(defn 名 …)`・`(defmacro 名 …)` など)の名。名の無い form は先頭の記号。"""
    if isinstance(top, hy.models.Expression) and len(top) > 1:
        return str(top[1]) if isinstance(top[1], hy.models.Symbol) else str(top[0])
    return "?"


def _spellings(path: Path) -> list[tuple[str, str, str]]:
    """file の中で `doeff.do` を綴る所の #(file の名 top の定義の名 綴りの種類)。"""
    found: list[tuple[str, str, str]] = []
    for top in hy.read_many(path.read_text(encoding="utf-8"), filename=str(path)):
        for form in _forms(top):
            if isinstance(form, hy.models.String) and str(form) == "doeff.do":
                found.append((path.name, _top_name(top), "文字"))
            elif (
                isinstance(form, hy.models.Expression)
                and len(form) > 1
                and form[0] == _IMPORT
                and hy.repr(form[1]) in _RUNTIME_DO_MODULE
            ):
                found.append((path.name, _top_name(top), "import"))
    return found


def test_runtime_do_is_spelled_only_in_the_table_row() -> None:
    """`doeff.do` を綴る所は `_runtime-do` の文字 1 つだけ — 他の macro は `_runtime-do-import` か `_runtime-do` を通す。"""
    found = [spelling for path in sorted(_SOURCE.glob("*.hy")) for spelling in _spellings(path)]
    assert found == [("macros.hy", "_runtime-do", "文字")], found
