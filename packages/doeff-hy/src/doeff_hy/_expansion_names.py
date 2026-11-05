"""Single source of truth for the spelling of the doeff names that the handle / defhandler expansions read.

The expansion places imports and name reads inside the writer's module, class, and function. The `Resume`, `Transfer`,
`Pass`, and `WithHandlerType` it reads for clause operations and handlers (plus `Get`, `Put`, and `Some` when a session exists), if
placed under their bare names, are shared between the writer's names and the expansion's names whenever the writer names
their own effect the same (`Transfer` for a bank transfer, `Put` for a KV store). The clause-head `isinstance` and annotations then pointed at the expansion's name,
so effects silently slipped through to the outer handler, and at module level the expansion's import overwrote the writer's name.

Every name the expansion reads is imported under an alias with the `_doeff_` prefix, and the spelling used to read it is also taken from here (the same convention as the helper names
`_doeff_<name>` that the expansion for type checking pulls in -- static_view.py). No writer puts a `_doeff_`-prefixed name in their module.

- `alias`, `alias_symbol`: the alias spelling. Places where the expansion reads a name take it from here instead of writing the name directly.
- `import_form`: the Hy import that binds the aliases (the runtime expansion places it where it was written).
- `import_source`: the Python import that binds the aliases (the expansion for type checking places it once at the top of the module -- static_view.py).

- `is_alias_import`: whether an import has that spelling (so the post-processing of the type-checking expansion can move imports from expansions inside
  function and class bodies up to module level -- static_check.py).

The table is closed: every name the expansion reads is listed in `_HOMES` together with its home, and `home` rejects names not in the table (an unknown name is not
reported as an alias import -- the for/do docstring has the writer write `(import doeff [do :as _doeff-do])`).

Readers: handle.hy (`WithHandlerType`, `Pass`, the session's `Get`, `Put`, `Some`, and the return type `ProgramHandler` of the factory for a defhandler with arguments), clause_ops.hy (the clause-operation nodes and the type-checking expansion's
`typed_resume`, `typed_transfer`), binding_forms.py (`Put` and `Some` for `:=`), macros.hy (`Put` and `Some` for `set!`), defeffect-form in declarations.hy and
defrecord (:check) and defwire in record.hy (typing's `ClassVar` and `cast`, read by the type body -- a type body written in a class body
looks up names from module level, so the type-checking projection moves this import to module level).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from hy.models import Expression, Keyword, List, Object, Symbol

#: The alias prefix. Same as the helper names the type-checking expansion pulls in (static_view.deftest_annotation_name).
PREFIX = "_doeff_"

#: The names the expansion reads and the module each lives in (a closed table -- the names of clause-operation nodes are held by the nodes table in clause_ops.hy; their homes are written here).
_HOMES: dict[str, str] = {
    "Resume": "doeff",
    "Transfer": "doeff",
    "Pass": "doeff",
    "Some": "doeff",
    "typed_resume": "doeff",
    "typed_transfer": "doeff",
    "WithHandlerType": "doeff",
    "ProgramHandler": "doeff.program",
    "Get": "doeff_core_effects._effects",
    "Put": "doeff_core_effects._effects",
    "ClassVar": "typing",
    "cast": "typing",
}


def alias(name: str) -> str:
    """The spelling used when the expansion reads name (the `_doeff_` prefix)."""
    return f"{PREFIX}{name}"


def alias_symbol(name: str) -> Symbol:
    """The Symbol with which the expansion reads name. Creates a new Symbol on every call -- if one Symbol is reused across expansions, the position that the
    position-assigning macro (`locate-synthesized`) set in the first expansion remains, and the lines of later expressions shift to that line."""
    return Symbol(alias(name))


def effect_symbol() -> Symbol:
    """The local name (argument) that binds the effect received by the clause-dispatch function, and the Symbol with which the expansion reads it. Creates a new Symbol on every call (see the note on `alias_symbol`).

    This is a local name of the expansion, not a doeff name, but it is spelled with the same `_doeff_` alias convention so it does not collide with the writer's clause fields or session names. If bound
    as a bare `effect`, a clause whose field is named `effect` (`(Note [effect extra] ...)`) has its field binding `(setv effect (. effect effect))` overwrite the received effect,
    and the following field reads and the effect passed outward became the field's value. The names a writer can read when they write `effect` in a body are decided separately by
    `WRITER_VISIBLE` (`_clause-aliases` in handle.hy)."""
    return alias_symbol("effect")


def continuation_symbol() -> Symbol:
    """The local name (argument) that binds the continuation received by the clause-dispatch function, and the Symbol with which the expansion reads it. Same convention as `effect_symbol` -- if bound as a bare `k`,
    a clause whose field is named `k` (`(Kv [k v] ...)` for KV) has its field binding `(setv k (. effect k))` overwrite the continuation, and `(resume ...)` failed with
    `TypeError: argument 'k': 'int' object is not an instance of 'K'`."""
    return alias_symbol("k")


@dataclass(frozen=True)
class WriterName:
    """One name that a writer can read as the clause's effect or continuation when writing a clause body or a `:when` guard.

    - `written`: the name the writer writes (`effect`, `k`).
    - `internal`: the expansion's local name that binds it (a `_doeff_` alias -- an argument of the clause-dispatch function).
    - `declared`: the type the type-checking expansion declares for the name (the same type as the clause-dispatch function's argument).
    - `narrowed`: whether this is a value that the clause's `isinstance` narrows. The type-checking expansion binds a narrowed value, before the clause test, as the un-narrowed original,
      and the clause test narrows the writer's name too (assigning after narrowing would have the destination variable receive the narrowed generic type `EffectBase[Unknown]`,
      which turns reportUnknownVariableType red -- `_narrowed-alias-bindings` and `_clause-test` in handle.hy).
    """

    written: str
    internal: str
    declared: str
    narrowed: bool


#: The table of writer-visible names, bound only by clauses where the writer wrote the name. If a clause field or session name is the same name, the writer's name takes precedence (it is not bound).
WRITER_VISIBLE: tuple[WriterName, ...] = (
    WriterName("effect", alias("effect"), "object", narrowed=True),
    WriterName("k", alias("k"), "_doeff_Continuation", narrowed=False),
)


def home(name: str) -> str:
    """The module from which name is imported. A name not in the table raises KeyError (when the expansion reads a new name, add its home to the table)."""
    if name not in _HOMES:
        raise KeyError(f"name not in the expansion name table (expansion_names._HOMES): {name!r} -- add it to the table together with its home")
    return _HOMES[name]


def _by_home(names: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Group names by home (homes and names are deduplicated, in order of first appearance)."""
    unique = tuple(dict.fromkeys(names))
    return {
        module: tuple(name for name in unique if home(name) == module)
        for module in dict.fromkeys(home(name) for name in unique)
    }


