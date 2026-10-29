"""
doeff - Algebraic Effects for Python.

Backed by a Rust VM with OCaml 5-aligned effect handler architecture.
"""

# ruff: noqa: I001 - import order avoids doeff_core_effects circular imports.
import opcode as _opcode
import sys as _sys
import types as _types
from collections.abc import Callable as _CallableT
from collections.abc import Generator
from typing import TYPE_CHECKING, Any, NoReturn, TypeAlias, TypeVar

from doeff_vm import Callable as Callable
from doeff_vm import Callable as _VmCallable
from doeff_vm import EffectBase as EffectBase
from doeff_vm import K as K
from doeff_vm import PyVM as PyVM
from doeff_vm import UnhandledEffect as UnhandledEffect

# The VM's types for writing handlers and program nodes. doeff is the one package that imports
# doeff_vm; a subpackage or a user imports these from doeff (agora-redesign ki-ff728b00369a).
# They are bound before doeff_core_effects is imported below, which imports them from here.
from doeff_vm import BindOpener as BindOpener
from doeff_vm import Call as Call
from doeff_vm import DoFunction as DoFunction
from doeff_vm import HandlerSpec as HandlerSpec
from doeff_vm import IRStream as IRStream
from doeff_vm import TailEval as TailEval
from doeff_vm import WithBoundaries as WithBoundaries

from doeff.do import do as do
from doeff.effectful import Effects as Effects
from doeff.effectful import effectful as effectful
from doeff.effectful import install_import_hook as install_import_hook
from doeff.mcp import McpParamSchema as McpParamSchema
from doeff.mcp import McpToolDef as McpToolDef
from doeff.program import Apply as Apply
from doeff.program import Expand as Expand
from doeff.program import GetBoundaries as GetBoundaries
from doeff.program import GetExecutionContext as GetExecutionContext
from doeff.program import GetHandlers as GetHandlers
from doeff.program import GetOuterHandlers as GetOuterHandlers
from doeff.program import GetTraceback as GetTraceback
from doeff.program import Pass as Pass
from doeff.program import Perform as Perform
from doeff.program import Pure as Pure
from doeff.program import Resume as Resume
from doeff.program import ResumeThrow as ResumeThrow
from doeff.program import Transfer as Transfer
from doeff.program import TransferThrow as TransferThrow
from doeff.program import (
    WithHandlerType as WithHandlerType,
)
from doeff.program import WithObserve as WithObserveRaw
from doeff.program import handler as handler
from doeff.program import program as program
from doeff.program import stacked_handlers as stacked_handlers
from doeff.program import typed_resume as typed_resume
from doeff.program import typed_transfer as typed_transfer
from doeff.program import with_handlers as with_handlers
from doeff_vm._effect_types import ProgramSignature as ProgramSignature
from doeff_vm._effect_types import effect_result_type as effect_result_type
from doeff_vm._effect_types import handler_effect_types as handler_effect_types
from doeff_vm._effect_types import program_signature as program_signature
from doeff.result import Err as Err
from doeff.result import Maybe as Maybe
from doeff.result import Nothing as Nothing
from doeff.result import Ok as Ok
from doeff.result import Some as Some
from doeff.run import run as run

