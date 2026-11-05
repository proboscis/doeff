"""Marker for whether the current compile is the expansion used for type checking. This module is its only owner (doeff_hy._static_view sets it, and the wrapper's compile hook reads it).

The expansion used for type checking (doeff-hy-check, :func:`doeff_hy._static_view.static_view`) emits code that
cannot run: it omits the :pre isinstance checks and the runtime imports. If, during that expansion, a macro-dependency
record were attached to the module providing a macro that was first loaded by require, that code would be kept in the
.pyc and in the shared code store (``DOEFF_HY_CODE_STORE``) under the same key as the normal expansion, and a later
normal import would read it and fail with ``NameError: name '_doeff_do' is not defined`` (reproduced in milestone I, I-3, 2026-10-03).
The wrapper's compile hook attaches no record to a compile made while this marker is set. Code without a record does
not enter the store, and even if written to a .pyc, the next normal import compiles it again.

It is imported from the wrapper that is loaded at startup, so it imports only the standard library.
"""

from contextvars import ContextVar

#: Whether we are in the middle of the type-checking expansion (default = normal expansion). It is a per-context value, so it does not affect imports in other threads of the same process.
TYPE_CHECK_EXPANSION: ContextVar[bool] = ContextVar("doeff_hy_static_view", default=False)