def _module_model(module: str) -> Object:
    """The model for a module name. A dotted name is built in the form Hy reads (`(. a b)`) -- as a single Symbol the name would break.
    Do not go through the reader (`hy.read`): the models the reader makes carry a position of line 1, column 1, which the position-assigning macro (`locate-synthesized`)
    confuses with the position of the user's expression, pulling the range of the enclosing expression to line 1. The lines of type-check diagnostics (unused imports, undefined names)
    became 1:1 instead of the handler's line. The model built here has no position."""
    parts = module.split(".")
    if len(parts) == 1:
        return Symbol(module)
    return Expression([Symbol("."), *(Symbol(part) for part in parts)])


def import_form(names: Iterable[str]) -> Expression:
    """The Hy import that binds names under aliases (a `(do ...)` of `(import home [name :as alias ...])` per home). With no names it is
    `(do)`. The model it builds has no position (see the note on `_module_model`)."""
    statements: list[Object] = [
        Expression(
            [
                Symbol("import"),
                _module_model(module),
                List(
                    [
                        part
                        for name in grouped
                        for part in (Symbol(name), Keyword("as"), alias_symbol(name))
                    ]
                ),
            ]
        )
        for module, grouped in _by_home(names).items()
    ]
    return Expression([Symbol("do"), *statements])


def is_alias_import(module: str, name: str, asname: str | None) -> bool:
    """Whether `from module import name as asname` is one name of an alias import written by import_form / import_source (the home and alias
    match this table's spelling) -- so that the post-processing of the type-checking expansion can tell imports synthesized by macros from the writer's own (static_check.py)."""
    return name in _HOMES and asname == alias(name) and home(name) == module


def import_source(names: Iterable[str]) -> str:
    """The Python import statements that bind names under aliases (`from home import name as alias, ...` per home, one line each)."""
    return "".join(
        f"from {module} import {', '.join(f'{name} as {alias(name)}' for name in grouped)}\n"
        for module, grouped in _by_home(names).items()
    )