from doeff_core_effects._effects import Ask as Ask
from doeff_core_effects._effects import Await as Await
from doeff_core_effects._effects import Get as Get
from doeff_core_effects._effects import Listen as Listen
from doeff_core_effects._effects import Local as Local
from doeff_core_effects._effects import Put as Put
from doeff_core_effects._effects import Slog as Slog
from doeff_core_effects._effects import SlogEffect as SlogEffect
from doeff_core_effects._effects import Tell as Tell
from doeff_core_effects._effects import Try as Try
from doeff_core_effects._effects import WriterTellEffect as WriterTellEffect
from doeff_core_effects._effects import MissingEnvKeyError as MissingEnvKeyError
from doeff_core_effects._effects import slog as slog
from doeff_core_effects._scheduler import PRIORITY_HIGH as PRIORITY_HIGH
from doeff_core_effects._scheduler import PRIORITY_IDLE as PRIORITY_IDLE
from doeff_core_effects._scheduler import PRIORITY_NORMAL as PRIORITY_NORMAL
from doeff_core_effects._scheduler import AcquireSemaphore as AcquireSemaphore
from doeff_core_effects._scheduler import Cancel as Cancel
from doeff_core_effects._scheduler import CompletePromise as CompletePromise
from doeff_core_effects._scheduler import CreateExternalPromise as CreateExternalPromise
from doeff_core_effects._scheduler import CreatePromise as CreatePromise
from doeff_core_effects._scheduler import CreateSemaphore as CreateSemaphore
from doeff_core_effects._scheduler import FailPromise as FailPromise
from doeff_core_effects._scheduler import Future as Future
from doeff_core_effects._scheduler import Gather as Gather
from doeff_core_effects._scheduler import Promise as Promise
from doeff_core_effects._scheduler import Race as Race
from doeff_core_effects._scheduler import ReleaseSemaphore as ReleaseSemaphore
from doeff_core_effects._scheduler import SchedulerDeadlockError as SchedulerDeadlockError
from doeff_core_effects._scheduler import Semaphore as Semaphore
from doeff_core_effects._scheduler import Spawn as Spawn
from doeff_core_effects._scheduler import Task as Task
from doeff_core_effects._scheduler import TaskCancelledError as TaskCancelledError
from doeff_core_effects._scheduler import Wait as Wait
from doeff_core_effects.vm_probe_effects import ReadInvariantChecks as ReadInvariantChecks
from doeff_core_effects.vm_probe_effects import ReadVmLiveCounts as ReadVmLiveCounts
from doeff_core_effects.vm_probe_effects import ReadVmScanCounts as ReadVmScanCounts
from doeff_core_effects.vm_probe_effects import ReadVmWorkCounts as ReadVmWorkCounts
from doeff_core_effects.vm_probe_effects import SetInvariantChecks as SetInvariantChecks
from doeff_core_effects.vm_probe_effects import SetSchedulerStepBudgetArmed as SetSchedulerStepBudgetArmed
from doeff_core_effects.vm_probe_effects import SetSchedulerTraceInstalled as SetSchedulerTraceInstalled

_BodyResult = TypeVar("_BodyResult")


def WithObserve(  # noqa: N802 - public compatibility constructor
    observer: "_CallableT[..., object] | _VmCallable", body: "Program[_BodyResult]"
) -> "WithObserveRaw[_BodyResult]":
    """Install observer and run body under it.

    Accepts a plain Python callable as observer — automatically wraps it
    with doeff_vm.Callable so the Rust VM can invoke it.

    Use WithObserveRaw if you need the underlying pyclass directly.
    """
    if isinstance(observer, _VmCallable):
        return WithObserveRaw(observer, body)
    if not callable(observer):
        raise TypeError(
            f"WithObserve: observer must be callable, got {type(observer).__name__}"
        )
    return WithObserveRaw(_VmCallable(observer), body)

Effect = EffectBase

# DoExpr — virtual base type for all program nodes.
# Enables isinstance(x, DoExpr) to check if a value is any program node.
_DOEXPR_TYPES = (
    Pure, Perform, Resume, Transfer, Apply, Expand, Pass,
    WithHandlerType, WithObserveRaw, ResumeThrow, TransferThrow,
    GetTraceback, GetExecutionContext, GetHandlers, GetBoundaries, GetOuterHandlers,
)


class _DoExprMeta(type):
    def __instancecheck__(cls, instance):
        return isinstance(instance, _DOEXPR_TYPES)

    def __subclasscheck__(cls, subclass):
        return issubclass(subclass, _DOEXPR_TYPES)


if TYPE_CHECKING:
    from doeff.cli.run_services import DoeffRunContext as DoeffRunContext

