"""Checks that a Hy module that forgot to require a macro, and a module that imported a macro with import, get the one line to add (initiative doeff-publish).

When a first-time reader removes ``(require doeff-hy.macros [do! <-])`` from the README's Hy example, Hy compiles ``do!`` as a function call and
at run time it becomes ``NameError: name 'hyx_doXexclamation_markX' is not defined`` (Hy's specification - we do not touch Hy). Importing a macro with
``(import doeff-hy.handle [defhandler])`` gives ``ImportError: cannot import name 'defhandler' ...``. In both cases the message is
produced by Python and does not say how to fix it. doeff-hy knows the macro names and their provider modules, so at the entry point that loads Hy modules
(get_code in doeff_hy_bytecode_guard - ``hy file.hy``, ``hy -m``, import, and pytest all go through it), it puts the one line to add in a warning.

Run a Hy module in a child process and read stderr (like the check that runs the README example, it uses ``-m hy`` in a fresh interpreter).
The shared code store is turned off (DOEFF_HY_CODE_STORE=off), and bytecode is written to a per-check store (the second run reads via .pyc).

"``import hy`` when importing .hy from Python" (card 18) adds no doeff code - the reasons are in the card's comment (list of responsibilities and necessity) and
the section Using Hy in docs/01-getting-started.md. The existing checks that run the README and chapter 01 code blocks cover the examples.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest
from doeff_hy_bytecode_guard import _records as records

#: The README's Hy example (the correct form, with the line ``(require doeff-hy.macros [do! <-])``).
CORRECT = """\
(require doeff-hy.handle [defhandler])
(require doeff-hy.macros [do! <-])
(import doeff [Ask run])

(defhandler ask-env
  (Ask [key]
    (resume (get {"greeting" "hello"} key))))

(setv greet
  (do!
    (<- greeting (Ask "greeting"))
    greeting))

