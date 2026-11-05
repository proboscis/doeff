"""Owner of the memo kept only during one outermost load (running a module body, including the imports inside it).

The check of the .pyc record (:mod:`doeff_hy_bytecode_guard._macro_use`) verifies whether a remembered macro digest is still usable, using the state
(mtime and size) of the files the digest depended on. The check runs once per Hy module, so if the verification were done per check, the same file would be
stat-ed hundreds of times within one load (loading test_emulated_screen.hy: 797 checks and 9,700 stats — agora-redesign).
The loader-hook wrapper (:mod:`doeff_hy_bytecode_guard._loader_hooks`) keeps this memo during the execution of the outermost module body,
and checks read file state only once inside it — one load is treated as one snapshot of file state. The next load reads again.
Outside a load (when a tool checks directly), each check sets its own.

The memo is used only within the thread that set it, and only until the context that set it closes (:func:`current_scope`). Threads started inside a load (in free-threaded
3.14 they inherit the starter's context — ``sys.flags.thread_inherit_context``) and copied contexts keep the ContextVar value even after the load,
so the mere presence of a value does not identify the current load's memo.

It is imported from the wrapper loaded at startup, so it imports only modules the standard library has already loaded at startup.
"""

from _thread import get_ident  # threading is not loaded at startup — the same function's origin
from contextvars import ContextVar, Token


class ImportScope:
    """The memo for one outermost load — file path -> state (set by the owning check; a vanished file is None), and the digest
    memo verified inside it (key -> memo — the same memo is never verified twice). Holds the thread that set it and whether the setting context has closed."""

    __slots__ = ("_mut_closed", "confirmed", "owner", "states")

    def __init__(self) -> None:
        self.states: dict[str, object] = {}
        self.confirmed: dict[object, object] = {}
        self.owner = get_ident()
        self._mut_closed = False

    @property
    def closed(self) -> bool:
        """Whether the setting context has closed."""
        return self._mut_closed

    def close(self) -> None:
        """The setting context closed — even if the value remains in an inherited thread or a copied context, it is no longer used."""
        self._mut_closed = True


#: The memo of the current load (None outside a load). Only the context that sets the memo sets and resets the value, and reads go through :func:`current_scope` —
#: threads started inside a load and copied contexts inherit the value.
IMPORT_SCOPE: ContextVar[ImportScope | None] = ContextVar("doeff_hy_import_scope", default=None)


def current_scope() -> ImportScope | None:
    """The memo of the current load — only within the thread that set it, and before the setting context closes (otherwise None — the check sets its own memo)."""
    scope = IMPORT_SCOPE.get()
    if scope is None or scope.closed or scope.owner != get_ident():
        return None
    return scope


class SharedScope:
    """A context that sets one memo across multiple checks (used with ``with``) — even when a tool checks directly (cache lookup of the type-check expansion),
    the memo is not discarded per check, and file state is stat-ed only once within one run of the tool. Files are treated as unchanged during the run.
    If a memo already exists inside a load (nested), it is used as is."""

    __slots__ = ("_mut_scope", "_mut_token")

    def __init__(self) -> None:
        self._mut_scope: ImportScope | None = None
        self._mut_token: Token[ImportScope | None] | None = None

    def __enter__(self) -> ImportScope:
        existing = current_scope()
        if existing is not None:
            return existing
        scope = self._mut_scope = ImportScope()
        self._mut_token = IMPORT_SCOPE.set(scope)
        return scope

    def __exit__(self, *exc_info: object) -> None:
        if self._mut_scope is None or self._mut_token is None:
            return
        self._mut_scope.close()
        IMPORT_SCOPE.reset(self._mut_token)
