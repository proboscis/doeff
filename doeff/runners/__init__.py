"""Runner backends for ``doeff run``.

A *runner* receives a :class:`doeff.cli.run_services.RunContext` describing the
invocation and decides how to execute the program. The CLI always builds a
RunContext and dispatches to a runner; with no ``--runner`` the built-in
:func:`doeff.runners.local.run_local` is used, which behaves exactly like the
legacy path — compose the Program from whatever source form the user gave
(``--hy``, ``-c``, or ``--program``) and call :func:`doeff.run`.

Custom runners can ship in any Python package and are selected via
``--runner pkg.module.callable``. Typical uses:

* ``doeff.runners.local`` — default, in-process
* ``myapp.runners.k3s``  — serialize to a Kubernetes Job via ``ctx.raw_argv``
* ``myapp.runners.tmux`` — spawn in a tmux pane for interactive debugging
"""

from doeff.runners.local import run_local

__all__ = ["run_local"]
