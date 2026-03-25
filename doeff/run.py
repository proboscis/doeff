"""
run(doexpr) — execute a DoExpr program to completion.
"""

import sys

from doeff_vm import PyVM


def run(doexpr):
    """Run a DoExpr program to completion and return the result.

    On error, enriches the exception with __doeff_traceback__ (from Python's
    __traceback__) and prints the doeff traceback to stderr.
    """
    vm = PyVM()
    try:
        return vm.run(doexpr)
    except Exception as e:
        _enrich_and_print(e)
        raise


def _enrich_and_print(exception):
    """Enrich exception with doeff traceback and print to stderr."""
    try:
        # If scheduler already enriched, just print
        if not hasattr(exception, '__doeff_traceback__'):
            _enrich_exception_traceback(exception)
        from doeff.traceback import format_default
        tb = format_default(exception)
        if tb is not None:
            print(tb, file=sys.stderr)
    except Exception:
        pass  # don't mask the original error


def _enrich_exception_traceback(exc):
    """Extract doeff-relevant frames from Python's __traceback__."""
    import traceback as tb_mod
    tb = exc.__traceback__
    if tb is None:
        return
    frames = []
    for fs in tb_mod.extract_tb(tb):
        fn = fs.filename
        name = fs.name
        # Skip doeff VM/framework internals
        if any(p in fn for p in ('/doeff_vm/', '/doeff/do.py', '/doeff/run.py',
                                  '/doeff_core_effects/')):
            continue
        frames.append([fs.name, fs.filename, fs.lineno])
    if frames:
        exc.__doeff_traceback__ = frames
