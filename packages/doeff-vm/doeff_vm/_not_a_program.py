"""What was meant by a value that is not a program, and how to fix it.

The VM runs DoExpr nodes and effects. Given anything else it can only name the type
("expected DoExpr or EffectBase, got: <class 'function'>"). The usual causes are a
``@do`` function passed or yielded without calling it, a generator function without
``@do``, and a plain value yielded as if ``yield`` returned it; the messages here name
the cause and the fix. They are built only after the VM rejected the value, so a program
pays nothing for them.

The other way round, a program can go where a handler's part goes: an ``(effect, k)``
handler called with the program it was meant to handle (``fetch_handler(prog())``), which
the VM can only refuse for the missing ``k``. ``handler_applied_as_a_program`` names that
mistake, also only after the call failed.

``@do`` (doeff.do) and ``@effectful`` mark the function they return with the generator
function they wrap (``__doeff_generator_function__``), the mark ``handler_spec``
(doeff_vm._effect_types) also reads.
"""

import inspect
import reprlib
import warnings
from types import FunctionType, GeneratorType

from doeff_vm import Call, EffectBase
from doeff_vm._effect_types import handler_effect_types


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


def program_shown(value: object) -> str:
    """How a message writes ``value`` where it names the program to give a handler: the call
    of a ``@do`` function as it was made (``prog()``, or ``prog(...)`` when it was given
    arguments), the call that builds the program for the ``@do`` function itself
    (``uncalled_do_function_call``), and ``program`` for anything else."""
    if isinstance(value, Call):
        name = getattr(value.function.function, "__name__", None)
        if not isinstance(name, str):
            return "program"
        return f"{name}(...)" if value.args or value.kwargs else f"{name}()"
    call = uncalled_do_function_call(value)
    return "program" if call is None else call


def _takes_effect_and_k(function: object) -> bool:
    """Whether ``function`` is an ``(effect, k)`` handler: two positional parameters, the
    second named ``k`` (doeff's name for the continuation) or the first annotated with
    effect classes."""
    if type(function) is not FunctionType:
        return False
    code = function.__code__
    if code.co_argcount != 2 or code.co_flags & inspect.CO_VARARGS:
        return False
    if code.co_varnames[1] == "k":
        return True
    with warnings.catch_warnings():
        # An annotation that does not resolve only means "not judged by it" here.
        warnings.simplefilter("ignore")
        effect_types = handler_effect_types(function)
    return effect_types is not None and all(
        isinstance(kind, type) and issubclass(kind, EffectBase) for kind in effect_types
    )


def handler_applied_as_a_program(
    function: object, args: tuple[object, ...], kwargs: dict[str, object] | None
) -> str | None:
    """The message of the TypeError for a failed call of ``function``, a ``@do`` definition's
    function, when it is an ``(effect, k)`` handler body called with one argument that is not an
    effect — applied to a program while its def has ``@do`` instead of ``@handler``
    (``fetch_handler(prog())``); ``None`` for any other failed call (its own error stands).
    """
    if len(args) != 1 or kwargs or isinstance(args[0], EffectBase):
        return None
    if not _takes_effect_and_k(function):
        return None
    name = getattr(function, "__name__", "handler")
    shown = program_shown(args[0])
    return (
        f"{name} takes (effect, k): it is a handler body, not a function that builds a program, "
        f"so {name}({shown}) cannot run. Mark its def with @handler in place of @do: then "
        f"{name}({shown}) runs the program under it"
    )
