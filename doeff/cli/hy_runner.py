"""Evaluate inline Hy source for ``doeff run --hy``.

The user supplies a block of Hy forms. Imports, requires, and defs execute at
module scope; the value of the final expression is treated as the Program.
Users compose handlers themselves inline — ``handle`` for one-shot
installs, ``defhandler`` for reusable ones. The common macro prelude
is prepended automatically so ``do!`` / ``<-`` / ``handle`` /
``defhandler`` are available without extra ``require`` lines.
"""

import sys
import types
import uuid
from dataclasses import dataclass
from typing import Any

_REQUIRE_PRELUDE = (
    "(require doeff-hy.macros [do! <-])\n"
    "(require doeff-hy.handle [defhandler handle])"
)


@dataclass
class HyEvalResult:
    """Result of evaluating a Hy source block."""

    program: Any
    module: types.ModuleType


class HyRunnerError(RuntimeError):
    """Raised when --hy source cannot be parsed or evaluated."""


def _require_hy():
    try:
        import hy  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional dep
        raise HyRunnerError(
            "--hy requires the 'hy' package. Install it with 'pip install hy'."
        ) from exc
    return hy


def evaluate_hy_source(source: str, filename: str = "<doeff-hy>") -> HyEvalResult:
    """Compile and evaluate a Hy source block.

    Returns the Program produced by the last evaluated expression along with
    the module the code was executed in (useful for post-mortem inspection).
    """
    stripped = source.strip()
    if not stripped:
        raise HyRunnerError("No Hy source provided")

    hy = _require_hy()
    full_source = f"{_REQUIRE_PRELUDE}\n{source}"

    module_name = f"__doeff_hy_cli_{uuid.uuid4().hex}__"
    module = types.ModuleType(module_name)
    module.__file__ = filename
    sys.modules[module_name] = module

    try:
        forms = list(hy.read_many(full_source, filename=filename))
    except Exception as exc:
        raise HyRunnerError(f"Failed to parse --hy source: {exc}") from exc

    program: Any = None
    try:
        for form in forms:
            result = hy.eval(form, module.__dict__, module=module)
            if result is not None:
                program = result
    except Exception as exc:
        raise HyRunnerError(f"Failed to evaluate --hy source: {exc}") from exc

    if program is None:
        raise HyRunnerError(
            "--hy source did not produce a Program value "
            "(final expression evaluated to None)"
        )

    return HyEvalResult(program=program, module=module)
