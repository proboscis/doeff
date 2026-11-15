"""The macro absent-as passes the type gate under strict (ADR-DOE-CORE-EFFECTS-003 R8).

absent-as wraps its body with ``direct_bind`` and hands the resulting DirectBind to the handler ``absent_as(default)`` returns; the handler's
parameter is ``Bindable``. If DirectBind leaves the ``Bindable`` union, every absent-as in users' code turns red with reportArgumentType,
although the run opens the DirectBind as before.
"""

import shutil
from pathlib import Path

import doeff_hy  # noqa: F401  # Enable the Hy import hook
import pytest

from doeff_hy._static_stub import strict_errors

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")


ABSENT_AS_FLOW = """\
(require doeff-hy.macros [defk <- absent-as on-raise])
(import doeff [Pure])

(defk one-or-zero []
  {:pre [] :post [(: % int)]}
  "absent-as around a program, alone and inside on-raise (the shape users write)."
  (<- n int (absent-as 0 (Pure 1)))
  (<- m int (on-raise (absent-as 0 (Pure 2)) [Exception 0]))
  (+ n m))
"""


@needs_pyright
def test_absent_as_expansion_is_accepted_by_the_boundary_handler(tmp_path: Path) -> None:
    errors = strict_errors(tmp_path, ABSENT_AS_FLOW)
    assert [e for e in errors if "reportArgumentType" in e] == []


@needs_pyright
def test_absent_as_still_refuses_a_body_that_cannot_be_bound(tmp_path: Path) -> None:
    # Failure case: if Bindable grows into object or Any to let DirectBind in, a body that is not a program stops turning red.
    bad = (
        "(require doeff-hy.macros [defk <- absent-as])\n"
        "(defk bad []\n  {:pre [] :post [(: % int)]}\n  \"x\"\n  (<- n int (absent-as 0 3))\n  n)\n"
    )
    assert [e for e in strict_errors(tmp_path, bad) if "reportArgumentType" in e]
