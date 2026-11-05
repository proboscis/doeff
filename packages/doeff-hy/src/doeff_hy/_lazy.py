"""Runtime container for lazy val / lazy var (ADR-DOE-HY-006).

When `(lazy val x expr)` is written in the body of a defk, deftest, or defhandler clause, the macro creates one
LazyCell local to that call and rewrites references to `x` into an expression that means "the remembered value if
it is ready; otherwise run force and remember the result" (doeff_hy/_binding_forms.py).

- The container is a local value created per call (same lifetime as the body's local variables). It holds no
  state across sessions; that is the job of defhandler's session val / session var (which go through the
  state effects Get / Put).
- If the first evaluation ends with an exception, nothing is remembered (the next use evaluates again, like
  Scala's lazy val).
- `(:= x v)` on a lazy var calls assign. If it is reassigned before first use, the initial-value expression is never evaluated.

The container mutates its contents, so it is not frozen (remembering and rewriting are the job of this type).
Mutable fields have names that start with `_mut_`, and reads go through a property.
"""

from collections.abc import Callable, Generator, Mapping

from doeff.do import do
from doeff.program import Expand

#: A zero-argument generator function wrapping the initial-value expression (the macro passes a generator even for an expression that uses no effects).
LazyInit = Callable[[], Generator[object, object, object]]


class LazyCell:
    """A container holding one lazy val / lazy var within a single call.

    init is a generator function wrapping the initial-value expression. force wraps it with doeff's do and runs it.
    The value's type is decided by the expression at the reference site (the type-checking expansion evaluates
    at the place where lazy was written, so pyright sees the type of the original expression)."""

    __slots__ = ("_init", "_mut_ready", "_mut_value")

    def __init__(self, init: LazyInit) -> None:
        self._init = init
        self._mut_ready = False
        self._mut_value: object = None

    @property
    def ready(self) -> bool:
        """Whether a remembered value exists (the reference expression checks whether it may return the value without running force)."""
        return self._mut_ready

    @property
    def value(self) -> object:
        """The remembered value (meaningful only when ready)."""
        return self._mut_value

    def force(self) -> Expand:
        """A Program that runs the initial-value expression and remembers the result if not yet evaluated (returns the remembered value if already evaluated)."""
        return _force(self)

    def assign(self, value: object) -> None:
        """Reassign a lazy var. Later references see this value, and the initial-value expression is no longer evaluated."""
        self._mut_value = value
        self._mut_ready = True


@do
def _force(cell: LazyCell) -> Generator[object, object, object]:
    """Body of LazyCell.force: run the initial-value expression only the first time, and remember the result only on success."""
    if cell.ready:
        return cell.value
    value = yield do(cell._init)()
    if not cell.ready:
        cell.assign(value)
    return cell.value


# ---------------------------------------------------------------------------
# Module-level lazy val (expressions that use no effects only)
# ---------------------------------------------------------------------------
#
# When `(lazy val NAME expr)` is written at module level, the expression is not evaluated at import time; it is
# evaluated once on first use and remembered. An expression that uses effects is an error at expansion time (a
# module has no handler, and a value built under the handler of whichever call used it first would be reused
# in later, unrelated sessions).
#
# The expansion (binding_forms.module_declaration) emits these two setv forms at module level:
#   (setv __doeff_module_lazies__ (with-module-lazy (globals) "NAME" (fn [] expr)))
#   (when (not-in "__getattr__" (globals)) (setv __getattr__ (module-getattr (globals))))
# Where it can be used: bodies of defk, deftest, and defhandler in the same module (the macro rewrites the
# references), and attribute access or from-import from other modules (PEP 562 module __getattr__).
# Bare references in defn, deff, or module-level expressions of the same module are not rewritten (NameError).

MODULE_LAZIES = "__doeff_module_lazies__"


class ModuleLazy:
    """One module-level lazy val (evaluated once on first use and remembered)."""

    __slots__ = ("_init", "_mut_ready", "_mut_value", "name")

    def __init__(self, name: str, init: Callable[[], object]) -> None:
        self.name = name
        self._init = init
        self._mut_ready = False
        self._mut_value: object = None

    def get(self) -> object:
        """Return the remembered value. If not yet remembered, evaluate the expression and remember it (on an exception nothing is remembered, and the next call evaluates again)."""
        if not self._mut_ready:
            self._mut_value = self._init()
            self._mut_ready = True
        return self._mut_value


def _lazy_table(namespace: Mapping[str, object]) -> Mapping[str, ModuleLazy]:
    """The table of lazy vals in the module namespace (empty if there is none yet). A wrong shape is an error."""
    table = namespace.get(MODULE_LAZIES, {})
    if not isinstance(table, Mapping):
        raise TypeError(f"{MODULE_LAZIES} is not a Mapping: {type(table).__name__}")
    for name, entry in table.items():
        if not isinstance(name, str) or not isinstance(entry, ModuleLazy):
            raise TypeError(f"{MODULE_LAZIES} has an entry that is not (str, ModuleLazy): {name!r}")
    return table


def with_module_lazy(namespace: Mapping[str, object], name: str, init: Callable[[], object]) -> dict[str, ModuleLazy]:
    """Return a new table with one more entry added to the module's lazy val table (the module-level setv binds this to the table name)."""
    return {**_lazy_table(namespace), name: ModuleLazy(name, init)}


def module_lazy_value(namespace: Mapping[str, object], name: str) -> object:
    """Get the value of a module's lazy val (evaluating it the first time). Used by the entry point for references from other modules."""
    table = _lazy_table(namespace)
    if name not in table:
        raise NameError(f"The module's lazy val `{name}` has not been declared yet (it is used before its declaration)")
    return table[name].get()


def module_getattr(namespace: Mapping[str, object]) -> Callable[[str], object]:
    """PEP 562 module __getattr__: when another module looks up a lazy val by name, evaluate and return it."""

    def getattr_(attribute: str) -> object:
        """Return the value if the name is a lazy val; otherwise raise the usual missing-attribute error."""
        if attribute in _lazy_table(namespace):
            return module_lazy_value(namespace, attribute)
        raise AttributeError(f"module {namespace.get('__name__')!r} has no attribute {attribute!r}")

    return getattr_
