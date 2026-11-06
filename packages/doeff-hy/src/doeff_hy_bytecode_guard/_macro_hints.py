"""For two mistakes in a Hy module that uses doeff-hy macros, warn with the one line to add (initiative doeff-publish).

- **Forgotten require**: if ``(require doeff-hy.macros [do! <-])`` is removed from a README Hy example, Hy compiles ``do!`` as a function call
  and at run time it fails with ``NameError: name 'hyx_doXexclamation_markX' is not defined`` (Hy semantics: an unknown symbol is a function call).
- **Imported instead of required**: ``(import doeff-hy.handle [defhandler])`` looks the macro up as an attribute and fails with ``ImportError: cannot import name
  'defhandler' …`` (a macro lives in the module's macro table, not as a module attribute).

Python raises both, and no doeff code is in the frames it passes through (the display for a failed ``hy file.hy`` comes from the excepthook Hy installs), so
the warning comes before execution: :func:`warn_about_macro_use` inspects the code and record returned by the entry point that loads Hy modules (``get_code`` in :mod:`doeff_hy_bytecode_guard._loader_hooks` — ``hy file.hy``, ``hy -m``,
import, and pytest all pass through it).

How to tell them apart (avoiding false warnings comes first):

- Forgotten require = the record's ``absent`` (names looked up in the module's macro table at expansion time and not found — function calls are included) contains a doeff-hy
  macro name, and the module's code **reads that name without binding it** (LOAD_NAME or LOAD_GLOBAL is present, and no STORE_NAME or STORE_GLOBAL
  is). A function of the same name that the module defines itself with ``(defn check …)`` or imports from another module has a binding, so no warning. A module with ``import *``
  (IMPORT_STAR) gets no warning because it is unknown what that brings in.
- Imported instead of required = the module's top-level code takes a macro name with ``IMPORT_FROM`` from a provider module (such as ``doeff_hy.macros``).
  Importing the provider module itself, or importing a name that is not a macro, is fine (a test guarantees that table names do not overlap the provider's attributes).

Macro names live in one place, ``MACRO_HOMES`` (provider module name -> names that ``(require provider *)`` brings in); agreement with the real macro tables is measured by
packages/doeff-hy/tests/test_macro_hints.py. Macros of packages outside doeff-hy (doeff-cluster, etc. — published by their owning packages) are not included.

This is part of :mod:`doeff_hy_bytecode_guard`, which is loaded at startup of every Python in the venv, but this module is loaded
the first time a Hy module is loaded (not at startup — ``hy`` loads it only when building the name table). For a correct module it finishes with just a comparison of the record's names against co_names and
does not walk the bytecode (even for a module that uses the same spelling as a macro only as a local name).
"""

import dis
import sys
import warnings
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from functools import cache
from types import CodeType

from doeff_hy_bytecode_guard._records import MacroRecord


class HyMacroUseWarning(UserWarning):
    """A Hy module that failed to require a doeff-hy macro (or imported it) — says the one line to add."""


#: Hy module name of each macro provider -> macro names (Hy spelling) that ``(require provider *)`` brings in. The order of providers decides which line to add:
#: the first provider that brings in a name wins. ``defhandler`` is also brought in by doeff-hy.macros, so it is folded into that one line.
MACRO_HOMES: Mapping[str, tuple[str, ...]] = {
    "doeff-hy.macros": (
        "<-",
        "<->",
        "absent-as",
        "check",
        "defeffect",
        "deff",
        "defhandler",
        "defk",
        "defmcp-tool",
        "defp",
        "defpipeline",
        "defpp",
        "deftest",
        "do!",
        "fnk",
        "for/do",
        "handle",
        "lazy",
        "on-raise",
        "session",
        "set!",
        "traverse",
        "val",
        "validate",
        "var",
        "with-handler",
    ),
    "doeff-hy.handle": ("defhandler", "handle", "with-handler"),
    "doeff-hy.record": ("defenum", "defrecord", "defwire"),
}

