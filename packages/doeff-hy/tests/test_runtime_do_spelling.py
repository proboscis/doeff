"""Checks that, inside the macro sources of doeff-hy (`.hy` forms), the only place that spells the runtime location of `do`, `doeff.do`, is the `do` row of the helper table
(`_runtime-do` in macros.hy).

The table test (test_helper_imports_table.py) sees only the expansions of macros listed in `_DEFINITIONS`, so if a new macro spells `doeff.do`
itself, it stays invisible until added to the list. This test reads the `.hy` forms and counts the string `"doeff.do"` and forms that import `doeff.do`
per top-level definition name that contains them. Mentions in docstrings and comments are not counted (a docstring is one whole-sentence string, so it is not
equal to `"doeff.do"`, and a comment is not a form). The import in a `.py` module (`from doeff.do import do` in lazy.py) is not an expansion,
so it is not counted.
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
    """Name of a top-level definition (`(defn name ...)`, `(defmacro name ...)`, etc.). For a form with no name, the first symbol."""
    if isinstance(top, hy.models.Expression) and len(top) > 1:
        return str(top[1]) if isinstance(top[1], hy.models.Symbol) else str(top[0])
    return "?"


def _spellings(path: Path) -> list[tuple[str, str, str]]:
    """#(file-name top-definition-name kind-of-spelling) of each place in the file that spells `doeff.do`."""
    found: list[tuple[str, str, str]] = []
    for top in hy.read_many(path.read_text(encoding="utf-8"), filename=str(path)):
        for form in _forms(top):
            if isinstance(form, hy.models.String) and str(form) == "doeff.do":
                found.append((path.name, _top_name(top), "string"))
            elif (
                isinstance(form, hy.models.Expression)
                and len(form) > 1
                and form[0] == _IMPORT
                and hy.repr(form[1]) in _RUNTIME_DO_MODULE
            ):
                found.append((path.name, _top_name(top), "import"))
    return found


def test_runtime_do_is_spelled_only_in_the_table_row() -> None:
    """The only place that spells `doeff.do` is the string in `_runtime-do` -- other macros go through `_runtime-do-import` or `_runtime-do`."""
    found = [spelling for path in sorted(_SOURCE.glob("*.hy")) for spelling in _spellings(path)]
    assert found == [("macros.hy", "_runtime-do", "string")], found
