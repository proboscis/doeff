"""Built-in in-process runner for ``doeff run``.

Builds a Program from the RunContext's source form (``hy_source`` /
``py_source`` / ``program_ref``) and executes it with :func:`doeff.run`.

This is the default when ``--runner`` is not supplied.
"""

from __future__ import annotations

import json
import sys
from typing import Any


def run_local(ctx: Any) -> int:
    """Run the program described by ``ctx`` in the current process.

    Source-form precedence: ``hy_source`` > ``py_source`` > ``program_ref``.
    Returns 0 on success (the value is printed according to ``ctx.format``),
    non-zero on error. The CLI's standard output formatting is preserved so
    scripts piping ``--format json`` get stable output.
    """
    from doeff import run as _run
    from doeff.cli.run_services import import_symbol

    if ctx.hy_source is not None:
        from doeff.cli.hy_runner import evaluate_hy_source
        program = evaluate_hy_source(ctx.hy_source).program
    elif ctx.py_source is not None:
        from doeff.cli.code_runner import execute_doeff_code
        program = execute_doeff_code(ctx.py_source, filename="<doeff-code>")
    elif ctx.program_ref is not None:
        program = import_symbol(ctx.program_ref)
    else:
        print(
            _format_no_source_error(),
            file=sys.stderr,
        )
        return 1

    value = _run(program)
    _render_value(ctx, value)
    return 0


def _render_value(ctx: Any, value: Any) -> None:
    fmt = getattr(ctx, "format", "text") or "text"
    if fmt == "json":
        payload = {
            "status": "ok",
            "program": ctx.program_ref,
            "interpreter": None,
            "envs": [],
            "result": _json_safe(value),
            "result_type": type(value).__name__,
        }
        print(json.dumps(payload))
    else:
        print(value)


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return repr(value)


def _format_no_source_error() -> str:
    return (
        "Error: run_local received a RunContext with no source form.\n"
        "  At least one of PROGRAM / --hy / -c must be provided. Example:\n"
        "\n"
        "    doeff run myapp.entrypoints.p_daily\n"
        "    doeff run --hy '(import doeff [Pure]) (Pure 42)'\n"
        "    doeff run -c 'from doeff import Pure; Pure(42)'\n"
    )
