"""A precompiled Hy .pyc is not recompiled at import time by doeff-hy's staleness check.

The preparation of doeff-cluster's execution environment precompiles the tree's sources with ``compile-python-sources`` (python_bytecode.hy in this package).
When compiling outside an import, Hy creates a temporary module and deletes it right after compiling, so doeff-hy's wrapper around the compile entry could not attach
the macro record. A .pyc without the record is recompiled at import time as possibly stale, so the precompiled work was wasted,
and the startup of jobs at the warehouse redid all the macro expansion (about 43 of about 46 CPU seconds).

The baking process and the reading process are separate Pythons as in production (this venv's interpreter -- the wrapper is loaded at startup via .pth).
Each macro expansion writes one line to the tree's expansions.log (to count whether expansion was redone).
"""

from __future__ import annotations

import marshal
import subprocess
import sys
from pathlib import Path

from doeff_hy_bytecode_guard import _records as records

#: Environment variables not passed to the child process (if the .pyc location is moved outside the tree, the reader does not see the baked .pyc). The child does not write bytecode
#: (PYTHONDONTWRITEBYTECODE=1 -- do not write .pyc of packages inside the checkout. The baking side writes .pyc itself, and whether the reader recompiled is
#: counted by the number of expansions).
_ENV_NOT_PASSED = ("PYTHONPYCACHEPREFIX",)

#: The baking side -- like the execution-environment preparation tool (doeff-cluster's code_prepare.hy), add the tree root to the import path before baking.
_PREPARE = """\
import sys
import hy
from doeff_core_effects._python_bytecode import compile_python_sources, prepare_compile_path

tree = sys.argv[1]
prepare_compile_path(tree, (".",))
items = (("pkg/__init__.py", "pkg"), ("pkg/helpers.hy", "pkg.helpers"),
         ("pkg/macros.hy", "pkg.macros"), ("pkg/user.hy", "pkg.user"))
failures = compile_python_sources(tree, items, 1, (".",))
assert not failures, failures
"""

#: The reading side -- like a child job, import the baked tree.
_IMPORT = """\
import sys
sys.path.insert(0, sys.argv[1])
import hy
import pkg.user
print(pkg.user.value)
"""


def _write_counting_package(root: Path) -> Path:
    """The macro provider (macros), the helper the macro calls at expansion time (helpers), and the user (user). The macro writes one line to the log at each expansion."""
    package = root / "pkg"
    package.mkdir()
    log = root / "expansions.log"
    (package / "__init__.py").write_text("")
    (package / "helpers.hy").write_text("(defn base [] 10)\n")
    (package / "macros.hy").write_text(
        "(import pkg.helpers [base])\n"
        f'(defmacro answer [] (with [f (open "{log}" "a")] (.write f "x\\n")) (+ (base) 1))\n'
    )
    (package / "user.hy").write_text("(require pkg.macros [answer])\n(setv value (answer))\n")
    return log


def _python(script: str, root: Path) -> str:
    # The child inherits this process's environment -- remove only the names in _ENV_NOT_PASSED (`env -u`), and add 2 names.
    unset = [flag for name in _ENV_NOT_PASSED for flag in ("-u", name)]
    completed = subprocess.run(
        [
            "env",
            *unset,
            "PYTHONDONTWRITEBYTECODE=1",
            f"DOEFF_HY_CODE_STORE={root / 'code-store'}",
            sys.executable,
            "-c",
            script,
            str(root),
        ],
        capture_output=True,
        text=True,
        cwd=root,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


def _expansions(log: Path) -> int:
    return len(log.read_text().splitlines()) if log.exists() else 0


def test_a_hy_pyc_compiled_ahead_is_used_at_import_without_expanding_again(tmp_path: Path) -> None:
    """A baked .pyc has the record of the macros the expansion depended on, and import compares the record with the current macros and uses the .pyc as is (1 expansion =
    only at bake time). With a .pyc without the record, import recompiles and the expansion happens twice."""
    log = _write_counting_package(tmp_path)
    _python(_PREPARE, tmp_path)
    assert _expansions(log) == 1
    [pyc] = (tmp_path / "pkg" / "__pycache__").glob("user.*.pyc")
    record = records.record_of(marshal.loads(pyc.read_bytes()[records.PYC_HEADER_BYTES :]))
    assert record is not None
    assert [(used.module, used.name) for used in record.macros] == [("pkg.macros", "answer")]
    assert _python(_IMPORT, tmp_path).split() == ["11"]
    assert _expansions(log) == 1
