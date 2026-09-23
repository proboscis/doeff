"""``@effectful`` / ``perform``: effects written as plain Python calls (docs/24-effectful-perform.md).

::

    @effectful
    def place_turns(perform: Effects[ReadShared | WriteShared | ReadClock], limit: int) -> int:
        rows = perform(ReadShared("turn/"))   # rows: dict[str, Row]
        now = perform(ReadClock())            # now: int
        ...
        return placed

    place_turns(3)   # Expand[int, ReadShared | WriteShared | ReadClock] — a program, not run yet

Static meaning (pyright): ``perform(e)`` answers with the effect's ``EffectBase[T]`` T,
or a program's result T, and ``e`` — or, for a program, every effect it may perform —
must be one of the effects in ``Effects[...]``. ``@effectful`` removes the ``perform``
parameter from the signature and returns a program: ``Callable[P, Expand[T, E]]``,
the same type ``@do`` gives.

Runtime meaning: the module is rewritten when it is imported (``install_import_hook``):
``perform(e)`` becomes ``(yield e)`` and the ``perform`` parameter is removed, which is
exactly the generator function ``@do`` receives. ``@effectful`` refuses a function whose
module was not rewritten, naming the fix.
"""

from collections.abc import Callable, Generator
from typing import TYPE_CHECKING, Any, Concatenate, Generic, ParamSpec, Protocol, TypeVar, overload

from doeff.do import program_factory

if TYPE_CHECKING:
    from doeff_vm import Expand

_P = ParamSpec("_P")
_T = TypeVar("_T")
_S = TypeVar("_S")
_E = TypeVar("_E")
_X = TypeVar("_X")
_E_contra = TypeVar("_E_contra", contravariant=True)
_X_co = TypeVar("_X_co", covariant=True)
_T_co = TypeVar("_T_co", covariant=True)


class _Performable(Protocol[_X_co, _T_co]):
    """What ``perform`` accepts: an effect or a program, seen through its static shape.

    ``EffectBase[T]`` performs itself and answers T; a program ``Expand[T, E]``
    performs E and answers T; ``Spawn(p)`` performs itself plus p's effects and
    answers ``Task[T]`` (the ``__iter__`` declarations in doeff_vm/__init__.pyi and
    doeff_core_effects).
    """

    def __iter__(self) -> Generator[_X_co, Any, _T_co]: ...


class Effects(Generic[_E_contra]):
    """The ``perform`` parameter of an ``@effectful`` function; ``E`` = the effects it may perform.

    Only a static type: the parameter is removed by the import-time rewrite and no
    value of this type exists at runtime. ``E`` is contravariant: ``perform(e)``
    binds ``Effects[declared]`` to ``Effects[X]`` where X is what ``e`` performs, which
    holds only when X is within the declared effects. (A class, not a Protocol: a
    Protocol would have its variance inferred from ``__call__`` as covariant.)
    """

    def __call__(self: "Effects[_X]", effect: _Performable[_X, _T], /) -> _T: ...


@overload
def effectful(
    fn: Callable[Concatenate[Effects[_E], _P], _T], /
) -> Callable[_P, "Expand[_T, _E]"]: ...


@overload
def effectful(
    fn: Callable[Concatenate[_S, Effects[_E], _P], _T], /
) -> Callable[Concatenate[_S, _P], "Expand[_T, _E]"]: ...


def effectful(fn: Callable[..., Any], /) -> Callable[..., Any]:
    """Turn a rewritten ``@effectful`` function into a program factory (like ``@do``)."""
    from doeff._effectful_rewrite import MARKER, REWRITE_VERSION

    rewritten = fn.__globals__.get(MARKER)
    if rewritten != REWRITE_VERSION:
        module = fn.__module__
        package = module.partition(".")[0]
        reason = (
            "was not rewritten at import"
            if rewritten is None
            else f"was rewritten by another version of the rewrite ({rewritten})"
        )
        raise TypeError(
            f"@effectful {module}.{fn.__qualname__}: module {module!r} {reason}. "
            f"Call doeff.install_import_hook({package!r}) before importing it "
            "(for a package, in its __init__.py: install_import_hook(__name__))"
        )
    return program_factory(fn, ())


def install_import_hook(*packages: str) -> None:
    """Rewrite ``@effectful`` functions in these packages (and their submodules) at import.

    Modules already imported are not affected. Only modules whose source mentions
    ``effectful`` are rewritten; the others compile as usual.
    """
    from doeff._effectful_rewrite import install_import_hook as install

    install(*packages)
