"""Before running one example the same as `hy <example>`, changes the macro expansion into a form that does not change the answer (a child of test_examples_run.py).

Usage: python run_example_mutated_expansion.py <example path> <macro name>…

Mutation "run under a do-nothing handler": replaces the Program the expansion builds with a Program (WithHandler) that runs under a handler passing every effect outward.
The answer after run is unchanged, and only the class name, type, and attributes of the value the expansion builds change — the wrapped value becomes a WithHandler whether the original is a Traverse effect or a Program
(Call), so it always changes regardless of the macro's current expansion shape. If the example prints that shape, the output changes even though not one character of the macro changed
— so we can name beforehand that the example turns red when a macro owner changes the expansion without changing the answer. Wrapping per macro (_MUTATIONS):
- traverse, for/do (the expansion is an expression that builds a Traverse effect or Program): wrap that value —
    `(WithHandlerType (do (fn [effect k] (yield (Pass effect k)))) <original expansion>)` (the evaluation timing stays as is).
- fnk (the expansion is an expression that builds a kleisli function): replace the built function with a function that wraps the value it returns.
- defk (the expansion is a statement that defines a kleisli function): after the definition, rebind that name to a function that wraps the value it returns.
Only the outermost value is wrapped: the shape of the item function and the contents of the effects that inner handlers receive are not changed.

The mutation is applied only to the expansion when compiling the example's module (the module `hy <example>` runs). Even if a lib `.hy` that the example imports uses the same macro,
its expansion is not changed — by construction this prevents the bytecode of a lib compiled with the changed expansion from being written to the store shared by the machine (DOEFF_HY_CODE_STORE)
and a run unaware of the mutation from reading the wrapped expansion. The wrapping function does not capture the example's path (the example is identified by sys.argv[0]) —
the doeff-hy bytecode guard builds a digest from the closure of the macro function, so if it held a different path per example, the records of libs using that macro
would grow per example.

The macro table is `_hy_macros` (Hy name -> expansion function) of doeff-hy's macros module, and the example's `(require doeff-hy.macros …)` pulls from there
(also via `*`, `:macros`, or an alias), so the named macros there are swapped before the example runs. If a named macro is not in doeff-hy
or this entry point does not know how to wrap it, it exits non-zero so the mutation does not silently miss.
"""

import functools
import sys
from collections.abc import Callable
from pathlib import Path

import doeff_hy.macros as doeff_hy_macros
import hy
from doeff_hy_bytecode_guard._records import MACRO_TABLE

# Head of the expression that wraps the expansion: fetches doeff's public names without binding names (independent of the names the example imports).
_WITH_HANDLER_TEXT = '(. (__import__ "doeff" :fromlist #("WithHandlerType")) WithHandlerType)'
# The wrapping handler: passes every effect it receives to the outer handler.
_PASS_EVERYTHING_TEXT = (
    '((. (__import__ "doeff" :fromlist #("do")) do)'
    ' (fn [effect k] (yield ((. (__import__ "doeff" :fromlist #("Pass")) Pass) effect k))))'
)
_WITH_HANDLER = hy.read(_WITH_HANDLER_TEXT)
_PASS_EVERYTHING = hy.read(_PASS_EVERYTHING_TEXT)
# A function that replaces a kleisli function with one that makes a Program running the value it returns under a do-nothing handler (copies the function's name, docstring, etc.).
_PROGRAMS_UNDER_HANDLER = hy.read(
    f"""(fn [kleisli]
  (setv pass-everything {_PASS_EVERYTHING_TEXT})
  ((. (__import__ "functools") update_wrapper)
   (fn [#* args #** kwargs] ({_WITH_HANDLER_TEXT} pass-everything (kleisli #* args #** kwargs)))
   kleisli))"""
)

# Wrapping per macro form: (form passed to the macro, original expansion) -> mutated expansion.
Mutation = Callable[[tuple[hy.models.Object, ...], hy.models.Object], hy.models.Object]


