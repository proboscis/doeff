"""Types for hy.macros (declarations for type checking -- at run time hy is loaded; a stub for the part doeff-hy distributes).

Hy's `(defmacro NAME …)` expands to `hy.macros.macro('NAME')(_hy_anon_N)`. hy.macros has no type declarations, so pyright strict
reported "macro type unknown" (reportUnknownMemberType) and "not an attribute of hy" (reportAttributeAccessIssue) on that one statement --
two errors per macro in every .hy file that wrote defmacro, which the author could not fix (6 in controllers/shared/intent/patrols.hy of agora-controllers).
This declares only the names the Hy expansion emits (macro, require) -- these are the only names that code in doeff and agora-controllers
reads from hy.macros (git grep of 2026-10-10). When adding code that reads other names, add their declarations here.
"""

from collections.abc import Callable, Sequence
from types import ModuleType
from typing import TypeVar

_F = TypeVar("_F", bound=Callable[..., object])

#: The decorator that the expansion of `(defmacro NAME …)` calls -- takes the name, registers the macro function, and returns the same function (renamed).
def macro(name: str) -> Callable[[_F], _F]: ...

#: The macro import that the expansion of `(require …)` calls (doeff_hy._sexpr and the check point at the module and call it directly). target = module name, module, name table
#: (dict), or None (the calling module). assignments = "ALL", "EXPORTS", or a sequence of (macro name, alias) pairs. Returns a sequence of
#: (new name, original name, macro function) for the macros actually moved.
def require(
    source_module: str | ModuleType,
    target: str | ModuleType | dict[str, object] | None,
    assignments: str | Sequence[Sequence[str]],
    prefix: str = "",
    target_module_name: str | None = None,
    compiler: object | None = None,
) -> list[tuple[str, str, Callable[..., object]]]: ...