#: Instructions that read a name (without looking up a binding) and instructions that bind one. Local names (LOAD_FAST etc.) are not module-namespace names, so they are not counted.
_READS = frozenset({"LOAD_NAME", "LOAD_GLOBAL"})
_BINDS = frozenset({"STORE_NAME", "STORE_GLOBAL", "DELETE_NAME", "DELETE_GLOBAL"})


@dataclass(frozen=True)
class MacroName:
    """One macro in the table: Hy spelling, Python name (the form Hy mangles to — it appears in the record and code), and the provider to add a require for."""

    spelling: str
    mangled: str
    home: str


@dataclass(frozen=True)
class MacroTable:
    """Two views of the table re-keyed by Hy mangling: provider Hy name -> (Python name -> macro), and Python name -> macro of the provider to add."""

    by_home: Mapping[str, Mapping[str, MacroName]]
    first_home: Mapping[str, MacroName]


@dataclass(frozen=True)
class Hint:
    """A warning for one module mistake — the line where it was used, and a message containing the one line to add."""

    line: int
    message: str


def macro_table() -> MacroTable | None:
    """Return the table of :data:`MACRO_HOMES` re-keyed by Hy mangling (built only when a Hy module is loaded).

    During ``import hy`` the table cannot be built, so None is returned: Hy loads its own core .hy modules during that time and those loads also go through this
    entry point, but Hy's core has no doeff-hy macros, and ``hy.mangle`` does not exist yet.
    """
    hy = sys.modules.get("hy")
    if hy is None or not hasattr(hy, "mangle"):
        return None
    return _mangled_table(hy.mangle)


@cache
def _mangled_table(mangle: Callable[[str], str]) -> MacroTable:
    """Build, once per process, the table of :data:`MACRO_HOMES` re-keyed with the mangle function."""
    by_home = {
        home: {
            mangle(spelling): MacroName(spelling, mangle(spelling), home) for spelling in spellings
        }
        for home, spellings in MACRO_HOMES.items()
    }
    # If several providers bring in the same name, the earlier provider wins (later ones are layered first).
    first_home = {
        mangled: macro
        for macros in reversed(tuple(by_home.values()))
        for mangled, macro in macros.items()
    }
    return MacroTable(by_home, first_home)


def warn_about_macro_use(code: CodeType, record: MacroRecord, path: str, fullname: str) -> None:
    """Warn with the one line to add if the code of a Hy module returned by the loader entry point has a mistake (do nothing for a module without mistakes).

    ``record`` is the record attached to the code (it holds the names that the expansion looked up and did not find) — narrowing the candidates is only a comparison of the record's names, and for a correct module
    the bytecode is not walked. The warning is issued every time the module is loaded (also when loaded from .pyc — it keeps saying so until fixed).
    """
    for hint in _hints(code, record):
        warnings.warn_explicit(hint.message, HyMacroUseWarning, path, hint.line, module=fullname)


def _hints(code: CodeType, record: MacroRecord) -> Iterator[Hint]:
    """Yield a warning for each mistake in the code (forgotten require, imported instead). Do not walk the bytecode if there are no candidates."""
    table = macro_table()
    if table is None:
        return
    candidates = frozenset(name for name in record.absent if name in table.first_home)
    if candidates:
        yield from _forgotten_requires(code, candidates, table)
    # Walk instructions only for a module whose top-level code has the provider module's name and one of its macro names in its names (walking all instructions takes
    # ten-odd ms for a large module — comparing names takes µs).
    names = frozenset(code.co_names)
    importable = {
        _python_module(home): macros
        for home, macros in table.by_home.items()
        if _python_module(home) in names and not names.isdisjoint(macros)
    }
    if importable:
        yield from _imported_macros(code, importable)


def _python_module(home: str) -> str:
    """Return the Python module name (``doeff_hy.macros``) corresponding to the provider's Hy module name (``doeff-hy.macros``)."""
    return home.replace("-", "_")


def _listed(spellings: Iterable[str]) -> str:
    """Turn a list of macro spellings into an English list inside the warning message (`a`; `a` and `b`; `a`, `b` and `c`)."""
    quoted = [f"`{spelling}`" for spelling in spellings]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} and {quoted[-1]}"


