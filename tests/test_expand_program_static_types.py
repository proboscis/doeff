"""The Expand returned by an @do function is accepted as a Program regardless of the order in which pyright checks files.

In agora-controllers' daily type check (pyright 1.1.414, all files in 1 process), the place that passes a defk function to an argument of
`Callable[..., Program]` failed with reportArgumentType and went red, and which files went red changed on every run.
The same shape is reproduced with two files (_TRIGGER and _PROBE below):

- In the file checked first, an @do call is passed to an argument of `Program[T, ...]` under a union expected type
  (`answer: str | None = run(make(1))`. The expansion of doeff-hy's `(<- x (| str None) ...)` has the same shape).
- In the file checked later, an @do function is passed to `tuple[Callable[..., Program], ...]`.

pyright (measured on 1.1.411 and 1.1.414), when assignment to a protocol fails, also tries the form with type arguments removed
(`Expand[_T_co, _E_co]` -> `Program[_ProgramResult, _ProgramEffects]`), and if that also fails,
records in the Expand class cache that "Expand does not fit Program with any type arguments". Assignments in later files hit this cache and
fail. Checking only the later file gives 0 findings, and the reverse order also gives 0. If Expand has Program as an explicit base, pyright
decides by inheritance rather than by protocol matching, so it does not go through this cache.

pyright checks files in the order listed in the arguments, so here the trigger file is listed first and checked in 1 process.
"""

import json
import subprocess
import sys
from pathlib import Path

from tests.test_static_typing import _pyright_python

_TRIGGER = """\
from doeff import do, run


@do
def make(x: int) -> str | None:
    return None


def judge() -> str | None:
    answer: str | None = run(make(1))
    return answer
"""

_PROBE = """\
from collections.abc import Callable

from doeff import Expand, Program, do


@do
def assemble(parts: int) -> int:
    return parts


def declared(parts: int) -> Expand[int, None]: ...


def count(parts: int) -> int:
    return parts


def receiver(host_side: tuple[Callable[..., Program], ...]) -> None: ...


receiver((assemble,))
receiver((declared,))
# ---- mistakes (each line below must be an error) ----
receiver((count,))
"""

_MISTAKE_MARKER = "# ---- mistakes"


def _pyright_in_order(tmp_path: Path, files: tuple[tuple[str, str], ...]) -> dict:
    """Check files in the listed order in one pyright process and return the --outputjson result."""
    root = Path(__file__).resolve().parents[1]
    original = json.loads((root / "pyrightconfig.json").read_text())
    config = tmp_path / "pyrightconfig.json"
    config.write_text(json.dumps({
        "extends": str(root / "pyrightconfig.json"),
        "extraPaths": [str(root), *(str(root / path) for path in original["extraPaths"])],
    }))
    for name, source in files:
        (tmp_path / name).write_text(source)
    result = subprocess.run(
        [sys.executable, "-m", "pyright", "--project", str(config),
         "--pythonpath", str(_pyright_python(tmp_path)), "--outputjson", *(str(tmp_path / name) for name, _ in files)],
        cwd=root, capture_output=True, text=True, timeout=120, check=False,
    )
    return json.loads(result.stdout)


def test_do_function_is_a_program_even_after_a_union_expected_bind(tmp_path: Path) -> None:
    report = _pyright_in_order(tmp_path, (("a_trigger.py", _TRIGGER), ("b_probe.py", _PROBE)))
    assert report["summary"]["filesAnalyzed"] == 2, report["summary"]
    errors = [item for item in report["generalDiagnostics"] if item["severity"] == "error"]
    trigger_errors = [e for e in errors if Path(e["file"]).name == "a_trigger.py"]
    assert trigger_errors == [], trigger_errors
    probe_lines = _PROBE.splitlines()
    first_mistake = next(i for i, line in enumerate(probe_lines) if line.startswith(_MISTAKE_MARKER))
    probe_errors = [e for e in errors if Path(e["file"]).name == "b_probe.py"]
    clean_part = [e for e in probe_errors if e["range"]["start"]["line"] < first_mistake]
    assert clean_part == [], clean_part
    flagged = {probe_lines[e["range"]["start"]["line"]].strip() for e in probe_errors}
    assert flagged == {"receiver((count,))"}, probe_errors
