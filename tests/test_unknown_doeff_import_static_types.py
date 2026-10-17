"""doeff に無い名の import は、型の check で import の行が error になる(card ki-02e317783af0 の抜け 3)。

実測(pyright 1.1.414 の standard と strict・mypy 2.4.0 の既定と --strict): ``doeff/__init__.py`` の
module の ``def __getattr__(name: str) -> object`` が型の道具に見え、``from doeff import state``(handler は
doeff_core_effects の下にある)も綴りの誤り ``from doeff import reeder`` も、import の行で error にならなかった。
名は ``object`` に解け、使う行で「呼べない」とだけ出た。

``__getattr__`` を ``if not TYPE_CHECKING:`` の下へ置いて型の道具から隠すと、doeff が静的に持たない名の import は
``"state" is unknown import symbol`` になる。実行時の案内(``ImportError: ... it is in
doeff_core_effects.handlers: ...``)は ``tests/test_import_handler_from_doeff.py`` が見ていて、変わらない。

``DoeffRunContext`` は同じ ``__getattr__`` で遅く読むが、型の側は ``if TYPE_CHECKING:`` の import で見えるので、
隠しても型は失わない — ここで ``reveal_type`` で確かめる。
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
