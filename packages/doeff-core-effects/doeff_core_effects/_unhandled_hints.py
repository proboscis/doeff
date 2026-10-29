"""Which handler to install when a core effect is unhandled — the one table.

When no handler takes an effect, the UnhandledEffect message ends with
``hint: <line>``: the line of the first entry below whose types the effect is an
instance of. A line names the handler and the composition to write around the
user's program ``prog``, the way the README's handler table does. It never names an
internal key: ``writer`` without ``state()`` fails on ``writer``'s own ``Get``, and
the line for ``Get`` says to install ``state()`` outside ``writer``.

Importing ``doeff_core_effects`` registers ``hint_for`` with the VM
(``doeff_vm._unhandled_hint``).
"""

from dataclasses import dataclass

from doeff._vm import register_unhandled_effect_hint
from doeff_core_effects._effects import (
    Ask,
    Await,
    Get,
    Listen,
    Local,
    Put,
    ReadWriterLog,
    SlogEffect,
    Try,
    WriterTellEffect,
)
from doeff_core_effects._scheduler import (
    AcquireSemaphore,
    Cancel,
    CompletePromise,
    CreateExternalPromise,
    CreatePromise,
    CreateSemaphore,
    Discard,
    FailPromise,
    Gather,
    Race,
    ReleaseSemaphore,
    Spawn,
    Wait,
)


@dataclass(frozen=True)
class UnhandledEffectHint:
    """The line for effects of ``effect_types`` that no handler took."""

    effect_types: tuple[type, ...]
    line: str


_SCHEDULER_EFFECTS = (
    Spawn,
    Wait,
    Gather,
    Race,
    Cancel,
    Discard,
    CreatePromise,
    CompletePromise,
    FailPromise,
    CreateSemaphore,
    AcquireSemaphore,
    ReleaseSemaphore,
)

UNHANDLED_EFFECT_HINTS: tuple[UnhandledEffectHint, ...] = (
    UnhandledEffectHint(
        (Ask,),
        "Ask is handled by reader(env={...}) or lazy_ask(env={...}) holding the key: "
        "run(reader(env={...})(prog))",
    ),
    UnhandledEffectHint(
        (Local,),
        "Local is handled by lazy_ask(env={...}), which also answers Ask: "
        "run(lazy_ask(env={...})(prog))",
    ),
    UnhandledEffectHint(
        (Get, Put),
        "Get and Put are handled by state(): run(state()(prog)); "
        "writer needs state() outside it: run(state()(writer(prog)))",
    ),
    UnhandledEffectHint(
        (WriterTellEffect,),
        "Tell is handled by writer, which needs state() outside it: run(state()(writer(prog)))",
    ),
    UnhandledEffectHint(
        (ReadWriterLog,),
        "ReadWriterLog is handled by writer, which needs state() outside it: "
        "run(state()(writer(prog)))",
    ),
    UnhandledEffectHint(
        (SlogEffect,),
        "slog is handled by slog_handler (slog_discard_handler drops it): run(slog_handler(prog))",
    ),
    UnhandledEffectHint((Try,), "Try is handled by try_handler: run(try_handler(prog))"),
    UnhandledEffectHint(
        (Listen,), "Listen is handled by listen_handler: run(listen_handler(prog))"
    ),
    UnhandledEffectHint(
        (Await,),
        "Await is handled by await_handler, which needs scheduled outside it: "
        "run(scheduled()(await_handler(prog)))",
    ),
    UnhandledEffectHint(
        (CreateExternalPromise,),
        "CreateExternalPromise is handled by scheduled; await_handler performs it, "
        "so install scheduled outside it: run(scheduled()(await_handler(prog)))",
    ),
    *(
        UnhandledEffectHint(
            (effect_type,),
            f"{effect_type.__name__} is handled by scheduled: run(scheduled()(prog))",
        )
        for effect_type in _SCHEDULER_EFFECTS
    ),
)


def hint_for(effect: object) -> str | None:
    """The line of the first entry whose types the effect is an instance of."""
    return next(
        (hint.line for hint in UNHANDLED_EFFECT_HINTS if isinstance(effect, hint.effect_types)),
        None,
    )


register_unhandled_effect_hint(hint_for)
