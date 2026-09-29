"""Core effects — Ask, Get, Put, Tell, HttpRequest.

These are EffectBase subclasses. Yield them from @do functions.
Handlers (reader, state, writer) handle them.
"""

from collections.abc import Awaitable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Never, TypeVar

from doeff_vm import EffectBase

if TYPE_CHECKING:
    from doeff_vm import Err, Ok  # noqa: F401 - named in the string answer type of Try

    from doeff import Program
    from doeff_core_effects.http_effects import HttpError, HttpRequest, HttpResponse  # noqa: F401

# Answer types: each effect declares what its handler answers with (EffectBase[T]), so
# ``x = yield from Get("k")`` is typed. Env and state values are dynamic (Any); use
# ``isinstance`` or a typed wrapper at the read site.

_T = TypeVar("_T")


class Ask(EffectBase[Any]):
    """Reader effect: get a value from the environment by key."""

    def __init__(self, key: object) -> None:
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"Ask({self.key!r})"


class Get(EffectBase[Any]):
    """State effect: get a value from mutable state by key."""

    def __init__(self, key: object) -> None:
        super().__init__()
        self.key = key

    def __repr__(self):
        return f"Get({self.key!r})"


class Put(EffectBase[None]):
    """State effect: set a value in mutable state."""

    def __init__(self, key: object, value: object) -> None:
        super().__init__()
        self.key = key
        self.value = value

    def __repr__(self):
        return f"Put({self.key!r}, {self.value!r})"


def Tell(message: object) -> "WriterTellEffect":  # noqa: N802
    """Convenience: Tell(message) → WriterTellEffect(message)."""
    return WriterTellEffect(message)


class Local(EffectBase[_T], Generic[_T]):
    """Scoped environment injection: run program with overridden env entries.

    yield Local({key: value, ...}, program) → result of program
    """

    __doeff_runs_carried__: ClassVar[frozenset[str]] = frozenset({"program"})

    def __init__(self, env: dict[Any, Any], program: "Program[_T]") -> None:
        super().__init__()
        self.env = env
        self.program = program

    def __repr__(self):
        return f"Local({self.env!r}, ...)"


class Listen(EffectBase[tuple[_T, list[Any]]], Generic[_T]):
    """Collect all effects of given types emitted during program execution.

    yield Listen(program, types=(WriterTellEffect,)) → (result, collected)
    """

    __doeff_runs_carried__: ClassVar[frozenset[str]] = frozenset({"program"})

    def __init__(self, program: "Program[_T]", types: tuple[type, ...] | None = None) -> None:
        super().__init__()
        self.program = program
        self.types = types

    def __repr__(self):
        return "Listen(...)"


class Await(EffectBase[_T], Generic[_T]):
    """Await a Python coroutine or future. Bridges async into doeff.

    yield Await(some_coroutine) → result

    ``deadline`` (a ``time.monotonic()`` instant) marks a clock wait whose
    completion time is known, e.g. ``asyncio.sleep``: the scheduler does not
    report it as a stall until the deadline is exceeded (agora-redesign #765).
    """

    def __init__(self, coroutine: Awaitable[_T], deadline: float | None = None) -> None:
        super().__init__()
        self.coroutine = coroutine
        self.deadline = deadline

    def __repr__(self):
        return "Await(...)"


class Try(EffectBase["Ok[_T] | Err"], Generic[_T]):
    """Wrap a program to catch errors as Ok/Err results.

    yield Try(some_program) → Ok(value) or Err(error)
    """

    __doeff_runs_carried__: ClassVar[frozenset[str]] = frozenset({"program"})

    def __init__(self, program: "Program[_T]") -> None:
        super().__init__()
        self.program = program

    def __repr__(self):
        return f"Try({self.program!r})"


class Resumption(Enum):
    """How a handler clause may end for an effect type (ADR-DOE-CORE-EFFECTS-003 R15).

    An effect type declares it with the class attribute ``__doeff_resumption__``; a type that
    does not declare it is ``REQUIRED``. ``defhandler`` checks every clause against it when the
    handler is defined (and by name at macro expansion for ``Raise`` / ``Absent``).
    """

    REQUIRED = "required"  # an ordinary effect: the clause resumes (finish only with a stated reason)
    ABSENT_AS_ONLY = "absent-as-only"  # Absent: only absent-as resumes it; other handlers finish or reperform
    NEVER = "never"  # Raise: nobody resumes it — "failed, but went on as if it succeeded"


def resumption_of(effect_type: type) -> Resumption:
    """The resumption declared by ``effect_type`` (``REQUIRED`` when it declares none)."""
    declared = getattr(effect_type, "__doeff_resumption__", Resumption.REQUIRED)
    if not isinstance(declared, Resumption):
        raise TypeError(
            f"{effect_type.__name__}.__doeff_resumption__ must be a Resumption, got {declared!r}"
        )
    return declared


