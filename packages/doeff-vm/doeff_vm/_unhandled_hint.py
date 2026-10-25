"""The lines an UnhandledEffect message ends with.

The VM knows no handler, so it cannot say which one to install. A package that
ships handlers registers a function from an effect to the line naming the handler
and how to install it (``doeff_core_effects.unhandled_hints`` holds the one for the
effects of that reference implementation). When no handler takes an effect, the VM calls ``hint_for`` and ends
the message with the line it returns; an effect no registered function answers for
(an effect class of the user's own) gets the general line, how to install a handler
for it with ``handler(fn)(prog)``.

Before the hint, the VM names the handlers in scope whose effect annotation left the
effect out (``skipped_by_annotation``): the VM knows which handlers were in scope and
the types each one's annotation admits (``doeff_vm._effect_types``), and this module
writes the line.
"""

from collections.abc import Callable, Sequence

HintFor = Callable[[object], str | None]

# A handler in scope that filters by its annotation: (the name the chain line shows, the
# types the annotation admits). The VM builds these pairs; it is the shape the Rust side
# can hand over without a class of its own.
TypedHandler = tuple[str, tuple[type, ...]]

_hint_fors: tuple[HintFor, ...] = ()


def register_unhandled_effect_hint(hint_for_effect: HintFor) -> None:
    """Ask ``hint_for_effect`` too; functions registered earlier are asked first."""
    global _hint_fors  # noqa: PLW0603 - one set per process, read by every VM's unhandled error
    _hint_fors = (*_hint_fors, hint_for_effect)


def hint_for(effect: object) -> str:
    """The first line a registered function gives for the effect, or the general line."""
    hints = (hint_for_effect(effect) for hint_for_effect in _hint_fors)
    registered = next((hint for hint in hints if hint is not None), None)
    return _install_a_handler(effect) if registered is None else registered


def _install_a_handler(effect: object) -> str:
    """How to install a handler for an effect no package names a handler for."""
    name = type(effect).__name__
    return (
        f"install a handler for {name}: run(fn(prog)), where fn is a def (effect: {name}, k) "
        f"marked with @handler that returns (yield Resume(k, value))"
    )


def skipped_by_annotation(effect: object, typed_handlers: Sequence[TypedHandler]) -> str | None:
    """The line naming the handlers whose effect annotation left ``effect`` out, or None.

    ``typed_handlers`` are the filtering handlers in scope, innermost first. When one
    of them admits the effect, a handler annotated for it saw it and passed it on (an
    ``Ask`` whose key no handler holds), so the annotations are not why it fell off
    and no line is written.
    """
    if any(isinstance(effect, admitted) for _, admitted in typed_handlers):
        return None
    entries = tuple(
        f"{name} (effect: {_admitted_text(admitted, type(effect))})"
        for name, admitted in typed_handlers
    )
    # A handler installed twice in the stack is named once, where it is innermost.
    named = tuple(entry for index, entry in enumerate(entries) if entry not in entries[:index])
    if not named:
        return None
    return (
        f"skipped by annotation: {', '.join(named)}"
        " — a handler only sees the effects its effect parameter is annotated with"
    )


_SPELLED_TYPES = 3  # a longer union (a defhandler of many clauses) shows its first ones and its size


def _admitted_text(admitted: tuple[type, ...], effect_type: type) -> str:
    """The admitted types as an annotation spells them, telling apart a class that shares the effect's name.

    Two classes of one name are one module imported twice (as ``__main__`` and by its
    name) or a class made anew per call; the bare name would read as the effect's own.
    """
    same_name = tuple(t for t in admitted if t.__qualname__ == effect_type.__qualname__)
    if same_name:
        spelled = " | ".join(
            f"{t.__module__}.{t.__qualname__}" if t in same_name else t.__qualname__ for t in admitted
        )
        return f"{spelled}, not the effect's {effect_type.__module__}.{effect_type.__qualname__}"
    if len(admitted) > _SPELLED_TYPES:
        first = " | ".join(t.__qualname__ for t in admitted[:_SPELLED_TYPES])
        return f"{first} | … ({len(admitted)} types)"
    return " | ".join(t.__qualname__ for t in admitted)
