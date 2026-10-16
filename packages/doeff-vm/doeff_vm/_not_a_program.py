"""What was meant by a value that is not a program, and how to fix it.

The VM runs DoExpr nodes and effects. Given anything else it can only name the type
("expected DoExpr or EffectBase, got: <class 'function'>"). The usual causes are a
``@do`` function passed or yielded without calling it, a generator function without
``@do``, and a plain value yielded as if ``yield`` returned it; the messages here name
the cause and the fix. They are built only after the VM rejected the value, so a program
pays nothing for them.

``@do`` (doeff.do) and ``@effectful`` mark the function they return with the generator
function they wrap (``__doeff_generator_function__``), the mark ``handler_spec``
(doeff_vm._effect_types) also reads.
"""

import inspect
import reprlib
from types import FunctionType, GeneratorType


def uncalled_do_function_call(value: object, *, name: str | None = None) -> str | None:
    """The call that builds the program — ``main()``, or ``main(...)`` when the function has a
    required parameter — when ``value`` is a ``@do`` function itself rather than the program it
    builds; ``None`` for anything else.

    ``run``, ``doeff run --program`` and ``yield`` take a program. ``name`` is the name the
    caller knows the function by (default: its ``__name__``) — ``doeff run`` shows the last
    part of ``--program``.
    """
    if type(value) is not FunctionType:
        return None
    fn = value.__dict__.get("__doeff_generator_function__")
    if fn is None:
        return None
    required = [
        parameter
        for parameter in inspect.signature(fn).parameters.values()
        if parameter.default is parameter.empty
        and parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
    ]
    shown = value.__name__ if name is None else name
    return f"{shown}(...)" if required else f"{shown}()"


def yielded_not_a_program(value: object) -> str:
    """The message of the TypeError the VM raises at a ``yield`` of ``value``, a value that is
    neither an effect nor a program."""
    if isinstance(value, FunctionType):
        call = uncalled_do_function_call(value)
        if call is not None:
            return (
                f"yield got the @do function {value.__name__!r} itself, not a program. "
                f"Call the function to build the program, then yield that: yield {call}"
            )
    if type(value) is GeneratorType and value.__name__ != "<genexpr>":
        return (
            f"yield got a generator from {value.__name__!r}, which has no @do. "
            f"Put @do on {value.__name__!r} so that calling it builds a program."
        )
    return (
        f"yield got {reprlib.repr(value)} ({type(value).__name__}), "
        "which is neither an effect nor a program. Yield an effect or a program to get "
        'its result (x = yield Ask("key"), x = yield sub()); a plain value needs no '
        "yield: use it as it is, or return it."
    )