if TYPE_CHECKING:
    # 静的の読み手(pyright)にとって DoExpr は「program の node のどれか」の union。
    # 実行時の DoExpr は下の virtual base class で、isinstance/issubclass の意味は
    # 変わらない — 変わるのは型検査器が `-> Program` を verify できるかだけ。
    # 段 7 lane 7c(agora-redesign 決定 1.3): driver 層が program を返す関数を
    # 持つようになり、`Expand` を `Program` へ返せない静的な穴が実害になった。
    # 総称の node の型引数は Any で明示する — 素のまま書くと pyright は Unknown(`Pure[Unknown]`)に読み、DoExpr を返す・受ける
    # 関数(on_raise・maybe など)を呼ぶ側の strict の型検査が「部分的に不明」の赤になる。
    DoExpr = (
        Pure[Any] | Perform[Any] | Resume | Transfer | Apply | Expand[Any, Any] | Pass
        | WithHandlerType[Any] | WithObserveRaw[Any] | ResumeThrow | TransferThrow
        | GetTraceback | GetExecutionContext | GetHandlers | GetBoundaries
        | GetOuterHandlers
    )

    # The protocol is defined in the doeff_vm stub so that Expand can name it as its base
    # (doeff_vm/__init__.pyi, agora-redesign #4346).
    from doeff_vm import Program as Program

    ProgramBase = DoExpr
else:

    class DoExpr(metaclass=_DoExprMeta):
        """Virtual base type for all doeff program nodes.

        isinstance(x, DoExpr) returns True for any program node
        (Pure, Expand, WithHandlerType, etc.).
        """

        def __class_getitem__(cls, item):
            # ``Program[int]`` / ``Program[int, ReadClock]`` in an evaluated
            # annotation; the static meaning is the Protocol in doeff_vm/__init__.pyi.
            return _types.GenericAlias(cls, item)

    Program = DoExpr
    ProgramBase = DoExpr
AskEffect = Ask


@do
def merge_dicts(*sources: object) -> Generator[Any, Any, dict[Any, Any]]:
    """Monadically merge multiple Program[dict] or plain dicts left-to-right.

    Usage: merge_dicts(Pure({"a": 1}), Pure({"b": 2})) → Program[{"a": 1, "b": 2}]

    The sources are typed ``object``: the CLI passes the env symbols it loaded by path,
    and a source that is neither a dict nor a program yielding one raises ``TypeError``.
    """
    merged: dict[Any, Any] = {}
    for source in sources:
        if isinstance(source, DoExpr):
            d = yield source
            if not isinstance(d, dict):
                raise TypeError(
                    f"merge_dicts: expected Program[dict] to yield dict, got {type(d).__name__}"
                )
        elif isinstance(source, dict):
            d = source
        else:
            raise TypeError(f"merge_dicts: expected Program[dict] or dict, got {type(source).__name__}")
        merged.update(d)
    return merged

_EffectGeneratorResult = TypeVar("_EffectGeneratorResult")
EffectGenerator: TypeAlias = Generator[Any, Any, _EffectGeneratorResult]
"""``EffectGenerator[T]`` = ``Generator[Any, Any, T]``: a ``@do`` body returning T with
unchecked effects. Declare the effects too with ``Generator[E, Any, T]``."""
race = Race  # lowercase alias

# Removed concepts — importing one still works at runtime and using it raises an error
# that names the replacement. Type checkers do not see them, so ``from doeff import
# KleisliProgram`` is an error on the import line, as for any name doeff does not have
# (tests/test_public_name_types.py).
if not TYPE_CHECKING:

    class _Removed:
        def __init__(self, name: str, reason: str) -> None:
            self._name = name
            self._reason = reason
            self.__doc__ = f"Removed from doeff; calling or using it raises RuntimeError: {reason}."

        def __call__(self, *a: object, **kw: object) -> NoReturn:
            raise RuntimeError(f"{self._name} was removed: {self._reason}")

        def __getattr__(self, attr: str) -> NoReturn:
            raise RuntimeError(f"{self._name} was removed: {self._reason}")

    Delegate = _Removed("Delegate", "use 'yield effect' to re-perform in handler body")
    WithIntercept = _Removed("WithIntercept", "use WithObserve")
    KleisliProgram = _Removed("KleisliProgram", "use @do instead")
    Modify = _Removed("Modify", "use Get + Put")
    AllocVar = _Removed("AllocVar", "use var_store directly")
    Discontinued = _Removed("Discontinued", "concept removed")
    Discontinue = _Removed("Discontinue", "concept removed")
    graph_snapshot = _Removed("graph_snapshot", "concept removed")
    # NOTHING — use Nothing (lowercase singleton) instead
    ReadVar = _Removed("ReadVar", "use var_store directly")
    CacheGet = _Removed("CacheGet", "renamed to MemoGet in doeff_core_effects.memo_effects")
    CacheExists = _Removed(
        "CacheExists", "renamed to MemoExists in doeff_core_effects.memo_effects"
    )
    cache = _Removed("cache", "cache module removed")
    presets = _Removed("presets", "presets module removed")
    rust_vm = _Removed("rust_vm", "use PyVM directly")
    default_handlers = _Removed(
        "default_handlers", "compose handlers by calling handler(program)"
    )
    async_run = _Removed("async_run", "use run() with scheduled()")
    default_async_handlers = _Removed(
        "default_async_handlers", "compose handlers by calling handler(program)"
    )

