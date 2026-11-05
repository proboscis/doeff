"""Importing a name doeff does not have makes the import line an error in type checks (gap 3 found by the initiative doeff-publish investigation).

Measured (pyright 1.1.414 standard and strict, mypy 2.4.0 default and --strict): the module-level ``def __getattr__(name: str) -> object`` in ``doeff/__init__.py``
looked to type tools like a real attribute, so neither ``from doeff import state`` (the handler lives under
doeff_core_effects) nor the misspelling ``from doeff import reeder`` produced an error on the import line.
The name resolved to ``object``, and only the use site reported "not callable".

Hiding ``__getattr__`` from type tools by placing it under ``if not TYPE_CHECKING:`` makes importing a name doeff does not statically have
report ``"state" is unknown import symbol``. The runtime guidance (``ImportError: ... it is in
doeff_core_effects._handlers: ...``) is checked by ``tests/test_import_handler_from_doeff.py`` and does not change.

``DoeffRunContext`` is loaded lazily by the same ``__getattr__``, but on the type side it is visible through the ``if TYPE_CHECKING:`` import,
so hiding does not lose the type -- verified here with ``reveal_type``.
"""

from pathlib import Path

from tests.test_expand_program_static_types import _pyright_in_order

_PROBE = """\
import doeff
from doeff import DoeffRunContext, Get, do, run

reveal_type(DoeffRunContext)
_ = (Get, do, run, doeff.run, doeff.DoeffRunContext)
# ---- mistakes (each line below must be an error) ----
from doeff import state
from doeff import no_such_name
from doeff import reeder
_ = doeff.scheduled
"""

_MISTAKE_MARKER = "# ---- mistakes"


def test_pyright_flags_a_name_doeff_does_not_have(tmp_path: Path) -> None:
    report = _pyright_in_order(tmp_path, (("probe.py", _PROBE),))
    assert report["summary"]["filesAnalyzed"] == 1, report["summary"]
    diagnostics = report["generalDiagnostics"]
    errors = [item for item in diagnostics if item["severity"] == "error"]
    lines = _PROBE.splitlines()
    first_mistake = next(i for i, line in enumerate(lines) if line.startswith(_MISTAKE_MARKER))
    clean_part = [e for e in errors if e["range"]["start"]["line"] < first_mistake]
    assert clean_part == [], clean_part
    flagged = {lines[e["range"]["start"]["line"]].strip() for e in errors}
    assert flagged == {
        "from doeff import state",
        "from doeff import no_such_name",
        "from doeff import reeder",
        "_ = doeff.scheduled",
    }, errors
    for error in errors:
        name = lines[error["range"]["start"]["line"]].split()[-1].removeprefix("doeff.")
        assert f'"{name}"' in error["message"], error["message"]


def test_doeff_run_context_keeps_its_type_when_the_lazy_getattr_is_hidden(tmp_path: Path) -> None:
    report = _pyright_in_order(tmp_path, (("probe.py", _PROBE),))
    revealed = [
        item["message"]
        for item in report["generalDiagnostics"]
        if item["severity"] == "information"
    ]
    assert revealed == ['Type of "DoeffRunContext" is "type[DoeffRunContext]"'], revealed
