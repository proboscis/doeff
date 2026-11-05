"""The spelling of Hy's internal names (macro table names, export-name table names, the prefix Hy gives temporary names) is held by exactly one definition home.

The definition home of the table names Hy puts in a module namespace (``_hy_macros``, ``_hy_reader_macros``, ``_hy_export_macros``) and of the temporary-name prefix (``_hy_``) is
``doeff_hy_bytecode_guard._records`` (``MACRO_TABLE``, ``READER_TABLE``, ``EXPORT_NAMES``, ``GENERATED_NAME_PREFIX``,
``ANON_VAR_NAME``); users hold no spelling and look names up through records' constants and functions (``macro_tables``, ``exported_macro_names``,
``fold_anon_var_numbers``). If the spelling is copied to another file, there are two or more places to fix when Hy renames, and the test run against the Hy version in uv.lock
(test_bytecode_guard.py) measures only records' reading, so copies go stale unmeasured (the form of the
3 places found — doeff_effect_analyzer._result_cache, doeff_hy._static_baseline, and the export-name table of macro_use).

This test counts the following spellings in the repo's production source (.py and .hy — tests, docs, and record dirs are not counted), and if even one is outside records it
names it and goes red.
- A string constant without whitespace (a name, a regex spelling — bytes and Hy's ``#[[…]]`` strings too) containing a name that starts with ``_hy_``.
- A form that reads one of the 3 table names (the values of records' constants) as a name, attribute, or imported name (Python's ``module._hy_macros``, ``from m import _hy_macros``,
  Hy's symbol ``(. module _hy_macros)``).
Descriptive text (strings containing whitespace), docstrings, and comments may write the names. Names with ``_hy_`` in the middle of a word such as ``_could_be_hy_src``, and names starting with ``__hy_``,
are not counted (nor are doeff's own function names such as ``_hy_symbols`` unless they are table names).

Only spelled names are counted; other ways of writing a name (a regex with the prefix split such as ``_(?:hy|lazy)_``, a string built by concatenating names, a computed-name
``getattr``) are not counted. The dependency itself is measured by test_bytecode_guard.py against the real Hy, so this scan serves to stop the plain form in which "the spelling becomes
a second definition home", not to replace measurement (static_baseline's regex was a form with the prefix split,
so this scan could not count it).
"""

from __future__ import annotations

import ast
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

from doeff_hy_bytecode_guard._records import EXPORT_NAMES, MACRO_TABLE, READER_TABLE

ROOT = Path(__file__).resolve().parents[3]
#: The definition home of the names (only this file holds the spelling).
HOME = ROOT / "packages" / "doeff-hy" / "src" / "doeff_hy_bytecode_guard" / "_records.py"
#: Directories not descended into (hidden dirs, build output, tests, docs, records — they hold no production source).
NOT_WALKED = re.compile(r"^(\..*|node_modules|target|build|dist|__pycache__|tests|docs|notes|sessions|publications)$")
#: Test files (those in places whose dir is not tests).
TEST_FILE = re.compile(r"^(test_.*|.*_test|conftest)\.(py|hy)$")
#: The table names Hy puts in a module namespace (the values are records' constants — no spelling is held here).
TABLE_NAMES = frozenset({MACRO_TABLE, READER_TABLE, EXPORT_NAMES})
#: The prefix of names Hy assigns (``_hy_`` in the middle of a word or of ``__hy_`` — ``_could_be_hy_src``, ``__hy_injected__``, etc. — is not counted).
HY_NAME = re.compile(r"(?<![A-Za-z0-9_])_hy_")
#: The spelling of Hy source strings (including those spanning lines), and bracket strings ``#[[…]]`` and ``#[name[…]name]``.
HY_STRING = re.compile(r'"(?:[^"\\]|\\.)*"')
HY_BRACKET_STRING = re.compile(r"#\[([^\[\]]*)\[(.*?)\]\1\]", re.DOTALL)
HY_COMMENT = re.compile(r";[^\n]*")
#: The table names as Hy symbols (``-`` is also a symbol character, so only when the neighbors are not name characters).
HY_TABLE_SYMBOL = re.compile(
    r"(?<![A-Za-z0-9_-])(" + "|".join(re.escape(name) for name in sorted(TABLE_NAMES)) + r")(?![A-Za-z0-9_-])"
)


def _production_sources() -> Iterator[Path]:
    """The repo's production .py and .hy (excluding HOME, test files, and under NOT_WALKED dirs)."""
    for current, dirs, files in os.walk(ROOT):
        dirs[:] = sorted(name for name in dirs if not NOT_WALKED.match(name))
        for name in sorted(files):
            path = Path(current, name)
            if path.suffix in {".py", ".hy"} and not TEST_FILE.match(name) and path != HOME:
                yield path


def _is_spelling(text: str) -> bool:
    """Whether a string constant is a spelling of a Hy internal name (a name or regex spelling — not descriptive text containing whitespace)."""
    return not any(char.isspace() for char in text) and HY_NAME.search(text) is not None


class _Spelling(NamedTuple):
    """Where one spelling of a Hy internal name is: line and spelling."""

    line: int
    text: str