def runs_carried_of(effect_type: type) -> frozenset[str]:
    """The fields of ``effect_type`` holding a Program its handler runs where the effect was
    performed — under the handlers around the performing site, as if written there.

    An effect type declares them with the class attribute ``__doeff_runs_carried__``
    (``Try`` / ``Local`` / ``Listen`` run their ``program`` in place; ``Spawn``'s child task
    carries the spawner's handlers; ``SqlTransaction`` runs its ``program`` under the SQL
    handler that answered it).  A type that declares none runs what it carries elsewhere,
    if at all (a remote job).  A closure check reads the declared Programs as run at the
    performing site (agora-redesign #1456).
    """
    declared = getattr(effect_type, "__doeff_runs_carried__", frozenset())
    if not (isinstance(declared, frozenset) and all(isinstance(name, str) for name in declared)):
        raise TypeError(
            f"{effect_type.__name__}.__doeff_runs_carried__ must be a frozenset of field names, "
            f"got {declared!r}"
        )
    return declared


@dataclass(frozen=True)
class Absent(EffectBase[Never]):
    """An expected absence — the Maybe side (ADR-DOE-CORE-EFFECTS-003 R1).

    Carries no value, only ``why`` (a sentence for records and debugging). Handlers do not
    resume it: ``maybe`` finishes its scope with ``Nothing`` and ``absent-as`` finishes it with
    its default. The one exception is ``absent-as`` resuming an Absent that a ``<-`` written
    directly inside it produced (R8). Not related to ``Raise`` by inheritance.
    """

    why: str
    __doeff_resumption__: ClassVar[Resumption] = Resumption.ABSENT_AS_ONLY

    def __post_init__(self) -> None:
        if not isinstance(self.why, str) or not self.why:
            raise TypeError(f"Absent.why must be a non-empty str, got {self.why!r}")


_E = TypeVar("_E")


@dataclass(frozen=True)
class Raise(EffectBase[Never], Generic[_E]):
    """An expected failure with its reason — the Result side (ADR-DOE-CORE-EFFECTS-003 R1).

    Nobody resumes it: ``result`` finishes its scope with ``Err(reason)``, ``on-raise`` with the
    business answer its pattern maps the reason to. The reason is a value, never a Python
    exception — exceptions stay implementation errors (R11), and ``Try`` keeps folding those.
    """

    reason: _E
    __doeff_resumption__: ClassVar[Resumption] = Resumption.NEVER

    def __post_init__(self) -> None:
        if isinstance(self.reason, BaseException):
            raise TypeError(
                "Raise の理由に Python の例外は置けない — 例外は実装の誤りのまま上げ、想定内の失敗は"
                f"値で表す(ADR-DOE-CORE-EFFECTS-003 R11): {self.reason!r}"
            )


class WriterTellEffect(EffectBase[None]):
    """Writer effect: a single accumulated message.

    This is the wire type for Tell() only. Listen collects these by default.
    Structured observability logs are SlogEffect, a disjoint wire type
    (ADR-DOE-CORE-EFFECTS-001 R1).
    """

    def __init__(self, msg: object) -> None:
        super().__init__()
        self.msg = msg

    def __repr__(self):
        return f"Tell({self.msg!r})"


class SlogEffect(EffectBase[None]):
    """Structured log (observability) effect: msg + kwargs.

    This is the wire type for slog(). slog_handler displays it on stderr;
    capture flows as values via Listen(prog, types=(SlogEffect,)).
    Not a WriterTellEffect: Writer accumulation and observability have
    opposite default behaviors (ADR-DOE-CORE-EFFECTS-001).
    """

    def __init__(self, msg: object, **kwargs: object) -> None:
        super().__init__()
        self.msg = msg
        self.kwargs = kwargs

    def __repr__(self):
        kw = ", ".join(f"{k}={v!r}" for k, v in self.kwargs.items())
        if kw:
            return f"slog({self.msg!r}, {kw})"
        return f"slog({self.msg!r})"


# Convenience alias
Slog = SlogEffect

_HTTP_EFFECTS = frozenset({"HttpError", "HttpRequest", "HttpResponse"})


def __getattr__(name: str) -> object:
    """HTTP effects are defined in Hy; load Hy only when they are asked for.

    Importing them eagerly made ``import doeff`` load the Hy compiler for every
    program (import floor +13 MiB, measured 2026-09-23).
    """
    if name not in _HTTP_EFFECTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from doeff_core_effects import http_effects  # a .hy module; loads Hy on first use

    value = getattr(http_effects, name)
    globals()[name] = value
    return value


def slog(msg: object, **kwargs: object) -> SlogEffect:
    """Convenience function to create a SlogEffect."""
    return SlogEffect(msg, **kwargs)