(print (run (ask-env greet)))
"""
#: The same example with the require lines for ``do!`` and ``<-`` removed (card 17, cases/h01_hy_forgot_require_macros).
FORGOT_REQUIRE = CORRECT.replace("(require doeff-hy.macros [do! <-])\n", "")
#: ``defhandler`` brought in with import instead of require (cases/h02_hy_import_instead_of_require).
IMPORT_INSTEAD = CORRECT.replace(
    "(require doeff-hy.handle [defhandler])", "(import doeff-hy.handle [defhandler])"
)
#: Time limit of the child process before it is stretched for load (seconds). One run takes about 1-2 seconds (importing doeff and hy).
CHILD_DEADLINE_SECONDS = 50


def _run(
    directory: Path, deadline_scale_factor: float, *arguments: str
) -> subprocess.CompletedProcess[str]:
    """Run a fresh interpreter in a directory (bytecode is written to the directory's pyc - the second run reads .pyc)."""
    environment = {**os.environ, "DOEFF_HY_CODE_STORE": "off"}  # noqa: DOEFF004 — the single place where the check passes the current environment to the child process
    return subprocess.run(
        [sys.executable, "-E", "-s", "-X", f"pycache_prefix={directory / 'pyc'}", *arguments],
        cwd=directory,
        env=environment,
        capture_output=True,
        check=False,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
    )


def _hy_script(
    directory: Path, text: str, deadline_scale_factor: float
) -> subprocess.CompletedProcess[str]:
    """Write text to main.hy and run it with ``python -m hy main.hy``, the same as the README example."""
    (directory / "main.hy").write_text(text, encoding="utf-8")
    return _run(directory, deadline_scale_factor, "-m", "hy", "main.hy")


def test_a_forgotten_require_of_do_and_arrow_says_the_line_to_add(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    result = _hy_script(tmp_path, FORGOT_REQUIRE, deadline_scale_factor)
    # The failure itself is unchanged (still NameError) - the warning comes first and says the one line to fix.
    assert result.returncode != 0
    assert "NameError: name 'hyx_doXexclamation_markX' is not defined" in result.stderr
    assert "(require doeff-hy.macros [do! <-])" in result.stderr
    assert result.stderr.index("(require doeff-hy.macros [do! <-])") < result.stderr.index(
        "Traceback"
    )
    # The warning points to the line in main.hy that used it (the do! line).
    assert "main.hy:9:" in result.stderr


def test_the_second_run_reads_a_pyc_and_still_says_the_line(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    first = _hy_script(tmp_path, FORGOT_REQUIRE, deadline_scale_factor)
    assert list((tmp_path / "pyc").rglob("*.pyc")), (
        "the first run did not write .pyc - the second run would not go through .pyc"
    )
    second = _hy_script(tmp_path, FORGOT_REQUIRE, deadline_scale_factor)
    assert "(require doeff-hy.macros [do! <-])" in first.stderr
    assert "(require doeff-hy.macros [do! <-])" in second.stderr


def test_a_forgotten_require_in_an_imported_module_says_the_line_to_add(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    (tmp_path / "greet.hy").write_text(
        '(import doeff [Ask])\n(setv greet (do! (<- greeting (Ask "greeting")) greeting))\n',
        encoding="utf-8",
    )
    (tmp_path / "main.py").write_text("import hy\nimport greet\n", encoding="utf-8")
    result = _run(tmp_path, deadline_scale_factor, "main.py")
    assert result.returncode != 0
    assert "greet.hy:2:" in result.stderr
    assert "(require doeff-hy.macros [do! <-])" in result.stderr


def test_names_from_two_providers_get_one_line_each(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    text = "(defk answer [] 42)\n(defrecord Point [x y])\n"
    result = _hy_script(tmp_path, text, deadline_scale_factor)
    assert "(require doeff-hy.macros [defk])" in result.stderr
    assert "(require doeff-hy.record [defrecord])" in result.stderr
    # Name of the NameError raised at run time (a macro name that is not mangled stays as is)
    assert "NameError: name 'defk' is not defined" in result.stderr


def test_a_name_that_the_module_defines_or_imports_is_not_reported(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    # Defines its own functions do! and check, and imports val from another module - a correct module that uses functions with the same names as macros.
    (tmp_path / "helpers.hy").write_text("(defn val [x] x)\n", encoding="utf-8")
    text = """\
(import helpers [val])
(defn do! [x] (+ x 1))
(defn check [x] (* x 2))
(print (do! 1) (check 2) (val 3))
"""
    result = _hy_script(tmp_path, text, deadline_scale_factor)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["2 4 3"]
    assert result.stderr == ""


def test_the_correct_readme_example_prints_no_warning(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    result = _hy_script(tmp_path, CORRECT, deadline_scale_factor)
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["hello"]
    assert result.stderr == ""


def test_a_macro_imported_instead_of_required_says_to_require_it(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    result = _hy_script(tmp_path, IMPORT_INSTEAD, deadline_scale_factor)
    assert result.returncode != 0
    assert "ImportError: cannot import name 'defhandler' from 'doeff_hy.handle'" in result.stderr
    assert "(require doeff-hy.handle [defhandler])" in result.stderr
    assert "main.hy:1:" in result.stderr


def test_a_plain_import_of_a_macro_module_is_not_reported(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    # Importing the module itself, or a name that is not a macro, is correct (functions of a macro module are brought in with import).
    text = '(import doeff-hy.handle)\n(import doeff-hy.macros)\n(print "ok")\n'
    result = _hy_script(tmp_path, text, deadline_scale_factor)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""


def test_the_hint_is_a_warning_that_python_can_turn_into_an_error(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    (tmp_path / "main.hy").write_text(FORGOT_REQUIRE, encoding="utf-8")
    result = _run(
        tmp_path,
        deadline_scale_factor,
        "-W",
        "error::UserWarning",
        "-m",
        "hy",
        "main.hy",
    )
    assert result.returncode != 0
    # Stops with the warning exception before execution starts (the last line of the failure output is the warning text, and there is no module body line).
    last = result.stderr.rstrip().splitlines()[-1]
    assert last.startswith("doeff_hy_bytecode_guard._macro_hints.HyMacroUseWarning:")
    assert 'main.hy", line' not in result.stderr


#: Providers whose table is checked against the real thing (table order - the search for a name's provider goes from the front).
HOMES = {
    "doeff-hy.macros": "doeff_hy.macros",
    "doeff-hy.handle": "doeff_hy.handle",
    "doeff-hy.record": "doeff_hy.record",
}


@pytest.mark.parametrize("hy_name", sorted(HOMES))
def test_the_table_of_macro_names_is_what_require_star_brings(hy_name: str) -> None:
    # If the table disagrees with the real macro table, a forgotten require of a new macro is silently missed, or a nonexistent name is recommended.
    from doeff_hy_bytecode_guard import _macro_hints as macro_hints

    module = importlib.import_module(HOMES[hy_name])
    import hy

    real = {hy.unmangle(name) for name in records.exported_macro_names(vars(module))}
    assert set(macro_hints.MACRO_HOMES[hy_name]) == real


@pytest.mark.parametrize("hy_name", sorted(HOMES))
def test_no_name_in_the_table_is_also_a_runtime_attribute_of_its_provider(hy_name: str) -> None:
    # If an imported name is a function with the same name as a macro, the import is correct (warning would be a mistake).
    from doeff_hy_bytecode_guard import _macro_hints as macro_hints

    module = importlib.import_module(HOMES[hy_name])
    import hy

    runtime = {hy.unmangle(name) for name in vars(module)}
    assert runtime & set(macro_hints.MACRO_HOMES[hy_name]) == set()


def test_the_table_lists_the_providers_in_the_order_the_line_prefers() -> None:
    from doeff_hy_bytecode_guard import _macro_hints as macro_hints

    assert list(macro_hints.MACRO_HOMES) == list(HOMES)


def _record(*absent: str) -> records.MacroRecord:
    """A record holding only the names the expansion looked up in the table and did not find (other fields are not used to tell warnings apart)."""
    return records.MacroRecord(
        hy_version="", macros=(), absent=absent, providers=(), values=(), files=()
    )


def test_a_module_that_uses_a_macro_spelling_only_as_a_local_name_is_not_walked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The form ``(defn f [check] (check 1))`` - even if check is in the record's absent, it is read as LOAD_FAST and is not in co_names. Walking the instructions of a large
    # module takes about 1 second each, so measure that it finishes with only the co_names comparison.
    import hy  # noqa: F401 — the table is built with Hy's mangle
    from doeff_hy_bytecode_guard import _macro_hints as macro_hints

    local = compile("def f(check):\n    return check(1)\n", "main.hy", "exec")
    walked: list[object] = []
    monkeypatch.setattr(macro_hints.dis, "get_instructions", lambda unit: walked.append(unit) or ())
    assert list(macro_hints._hints(local, _record("check"))) == []
    assert walked == []


def test_a_macro_spelling_read_as_a_global_inside_a_function_is_still_reported() -> None:
    # The filter also looks at co_names of nested code - a check read inside a function without being bound is a forgotten require.
    import hy  # noqa: F401 — the table is built with Hy's mangle
    from doeff_hy_bytecode_guard import _macro_hints as macro_hints

    nested = compile("def f(row):\n    return check(row)\n", "main.hy", "exec")
    hints = list(macro_hints._hints(nested, _record("check")))
    assert [hint.line for hint in hints] == [2]
    assert "(require doeff-hy.macros [check])" in hints[0].message