def _units(code: CodeType) -> Iterator[CodeType]:
    """Walk the module's code and all the function and class code inside it (including nested ones)."""
    yield code
    for const in code.co_consts:
        if isinstance(const, CodeType):
            yield from _units(const)


def _line(instruction: dis.Instruction) -> int:
    """Return the instruction's line (line 1 for an instruction without a position)."""
    positions = instruction.positions
    line = None if positions is None else positions.lineno
    return 1 if line is None else line


def _forgotten_requires(
    code: CodeType, candidates: frozenset[str], table: MacroTable
) -> Iterator[Hint]:
    """Turn the candidate names that the module's code reads without binding into warnings, each with a require line (per provider)."""
    units = tuple(_units(code))
    # Both read names (LOAD_NAME, LOAD_GLOBAL) and bound names (STORE_NAME, STORE_GLOBAL, etc.) come from co_names, so if the candidates are not in the
    # union of co_names there is no warning — a module that uses the same spelling as a local name (``(check row)`` is LOAD_FAST) does not walk the instructions
    # (about 1 second each for a large module — dis is slow on free-threaded builds).
    if candidates.isdisjoint(name for unit in units for name in unit.co_names):
        return
    instructions = tuple(ins for unit in units for ins in dis.get_instructions(unit))
    if any(ins.opname == "IMPORT_STAR" for ins in instructions):
        return  # for a module where it is unknown what gets brought in, we cannot say there is no binding
    bound = frozenset(ins.argval for ins in instructions if ins.opname in _BINDS)
    reads = [
        (_line(ins), ins.argval)
        for ins in instructions
        if ins.opname in _READS and ins.argval in candidates and ins.argval not in bound
    ]
    if not reads:
        return
    first_line = {name: min(line for line, other in reads if other == name) for _, name in reads}
    macros = [table.first_home[name] for name in sorted(first_line, key=first_line.__getitem__)]
    lines = [
        f"(require {home} [{' '.join(macro.spelling for macro in macros if macro.home == home)}])"
        for home in MACRO_HOMES
        if any(macro.home == home for macro in macros)
    ]
    verb = "is a doeff-hy macro" if len(macros) == 1 else "are doeff-hy macros"
    yield Hint(
        min(first_line.values()),
        f"Add {' and '.join(lines)} to this module: {_listed(macro.spelling for macro in macros)} "
        f"{verb} used here without a require, so Hy compiled the use as a call to an undefined "
        f"name (at run time: NameError: name {macros[0].mangled!r} is not defined).",
    )


@dataclass(frozen=True)
class _ImportedMacro:
    """One ``from provider import macro``: the provider's Python name, the line where it was used, and the macro."""

    source: str
    line: int
    macro: MacroName


def _macro_imports(
    code: CodeType, importable: Mapping[str, Mapping[str, MacroName]]
) -> Iterator[_ImportedMacro]:
    """Yield those names in the module's top-level ``from provider import name`` that are macros of the provider."""
    source = ""
    for instruction in dis.get_instructions(code):
        if instruction.opname == "IMPORT_NAME":
            source = instruction.argval
        elif instruction.opname == "IMPORT_FROM":
            macro = importable.get(source, {}).get(instruction.argval)
            if macro is not None:
                yield _ImportedMacro(source, _line(instruction), macro)


def _imported_macros(
    code: CodeType, importable: Mapping[str, Mapping[str, MacroName]]
) -> Iterator[Hint]:
    """Turn the mistake of importing a macro into a warning with a require line, per provider."""
    imports = tuple(_macro_imports(code, importable))
    for source in dict.fromkeys(item.source for item in imports):
        mine = [item for item in imports if item.source == source]
        home = mine[0].macro.home
        names = " ".join(item.macro.spelling for item in mine)
        yield Hint(
            min(item.line for item in mine),
            f"Require, do not import: (require {home} [{names}]). "
            f"{_listed(item.macro.spelling for item in mine)} "
            f"{'is a macro' if len(mine) == 1 else 'are macros'} of {source}, and import cannot "
            f"bring in a macro (at run time: ImportError: cannot import name "
            f"{mine[0].macro.spelling!r} from {source!r}).",
        )
