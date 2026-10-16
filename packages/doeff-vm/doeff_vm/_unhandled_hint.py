"""The hint line an UnhandledEffect message ends with.

The VM knows no handler, so it cannot say which one to install. A package that
ships handlers registers a function from an effect to the line naming the handler
and how to install it (``doeff_core_effects.unhandled_hints`` holds the one for the
core effects). When no handler takes an effect, the VM calls ``hint_for`` and ends
the message with the line it returns.
"""

from collections.abc import Callable

HintFor = Callable[[object], str | None]

_hint_fors: tuple[HintFor, ...] = ()


def register_unhandled_effect_hint(hint_for_effect: HintFor) -> None:
    """Ask ``hint_for_effect`` too; functions registered earlier are asked first."""
    global _hint_fors  # noqa: PLW0603 - one set per process, read by every VM's unhandled error
    _hint_fors = (*_hint_fors, hint_for_effect)


def hint_for(effect: object) -> str | None:
    """The first line a registered function gives for the effect, or None."""
    hints = (hint_for_effect(effect) for hint_for_effect in _hint_fors)
    return next((hint for hint in hints if hint is not None), None)