__version__ = "0.8.0"


# Handlers are imported from the package doeff_core_effects, not from doeff: the package shows its
# names from its entry, and the modules that define them start with ``_`` (ADR-DOE-API-001 R5).
# The handlers are the names it exports from these modules, so no list of names is kept here.
_HANDLER_HOME = "doeff_core_effects"
_HANDLER_MODULES = ("doeff_core_effects._handlers", "doeff_core_effects._scheduler")


def _handler_module(name: str) -> str | None:
    """``doeff_core_effects`` when it exports the name from a module of ``_HANDLER_MODULES``.

    Reads only loaded modules (``import doeff`` loads them), so a lookup while doeff is
    still initializing imports nothing.
    """
    package = _sys.modules.get(_HANDLER_HOME)
    if package is None or name.startswith("_") or name not in vars(package):
        return None
    exported = vars(package)[name]
    defined_in_a_handler_module = any(
        module in _sys.modules and vars(_sys.modules[module]).get(name) is exported
        for module in _HANDLER_MODULES
    )
    return _HANDLER_HOME if defined_in_a_handler_module else None


def _looked_up_by_from_import() -> bool:
    """Whether the frame that asked ``__getattr__`` runs ``from doeff import <name>``.

    Python turns an AttributeError from a module ``__getattr__`` into False in
    ``hasattr`` and into "cannot import name ..." in ``from doeff import``, by the
    same path, so no exception type keeps both: the import statement gets an
    ImportError, every other lookup an AttributeError.
    """
    frame = _sys._getframe(2)  # 0: this function, 1: __getattr__, 2: the lookup
    return frame.f_code.co_code[frame.f_lasti] == _opcode.opmap["IMPORT_FROM"]


if not TYPE_CHECKING:
    # Hidden from type checkers: a module ``__getattr__`` returning ``object`` makes every
    # name look present, so ``from doeff import state`` (or a misspelling) was no error at
    # the import line. ``DoeffRunContext`` stays visible through the TYPE_CHECKING import above.

    def __getattr__(name: str) -> object:
        """``DoeffRunContext`` belongs to the CLI (argparse, rich, Hy); load it on first use.

        Importing it eagerly made every ``import doeff`` load the CLI stack.

        A handler name (``state``, ``reader``, ``scheduled``, ...) says the module to
        import it from.
        """
        if name == "DoeffRunContext":
            from doeff.cli.run_services import DoeffRunContext

            globals()[name] = DoeffRunContext
            return DoeffRunContext
        home = _handler_module(name)
        if home is None:
            raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
        hint = f"it is in {home}: from {home} import {name}"
        if _looked_up_by_from_import():
            raise ImportError(
                f"cannot import name {name!r} from {__name__!r}; {hint}", name=__name__
            )
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}; {hint}")

    def __dir__() -> list[str]:
        """The module's names, with ``DoeffRunContext`` before its first use (PEP 562).

        Without it, ``dir(doeff)`` showed ``DoeffRunContext`` only once some code had read it, so the
        names a reader of ``dir`` saw depended on what ran first.
        """
        return sorted({*globals(), "DoeffRunContext"})
