"""
run(doexpr) — execute a DoExpr program to completion.
"""

from doeff_vm import PyVM


def run(doexpr):
    """Run a DoExpr program to completion and return the result."""
    vm = PyVM()
    return vm.run(doexpr)