def _python_spelling(node: ast.AST) -> _Spelling | None:
    """The spelling of a Hy internal name that one tree node reads (line, spelling — a string constant, bytes, or the name, attribute, or imported name of a table name). None if none."""
    match node:
        case ast.Constant(value=str() as text) as found if _is_spelling(text):
            return _Spelling(found.lineno, text)
        case ast.Constant(value=bytes() as raw) as found if _is_spelling(raw.decode("latin-1")):
            return _Spelling(found.lineno, raw.decode("latin-1"))
        case (ast.Attribute(attr=name) | ast.Name(id=name) | ast.alias(name=name)) as found if name in TABLE_NAMES:
            return _Spelling(found.lineno, name)
        case _:
            return None


def _line_of(text: str, offset: int) -> int:
    """The line of the character at offset in text (1-based)."""
    return text.count("\n", 0, offset) + 1


def _hy_spellings(text: str) -> Iterator[tuple[int, str]]:
    """Spellings of Hy internal names in Hy source (line, spelling) — strings, bracket strings, table-name symbols. Comments and descriptive text are not counted."""
    code = "\n".join("" if line.lstrip().startswith(";") else line for line in text.split("\n"))
    for found in HY_BRACKET_STRING.finditer(code):
        if _is_spelling(found.group(2)):
            yield _line_of(code, found.start()), found.group(2)
    for found in HY_STRING.finditer(code):
        if _is_spelling(found.group(0)):
            yield _line_of(code, found.start()), found.group(0)
    # Emptying strings (keeping lines) and then removing comments avoids being fooled by ``;`` inside a string, and names in comments and descriptive text are not counted.
    bare = HY_BRACKET_STRING.sub(lambda found: "\n" * found.group(0).count("\n"), code)
    bare = HY_STRING.sub(lambda found: '"' + "\n" * found.group(0).count("\n") + '"', bare)
    uncommented = HY_COMMENT.sub("", bare)  # comments are within a line, so line counting is unchanged
    for found in HY_TABLE_SYMBOL.finditer(uncommented):
        yield _line_of(uncommented, found.start()), found.group(1)


def _spellings(path: Path) -> Iterator[tuple[int, str]]:
    """Spellings of Hy internal names in a file (line, spelling — in line order). A docstring is descriptive text, so it contains whitespace and is not counted."""
    text = path.read_text(encoding="utf-8")
    if "_hy_" not in text:
        return
    if path.suffix == ".hy":
        yield from sorted(_hy_spellings(text), key=lambda spelled: spelled[0])
        return
    found = (_python_spelling(node) for node in ast.walk(ast.parse(text, filename=str(path))))
    yield from sorted((spelled for spelled in found if spelled is not None), key=lambda spelled: spelled[0])


def test_no_file_but_records_spells_a_name_hy_gives_its_tables_and_temporaries() -> None:
    """Spellings of Hy internal names: 0 outside the definition home records.py."""
    copies = [
        f"{path.relative_to(ROOT)}:{line}: {text}"
        for path in _production_sources()
        for line, text in _spellings(path)
    ]
    assert copies == [], (
        f"Spellings of Hy internal names (names starting with _hy_, table names) exist outside records: {copies} — look names up through the constants of {HOME.relative_to(ROOT)}"
        "(MACRO_TABLE, READER_TABLE, EXPORT_NAMES, GENERATED_NAME_PREFIX) and functions (macro_tables, exported_macro_names, "
        "fold_anon_var_numbers). If a form is missing, add it to records (the test that runs on Hy version bumps measures records' reading)"
    )


def test_the_scan_sees_a_spelling_in_each_form_a_copy_takes(tmp_path: Path) -> None:
    """Checks the scan does not miss (failure cases): name spellings, regex spellings, f-string prefixes, bytes, attribute reads, imported names, and Hy source strings and
    symbols are counted, while descriptive text, docstrings, comments, ``_hy_`` in the middle of a word or of ``__hy_``, and names of doeff's own functions starting with ``_hy_`` are
    not counted."""
    python = tmp_path / "copy.py"
    python.write_text(
        '"""Description: reads _hy_macros."""\n'
        "from other import _hy_export_macros\n"
        'A = "_hy_macros"\n'
        'B = r"(_hy_[a-z]+_)(_hy_.*)(_[0-9]+)"\n'
        'C = f"_hy_{x}_{y}"\n'
        'D = "_could_be_hy_src"\n'
        'E = "error: _hy_macros is missing"\n'
        "F = module._hy_reader_macros\n"
        'G = b"_hy_macros"\n'
        "H = _hy_macros\n"
        'I = "__hy_injected__"\n'
        'J = "foo__hy_bar"\n'
        "K = _hy_symbols(form)\n",
        encoding="utf-8",
    )
    hy = tmp_path / "copy.hy"
    hy.write_text(
        '; "_hy_commented" is a comment\n'
        '(defn f []\n  "docstring: description of _hy_macros"\n'
        '  (get (vars module) "_hy_macros")  ; reads _hy_export_macros\n'
        "  (. module _hy_reader_macros)\n"
        "  (setv c _hy_export_macros)\n"
        "  #[[_hy_macros]]\n"
        "  (_hy_symbols form))\n",
        encoding="utf-8",
    )
    assert [text for _, text in _spellings(python)] == [
        "_hy_export_macros",
        "_hy_macros",
        "(_hy_[a-z]+_)(_hy_.*)(_[0-9]+)",
        "_hy_",
        "_hy_reader_macros",
        "_hy_macros",
        "_hy_macros",
    ]
    assert [text for _, text in _spellings(hy)] == [
        '"_hy_macros"',
        "_hy_reader_macros",
        "_hy_export_macros",
        "_hy_macros",
    ]
    assert [line for line, _ in _spellings(hy)] == [4, 5, 6, 7]
