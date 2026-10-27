"""Reference effects — Ask, Get, Put, Tell, HttpRequest.

These are EffectBase subclasses. Yield them from @do functions.
Handlers (reader, state, writer) handle them.
"""

import difflib
from collections.abc import Awaitable, Callable, Hashable
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any, ClassVar, Generic, Never, TypeVar

from doeff import EffectBase
from doeff_core_effects.extra_hints import extra_needed

if TYPE_CHECKING:
    from doeff import Err, Ok, Program  # noqa: F401 - named in the string answer type of Try
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

    def __repr__(self) -> str:
        return f"Ask({self.key!r})"


_MISSING_KEY_SHOWN_KEYS = 8


class MissingEnvKeyError(KeyError):
    """Ask(key) found no value: the env of the handler answering Ask has no such key.

    A KeyError, so ``except KeyError`` still catches it; ``args`` is ``(key,)`` as
    for a dict lookup. ``env_keys`` are the keys the env did hold. ``str()`` is
    the message itself — KeyError's own ``str()`` quotes it and folds the
    newlines into ``\\n``. The message names no CLI flag; ``doeff run`` adds
    its own hint when the error reaches it.
    """

    def __init__(self, key: Hashable, env_keys: tuple[Hashable, ...] = ()) -> None:
        super().__init__(key)
        self.key = key
        self.env_keys = env_keys

    def __str__(self) -> str:
        return _missing_key_message(self.key, self.env_keys)


def _missing_key_message(key: Hashable, env_keys: tuple[Hashable, ...]) -> str:
    """Name the keys the env holds and the close names of a missing Ask key."""
    return "\n".join((
        f"Ask: key not found: {key!r}",
        *_close_key_lines(key, env_keys),
        _env_keys_line(env_keys),
        "  To provide it, put the key in the env of the handler that answers Ask:"
        " reader(env={...}) or lazy_ask(env={...}).",
    ))


def _close_key_lines(key: Hashable, env_keys: tuple[Hashable, ...]) -> tuple[str, ...]:
    """The difflib close names of a str key among the env's str keys (none or one line)."""
    if not isinstance(key, str):
        return ()
    close = difflib.get_close_matches(key, [k for k in env_keys if isinstance(k, str)], n=3)
    if not close:
        return ()
    return (f"  Did you mean {' or '.join(map(repr, close))}?",)


def _env_keys_line(env_keys: tuple[Hashable, ...]) -> str:
    """The env's keys in its own order — the first few of a large env and the count of the rest."""
    if not env_keys:
        return "  The env is empty."
    if len(env_keys) <= _MISSING_KEY_SHOWN_KEYS:
        return f"  Keys in the env: {', '.join(map(repr, env_keys))}"
    shown = ", ".join(map(repr, env_keys[:_MISSING_KEY_SHOWN_KEYS]))
    rest = len(env_keys) - _MISSING_KEY_SHOWN_KEYS
    return (
        f"  Keys in the env ({_MISSING_KEY_SHOWN_KEYS} of {len(env_keys)}):"
        f" {shown}, … {rest} more"
    )


class Get(EffectBase[Any]):
    """State effect: get a value from mutable state by key.

    With the state() handler, a missing key yields None (SPEC-EFF-002).
    """

    def __init__(self, key: object) -> None:
        super().__init__()
        self.key = key

    def __repr__(self) -> str:
        return f"Get({self.key!r})"


class Put(EffectBase[None]):
    """State effect: set a value in mutable state."""

    def __init__(self, key: object, value: object) -> None:
        super().__init__()
        self.key = key
        self.value = value

    def __repr__(self) -> str:
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

    def __repr__(self) -> str:
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

    def __repr__(self) -> str:
        return "Listen(...)"


class Await(EffectBase[_T], Generic[_T]):
    """Await a Python coroutine or future. Bridges async into doeff.

    yield Await(some_coroutine) → result

    ``deadline`` (a ``time.monotonic()`` instant) marks a clock wait whose
    completion time is known, e.g. ``asyncio.sleep``: the scheduler does not
    report it as a stall until the deadline is exceeded (agora-redesign #765).
    ``source_alive`` marks a watched wait, e.g. a child's exit descriptor: the
    scheduler does not report it as a stall while ``source_alive()`` is True
    (agora-redesign card ki-6222e22d4179). Both go to the promise the await
    handler creates — see ``CreateExternalPromise`` (invariant S1).
    """

    def __init__(
        self,
        coroutine: Awaitable[_T],
        deadline: float | None = None,
        source_alive: Callable[[], bool] | None = None,
    ) -> None:
        super().__init__()
        self.coroutine = coroutine
        self.deadline = deadline
        self.source_alive = source_alive

    def __repr__(self) -> str:
        return "Await(...)"


class Try(EffectBase["Ok[_T] | Err[Exception]"], Generic[_T]):
    """Wrap a program to catch errors as Ok/Err results.

    yield Try(some_program) → Ok(value) or Err(error) — ``error`` is the ``Exception``
    the program raised (``try_handler`` catches ``Exception``).
    """

    __doeff_runs_carried__: ClassVar[frozenset[str]] = frozenset({"program"})

    def __init__(self, program: "Program[_T]") -> None:
        super().__init__()
        self.program = program

    def __repr__(self) -> str:
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
    performed — under the handlers around the performing site, as if written there — or a
    function the handler calls to build each such Program (doeff-traverse's ``Traverse.f`` /
    ``Reduce.f``, called once per item).

    An effect type declares them with the class attribute ``__doeff_runs_carried__``
    (``Try`` / ``Local`` / ``Listen`` run their ``program`` in place; ``Spawn``'s child task
    carries the spawner's handlers; ``SqlTransaction`` runs its ``program`` under the SQL
    handler that answered it; ``Traverse`` runs what its ``f`` builds under the inner
    handlers it puts back).  A type that declares none runs what it carries elsewhere,
    if at all (a remote job).  A closure check reads the declared Programs as run at the
    performing site, and a declared function's body as the Program it builds
    (agora-redesign #1456・#2973).
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

    def __repr__(self) -> str:
        return f"Tell({self.msg!r})"


class ReadWriterLog(EffectBase[list[Any]]):
    """Writer effect: read a copy of the messages Tell has logged so far.

    yield ReadWriterLog() → [msg, ...] in the order told ([] before the first Tell).
    The writer handler answers it with a new list, so changing the list leaves the
    log as told. Without writer it is an unhandled effect.
    """

    def __repr__(self) -> str:
        return "ReadWriterLog()"


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

    def __repr__(self) -> str:
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
    program (import floor +13 MiB, measured 2026-09-23). Without Hy, asking for
    one names the extra ``http``, which also brings the handler that answers them.
    """
    if name not in _HTTP_EFFECTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    with extra_needed("http", "the HTTP effects are written in Hy"):
        from doeff_core_effects import http_effects  # a .hy module; loads Hy on first use

    value = getattr(http_effects, name)
    globals()[name] = value
    return value


def slog(msg: object, **kwargs: object) -> SlogEffect:
    """Convenience function to create a SlogEffect."""
    return SlogEffect(msg, **kwargs)