def _value_under_handler(
    forms: tuple[hy.models.Object, ...], expansion: hy.models.Object
) -> hy.models.Object:
    """An expansion in which the value the expansion builds (a Traverse effect or Program) becomes a Program running under a do-nothing handler (the original expansion is
    evaluated at the same time as before)."""
    return hy.models.Expression([_WITH_HANDLER, _PASS_EVERYTHING, expansion])


def _function_returning_programs(
    forms: tuple[hy.models.Object, ...], expansion: hy.models.Object
) -> hy.models.Object:
    """An expansion in which the kleisli function the expansion builds is replaced with a function that makes the value it returns a Program running under a do-nothing handler."""
    return hy.models.Expression([_PROGRAMS_UNDER_HANDLER, expansion])


def _definition_returning_programs(
    forms: tuple[hy.models.Object, ...], expansion: hy.models.Object
) -> hy.models.Object:
    """An expansion in which, after the definition, the name of the kleisli function the expansion defines (the macro's first argument) is rebound to a function that wraps the value it returns."""
    name = forms[0]
    rebind = hy.models.Expression(
        [
            hy.models.Symbol("setv"),
            name,
            hy.models.Expression([_PROGRAMS_UNDER_HANDLER, name]),
        ]
    )
    return hy.models.Expression([hy.models.Symbol("do"), expansion, rebind])


# The macros this entry point knows how to wrap (Hy name -> wrapping). MUTATED_MACROS in test_examples_run.py is chosen from the names in this table.
_MUTATIONS: dict[str, Mutation] = {
    "traverse": _value_under_handler,
    "for/do": _value_under_handler,
    "fnk": _function_returning_programs,
    "defk": _definition_returning_programs,
}


def _takes_compiler(macro: Callable[..., hy.models.Object]) -> bool:
    """Hy passes the current compiler only to a macro whose first argument is `_hy_compiler` (the macro entry)."""
    return macro.__code__.co_varnames[:1] == ("_hy_compiler",)


def _mutated(
    macro: Callable[..., hy.models.Object], mutation: Mutation
) -> Callable[..., hy.models.Object]:
    """A macro with the same arguments and answer as the macro, whose expansion alone, when compiling the example file, is changed by the mutation.

    Hy passes the compiler to a function whose first argument is `_hy_compiler` — the wrapping function is always written that way and passes it only when the original macro accepts a compiler.
    Whether the module being compiled is the example is told by the module's file and sys.argv[0] (repacked to the example path that hy runs) (the module name that `hy <example>`
    compiles is the file's stem). An expansion without a compiler (when hy.macroexpand passes no compiler) is left as is.
    """

    @functools.wraps(macro)
    def expand(
        _hy_compiler: hy.compiler.HyASTCompiler | None, *forms: hy.models.Object
    ) -> hy.models.Object:
        expansion = macro(_hy_compiler, *forms) if _takes_compiler(macro) else macro(*forms)
        if _hy_compiler is None:
            return expansion
        compiled = _hy_compiler.module.__dict__.get("__file__")
        if compiled is not None and Path(compiled).resolve() == Path(sys.argv[0]).resolve():
            return mutation(forms, expansion)
        return expansion

    return expand


def main(example: str, macros: list[str]) -> None:
    table: dict[str, Callable[..., hy.models.Object]] = vars(doeff_hy_macros)[MACRO_TABLE]
    missing = [name for name in macros if hy.mangle(name) not in table]
    if missing:
        raise SystemExit(
            f"doeff-hy has no macro {' '.join(missing)} — align the table of macros wrapped by the mutation (MUTATED_MACROS in test_examples_run.py)"
            f" with the current macros"
        )
    unknown = [name for name in macros if name not in _MUTATIONS]
    if unknown:
        raise SystemExit(
            f"this entry point does not know how to wrap {' '.join(unknown)} — add to _MUTATIONS or remove from MUTATED_MACROS"
        )
    for name in macros:
        key = hy.mangle(name)
        table[key] = _mutated(table[key], _MUTATIONS[name])
    sys.argv = ["hy", example]
    from hy.cmdline import hy_main

    hy_main()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2:])
