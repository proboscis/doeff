"""Adding a name outside effects and handlers to a subpackage's public API turns the check red, and today's violations sit in a known table that can only shrink.

Rule: the public API a doeff subpackage shows its users is effects and handlers only (initiative doeff-subpackages-effects-only).
Public API is counted in exactly one place, tools/public_api_inventory.py; this file does not count. Among the tool's inventory rows,
those whose kind is neither effect nor handler (other, unclassified) are violations, and they are matched against two tables, the known table
tests/data/public_api_known_violations.json (the known-violations table — violations and boundaries only) and the allowed table tests/data/public_api_allowed.json (names the rule allows name by name),
by the key (module that shows the name, name). A submodule whose name does not start with _ also counts as one inventory row (the module that shows the name is
(submodule), the name is the submodule's import name) and as a violation (ADR-DOE-API-001 R5 — public names come out of the import root only):

1. One check per package (looked up by the dir name under packages/): a violation in neither table is red (a newly added violation or a public
   submodule), and a name that is in a table but not in the inventory is red (fixed but the table was not shrunk — for either of the two tables).
2. A row whose name moved from a submodule to the entry point is read as the same row without rewriting the table's module: a root (root, name) that is not in the table, when the table has the same name as
   (a submodule under the root, name), is read as that table row by definition place only if that submodule still shows the name, and by name and root alone if it no longer shows it
   (the old door is closed — there is no definition to compare against). Adding a different definition to the root stays red.
3. Table shape: every package of the inventory (the dirs under packages/ other than the parts inside doeff) is listed exactly once, with a scope of a defined value, and names are
   sorted and not duplicated within a group. The violations table has no allowed groups (allowed things live only in the allowed table, so the violations table can be empty).
4. The violations table only shrinks: a name in a table "names" group must be a name present in the version at the commit that added this table (the first version in git history). A submodule
   row must be one present in the version at the commit that added the submodule rows to the table. A violation that can only be added goes in "added names", with a reason written
   one line each.
5. Failure cases: build a throwaway package and confirm with the same matching that effects and handlers alone are green, adding one violation is red, removing it is green, fixing it but leaving it in the table is
   red, writing it in "added names" with a reason is green, adding a submodule without _ is red, moving a name from a submodule to the entry point is green
   (both while the forwarding module remains and after it is removed), adding a different definition of the same name to the entry point is red, and a forwarding module whose first line has the marker moved-submodule-shim
   counts the modules under the destination package by their old names too and is green.
   A forwarding module that has a fixed list of names under the marker stays green, adding no inventory rows, when a helper is added to a destination _ module; without the list
   it is red, and adding a name to the entry point is red even with the list. It also confirms that every forwarding module has the list.
   A name read as a function that returns a handler by its return type (a factory) is red if calling it returns a value without the marker _doeff_is_handler_fn, and green once it
   returns a handler (the inventory calls factories that can be called without arguments and checks the marker — ADR-DOE-API-001 R3).
6. The allowed table can be added to with a basis (it does not only shrink): one row = (package, module, name), a group, and a basis. The basis is the ADR rule that allows the row
   (ADR-DOE-API-001 R<number>), or, for a row with no rule, one English sentence giving the reason it is allowed. A row without a basis, a basis containing a record id or an issue number,
   and a row with the same key as the violations table are red. Only groups whose shape the inventory checks (ALLOWED_GROUPS) can be used. In the group "Allowed — program helper"
   (ADR-DOE-API-001 R18 — functions the rule allows name by name that only emit effects and return a program), only names whose inventory shape is a program helper can be placed:
   placing a function that installs a handler, answers an effect, or does not return a program is red. In the group "Allowed — Hy macro"
   (R14, R15, R31 — a module that shows macros sits at a public boundary), only rows whose inventory shape is a Hy macro, and rows of public submodules that show only Hy
   macros (macro modules), can be placed: placing a non-macro name or a submodule that also shows names outside macros is red.
   In the group "Allowed — CLI command" (R7, R31), only console script and Rust executable rows whose name an ADR rule mentions can be placed
   (ADR_COMMANDS — it also checks that the rule text mentions the name): placing a command not in an ADR is red. In the group "Allowed — pytest plugin
   registration" (R10, R33), only pytest11 entry point rows and names shown by the module that entry point loads can be placed, and in the group "Allowed —
   startup entry" (R36), only names that a .pth shipped by the package calls at every startup can be placed. In the group "Allowed — conversion
   function" (R35 — functions that convert an effect's answer to another scale), only names whose inventory shape is a conversion function can be placed: a function that creates an effect, handler, or
   program inside, reads a clock, the environment, or a file, or is a generator function is red. In the group "Allowed — exception type" (R2 — exception types that effects and handlers
   throw), only names whose inventory shape is an exception class (a child of BaseException) defined by that package can be placed: a class that is not an exception, a function, or a name that re-exports an exception of another package or the standard library is red. In the group "Allowed — effect value type" (R2), only
   classes and type aliases that appear in the annotations of the fields of effect classes shown by the same package (including type arguments, following the fields of value types the package defines — the inventory's value_types) can be placed:
   placing a class that appears in no effect's fields is red. In the group "Allowed — effect answer type" (R2), only the package's
   effect answer types can be placed (the T of EffectBase[T] or the type in a defeffect :answer, a bus event that an effect waiting on a body-named type declared in its own __doeff_bus_events__, and
   types in the fields of an answer class, child classes, and type aliases that list only answer classes — the inventory's answer_types): an answer written only in a docstring
   and a class that appears in no effect's answer are red. In the group "Allowed — program-shape builder and type" (R37 — agent!, loop and others that
   write a doeff-conductor workflow, and AgentSpec and others), only builders (functions of the conversion shape that return a value of a class of the same package) whose returned class is a name the package shows,
   and classes that the group's builders return, can be placed: a function that emits or creates an effect, reads I/O, returns a class that is not shown, or returns outside value types, and a class that no builder of the group returns, are red. In the group "Allowed — handler setting type" (R2), only
   types defined by that package can be placed, and only classes and type aliases that appear in the annotations of the arguments of handlers, handler classes, and factories shown by the same package (including type arguments, following the fields of
   setting types the package defines [including Protocol properties] — the inventory's settings): a class that appears in no argument, a function, or a name that re-exports a standard library type is red.
7. A package in scope of the rule that has a Rust extension (maturin) is red if the inventory cannot be read by import — a literal reading does not count the names the entry point shows from the extension
   (it had not counted Indexer and SymbolInfo of doeff-indexer), so install it into the environment and read it.
8. A part inside doeff (the inventory tool's DOEFF_PARTS — doeff-vm) is not counted by the inventory, like the doeff root package, and is not listed in the tables
   (ADR-DOE-API-001 R11). As its premise, a file outside doeff (a package under packages/ that is not a part, examples, docs/adr)
   that imports a part's import root is red (naming the file, line, and module). Failure cases: in a throwaway repo, a plain import, from, a lower module, a Hy import and require,
   and a name string passed to importlib and pytest.importorskip are red, while the forms that read from the doeff root and doeff._vm, and the imports of the part itself, the doeff root package, and
   doeff's checks (tests/), are green.
9. Do not place under the root a child with the same name as a name the entry point (the import root's __init__) shows (including forwarding modules) (ADR-DOE-API-001 R34
   — an old-path import bound the parent's attribute to a module, hid the entry point's function, and the records service fell over at every startup). Checked two ways per package: a literal check (the names the entry file binds against the child names), and
   a check that, in a fresh process, after importing the root and every public module (including old paths) one by one, the entry point's names do not turn into modules. Failure case: where the entry point of a throwaway package
   shows the function serve (lazy) and tool (immediate), placing a forwarding module of the same name makes both red, and removing the shell makes them green.
   The same is applied to the entry points of packages under the root (including _ packages) (the old path doeff_agents.effects.agent of a moved package imported
   and overwrote the function agent of doeff_agents._effects with a module). Failure case: the entry point of the package _inner under the root binds the function tool from a child of the same name, tool, and the shell answers the old path inner of the moved package, making both red;
   moving the child to _tool makes them green.
"""

from __future__ import annotations

import ast
import collections
import importlib.util
import json
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, NotRequired, TypedDict, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
TABLE = ROOT / "tests" / "data" / "public_api_known_violations.json"
ALLOWED = ROOT / "tests" / "data" / "public_api_allowed.json"
#: The inventory kinds that are outside the rule (outside effects and handlers).
OUTSIDE_KINDS = frozenset({"other", "unclassified"})
#: Scope of a package. Names of a package out of scope are not matched (it needs a basis).
SCOPES = ("in scope", "awaiting scope answer", "out of scope")
#: Head of the group names in the violations table (the division — direction of the fix or kind of boundary). Allowed things go in the allowed table.
GROUP_HEADS = ("Violation — ", "Boundary — ", "Unsorted — ")
#: Head of the group names of allowed things (the "group" field of the allowed table).
ALLOWED_HEAD = "Allowed — "
#: Group of program helpers the rule allows name by name (ADR-DOE-API-001 R18) — only names whose inventory shape is a program helper can be placed.
HELPER_GROUP = "Allowed — program helper"
#: Group of Hy macros the rule allows (ADR-DOE-API-001 R14, R15, R31) — only macro names and rows of macro modules can be placed.
MACRO_GROUP = "Allowed — Hy macro"
#: Group of CLI commands the rule allows (ADR-DOE-API-001 R7, R31) — only console script and Rust executable rows can be placed.
COMMAND_GROUP = "Allowed — CLI command"
#: Group of conversion functions the rule allows name by name (ADR-DOE-API-001 R35) — only names whose inventory shape is a conversion function can be placed.
CONVERSION_GROUP = "Allowed — conversion function"
#: Group of ADR-writing functions the rule allows (ADR-DOE-API-001 R14 — fact, counterexample, interpretation that docs/adr files import and call).
ADR_WRITER_GROUP = "Allowed — ADR-writing function"
#: Group of effect answer types the rule allows (ADR-DOE-API-001 R2 — result types of effects that callers tell apart with match).
ANSWER_TYPE_GROUP = "Allowed — effect answer type"
#: Group of exception types the rule allows (ADR-DOE-API-001 R2 — exception classes that effects and handlers throw and callers catch).
EXCEPTION_GROUP = "Allowed — exception type"
#: Group of effect value types the rule allows (ADR-DOE-API-001 R2) — only types in the fields of effect classes shown by the same package can be placed.
VALUE_TYPE_GROUP = "Allowed — effect value type"
#: Group of pytest plugin registrations the rule allows (ADR-DOE-API-001 R10, R33) — only pytest11 entry point rows and the names shown by the module that entry point
#: loads (the plugin module) (declarations written by users' checks) can be placed.
PLUGIN_GROUP = "Allowed — pytest plugin registration"
#: Group of startup entries the rule allows (ADR-DOE-API-001 R36) — only names that a .pth shipped by the package calls at every startup of the venv's Python can be placed.
STARTUP_GROUP = "Allowed — startup entry"
#: Group of builders the rule allows that assemble values representing the shape of a program, and the types of the values they return (ADR-DOE-API-001 R37 — the
#: workflow vocabulary of doeff-conductor). Only builder names whose inventory shape is a builder, and classes that the group's builders return, can be placed.
BUILDER_GROUP = "Allowed — program-shape builder and type"
#: Group of handler setting types the rule allows (ADR-DOE-API-001 R2) — only types of the arguments of handlers and factories shown by the same package can be placed.
SETTING_GROUP = "Allowed — handler setting type"
#: Shape of the inventory row for a pytest11 entry point (_metadata_rows in tools/public_api_inventory.py).
PYTEST_PLUGIN_FORM = "entry point(pytest11)"
#: What each allowed group can hold (the shape the inventory checks — used in the sentence that names a name whose shape does not fit). A group with no check cannot be used.
ALLOWED_SHAPES: dict[str, str] = {
    HELPER_GROUP: "Only functions that return a program and, inside, neither install a handler nor answer an effect can be placed (ADR-DOE-API-001 R18)",
    MACRO_GROUP: "Only Hy macros and public submodules that show nothing but Hy macros (macro modules) can be placed"
    "(ADR-DOE-API-001 R14, R15, R31)",
    COMMAND_GROUP: "Only console scripts and Rust executables that an ADR-DOE-API-001 rule names and decides to keep can be placed"
    "(ADR_COMMANDS — R7, R23, R24, R27, R28, R31, R33)",
    CONVERSION_GROUP: "Only functions that take a value and return a value, create no effect, handler, or program inside, and do not read the clock, environment, files, processes, or"
    " randomness can be placed (ADR-DOE-API-001 R35)",
    ADR_WRITER_GROUP: "Only functions (plain or conversion functions) shown by a Hy macro module can be placed (an ADR imports and calls them in a defadr body — ADR-DOE-API-001 R14)",
    ANSWER_TYPE_GROUP: "Only classes and type aliases that the inventory reads as a package's effect answer type can be placed (the T of EffectBase[T] or the type in a defeffect"
    " :answer, a bus event that an effect waiting on a body-named type declared in __doeff_bus_events__, and types in the "
    "fields of an answer class and child classes — effect answer types — ADR-DOE-API-001 R2). The answer of an effect whose answer is written only in a docstring is not counted — "
    "write the answer type on the effect",
    EXCEPTION_GROUP: "Only classes that are children of BaseException defined by that package can be placed (exception types that effects and handlers throw — "
    "ADR-DOE-API-001 R2). A name that re-exports an exception of another package or the standard library cannot be placed",
    VALUE_TYPE_GROUP: "Only classes and type aliases that appear in the annotations of the fields of effect classes shown by the same package (including type arguments, following the fields of value types the package defines)"
    " can be placed (effect value types — ADR-DOE-API-001 R2)",
    PLUGIN_GROUP: "Only pytest11 entry point rows and names shown by the module that entry point loads (the plugin module) can be placed"
    "(ADR-DOE-API-001 R10, R33)",
    STARTUP_GROUP: "Only names that a .pth shipped by the package calls at every startup of the venv's Python can be placed (ADR-DOE-API-001 R36)",
    BUILDER_GROUP: "Only functions that return a value of a class shown by that package and, inside, create no effect, handler, or program and do not read the clock, environment, files,"
    " processes, or randomness (builders), and the classes that the group's builders return, can be placed (builders of values representing the shape of a program, and their types — "
    "ADR-DOE-API-001 R37)",
    SETTING_GROUP: "Only classes and type aliases defined by that package that appear in the annotations of the arguments of handlers, handler classes, and factories shown by the same"
    " package (including type arguments, following the fields of setting types the package defines) can be placed (handler setting types — "
    "ADR-DOE-API-001 R2). A type that appears in no argument, and a name that re-exports a standard library type or a type of another package, cannot be placed",
}
#: CLI commands that an ADR-DOE-API-001 rule names and decides to keep (from the package dir, from the name of a console script or Rust executable to
#: the rule that names it). The group "Allowed — CLI command" can hold only the names here, and
#: test_every_command_the_guard_allows_is_named_by_its_rule checks that the rule text names each — adding a command not in an ADR is red.
ADR_COMMANDS: dict[str, dict[str, str]] = {
    "doeff-adr": {"doeff-adr": "R33"},
    "doeff-agentic": {"doeff-agentic": "R28", "doeff-agentic-tui": "R7"},
    "doeff-agents": {"doeff-agents": "R7"},
    "doeff-conductor": {"conductor": "R7"},
    "doeff-effect-analyzer": {"doeff-effects": "R24"},
    "doeff-flow": {"doeff-flow": "R7"},
    "doeff-hy": {"doeff-hy-check": "R31"},
    "doeff-indexer": {"doeff-indexer": "R27"},
    "doeff-linter": {"doeff-linter": "R23"},
}
ADR_FILE = ROOT / "docs" / "adr" / "defadr_doeff_api_001_subpackages_show_effects_and_handlers.hy"
#: All the allowed groups (the inventory checks the shape per group — fits_allowed_group).
ALLOWED_GROUPS = tuple(ALLOWED_SHAPES)
#: First form of a basis: the ADR rule that allows the row.
DECISION_RULE = re.compile(r"ADR-DOE-API-001 R[0-9]+")
#: Marks not to write in a basis: record ids (names of cards, requests, messages, conversations, and contacts) and issue numbers.
PRIVATE_MARK = re.compile(
    r"(?<![A-Za-z0-9-])(?:(?:ki|ms)-[0-9a-f]{12}|(?:lt|ci|c|m|idea)-[0-9A-HJKMNP-TV-Z]{26})(?![A-Za-z0-9])|#[0-9]+"
)
#: Basis of a throwaway table.
FIXTURE_DECISION = "ADR-DOE-API-001 R18"
#: Upper bound in seconds for reading one package in a child process (the heaviest, doeff-cluster, takes 9 seconds under a load of about 55). The root conftest multiplies
#: only pytest's limit (the timeout marker) by the load factor, so checks that read a real package also multiply the seconds passed to the child by deadline_scale_factor —
#: without it, a run with a cold Hy compile cache (doeff-cluster 38 seconds; 4 seconds once warm) exceeded 100 seconds under a load factor of 5.4.
_PROBE_SECONDS = 100

Key = tuple[str, str]


class AddedName(TypedDict):
    """One "added names" row: a violation that could only be added after the table was added (with a reason).

    Allowed things (a newly shown program helper, for example) are not written here; they are added to the allowed table with a basis.
    """

    package: str
    module: str
    name: str
    reason: str


#: Fields of one "added names" row.
ADDED_FIELDS = frozenset({"package", "module", "name", "reason"})


class PackageEntry(TypedDict):
    """One package's entry: the scope, and the group name (the division — direction of the fix or kind of boundary) to module to list of names. A package out of scope has a basis."""

    scope: str
    names: dict[str, dict[str, list[str]]]
    basis: NotRequired[str]


class KnownTable(TypedDict):
    """The whole known table (the violations table) (the description is a field for human readers)."""

    description: NotRequired[list[str]]
    packages: dict[str, PackageEntry]
    added_names: list[AddedName]


class AllowedName(TypedDict):
    """One row of the allowed table: a name the rule allows name by name (group = the allowed group whose shape the inventory checks; basis = the ADR rule that allows the row, or one sentence giving the reason)."""

    package: str
    module: str
    name: str
    group: str
    basis: str


#: Fields of one row of the allowed table.
ALLOWED_FIELDS = frozenset({"package", "module", "name", "group", "basis"})


class AllowedTable(TypedDict):
    """The whole allowed table (the description is a field for human readers). Rows are sorted by (package, module, name) and not duplicated."""

    description: NotRequired[list[str]]
    allowed: list[AllowedName]


#: An allowed table that allows nothing (the default for matching throwaway packages).
NOTHING_ALLOWED: AllowedTable = {"allowed": []}


def _load() -> ModuleType:
    """Load the inventory tool in tools/ (tools/ is not a package — share one with the tool's own check under the same name)."""
    loaded = sys.modules.get("public_api_inventory")
    if loaded is not None:
        return loaded
    spec = importlib.util.spec_from_file_location(
        "public_api_inventory", ROOT / "tools" / "public_api_inventory.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["public_api_inventory"] = module
    spec.loader.exec_module(module)
    return module


tool = _load()
#: The module column of a public submodule's row (the name is the submodule's import name).
SUBMODULE: str = tool.SUBMODULE


def _read_table(path: Path = TABLE) -> KnownTable:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_allowed(path: Path = ALLOWED) -> AllowedTable:
    return json.loads(path.read_text(encoding="utf-8"))


def listed_names(table: KnownTable) -> set[tuple[str, str, str]]:
    """Every (package dir, module, name) in the "names" groups of the table."""
    return {
        (package_dir, module, name)
        for package_dir, entry in table["packages"].items()
        for modules in entry["names"].values()
        for module, names in modules.items()
        for name in names
    }


def known_names(table: KnownTable, package_dir: str) -> set[Key]:
    """One package's known violations: the names in the "names" groups and the names in "added names"."""
    listed = {
        (module, name)
        for modules in table["packages"][package_dir]["names"].values()
        for module, names in modules.items()
        for name in names
    }
    added = {
        (row["module"], row["name"]) for row in table["added_names"] if row["package"] == package_dir
    }
    return listed | added


def allowed_names(allowed: AllowedTable, package_dir: str) -> set[Key]:
    """Every (module, name) one package placed in the allowed table."""
    return {
        (row["module"], row["name"]) for row in allowed["allowed"] if row["package"] == package_dir
    }


def allowed_groups(allowed: AllowedTable, package_dir: str) -> dict[Key, str]:
    """From (module, name) to group for one package's rows in the allowed table (only rows of groups whose shape the inventory checks)."""
    return {
        (row["module"], row["name"]): row["group"]
        for row in allowed["allowed"]
        if row["package"] == package_dir and row["group"] in ALLOWED_SHAPES
    }


def _is_macro_module(package: Mapping[str, Any], module: str, writers: frozenset[Key] = frozenset()) -> bool:
    """Whether the module has at least one name in the inventory and every one is a Hy macro or a name placed in the allowed table as an ADR-writing function (a macro module
    — ADR-DOE-API-001 R14, R15, R31)."""
    rows = [row for row in package["exports"] if row["module"] == module]
    return any(row["form"] == tool.MACRO_FORM for row in rows) and all(
        row["form"] == tool.MACRO_FORM or (module, row["name"]) in writers for row in rows
    )


def defined_by(row: Mapping[str, Any], package: Mapping[str, Any]) -> bool:
    """Whether the package defined the inventory row's name: the definition place is a file under the package's dir, or a module under the package's import root
    (throwaway packages outside the repo and Rust extensions are written by module name — _place_text in tools/public_api_inventory.py)."""
    place = row["defined_at"]
    return place.startswith(f"packages/{package['dir']}/") or (
        place.partition("(")[0].split(".")[0] in package["import_roots"]
    )


@dataclass(frozen=True)
class GroupFacts:
    """Facts outside the inventory rows that checking a group's shape needs (for one package)."""

    #: (module, name) placed in the group "ADR-writing function" in the allowed table.
    writers: frozenset[Key] = frozenset()
    #: Names of CLI commands that an ADR-DOE-API-001 rule names and decides to keep (the part of ADR_COMMANDS for that package).
    commands: frozenset[str] = frozenset()
    #: Modules that pytest11 entry points load (plugin_modules in tools/public_api_inventory.py).
    plugin_modules: frozenset[str] = frozenset()
    #: (module, name) that the .pth shipped by the package calls at every startup (startup_calls in tools/public_api_inventory.py).
    startup_calls: frozenset[Key] = frozenset()
    #: Classes returned by builders placed in the group "program-shape builder and type" in the allowed table (the shape of the inventory tool's built_class).
    built: frozenset[str] = frozenset()


#: Group check with no facts outside the inventory rows (the default).
NO_FACTS = GroupFacts()


def _is_plain_class(row: Mapping[str, Any]) -> bool:
    """Whether the inventory row is a plain class that is neither an effect, a handler, nor an exception (a type a builder can return a value of)."""
    return row["kind"] == "other" and row["form"] == "class"


def shown_classes(package: Mapping[str, Any]) -> frozenset[str]:
    """All the plain classes the package shows (the shape of the inventory tool's built_class — name and definition place)."""
    return frozenset(
        tool.built_class(row["name"], row["defined_at"])
        for row in package["exports"]
        if _is_plain_class(row)
    )


def fits_builder_group(
    row: Mapping[str, Any], package: Mapping[str, Any], built: frozenset[str]
) -> bool:
    """Whether one inventory row fits the shape of the group "program-shape builder and type" (ADR-DOE-API-001 R37): the shape is a builder and the package
    shows the class it returns, or it is a plain class that one of the builders placed in the group returns (built)."""
    if row["form"] == tool.BUILDER:
        return row.get("returns") in shown_classes(package)
    return _is_plain_class(row) and tool.built_class(row["name"], row["defined_at"]) in built


def fits_allowed_group(
    group: str,
    row: Mapping[str, Any],
    package: Mapping[str, Any],
    facts: GroupFacts = NO_FACTS,
) -> bool:
    """Whether one inventory row fits the shape of an allowed group (the shape per group is the sentence in ALLOWED_SHAPES). facts = facts outside the inventory rows (GroupFacts)."""
    writers = facts.writers
    shapes: dict[str, Callable[[], bool]] = {
        HELPER_GROUP: lambda: row["form"] == tool.PROGRAM_HELPER,
        CONVERSION_GROUP: lambda: row["form"] == tool.CONVERSION,
        MACRO_GROUP: lambda: (
            row["form"] == tool.MACRO_FORM
            or (row["module"] == SUBMODULE and _is_macro_module(package, row["name"], writers))
        ),
        ADR_WRITER_GROUP: lambda: (
            row["form"] in (tool.PLAIN_FUNCTION, tool.CONVERSION) and row["module"].endswith(".macros")
        ),
        ANSWER_TYPE_GROUP: lambda: any(
            (shown["module"], shown["name"]) == (row["module"], row["name"])
            for shown in package["answer_types"]
        ),
        EXCEPTION_GROUP: lambda: row["form"] == tool.EXCEPTION_CLASS and defined_by(row, package),
        VALUE_TYPE_GROUP: lambda: any(
            (shown["module"], shown["name"]) == (row["module"], row["name"])
            for shown in package["value_types"]
        ),
        COMMAND_GROUP: lambda: (
            row["form"] in (tool.CONSOLE_SCRIPT, tool.RUST_EXECUTABLE)
            and row["name"] in facts.commands
        ),
        PLUGIN_GROUP: lambda: (
            row["form"] == PYTEST_PLUGIN_FORM or row["module"] in facts.plugin_modules
        ),
        STARTUP_GROUP: lambda: (row["module"], row["name"]) in facts.startup_calls,
        BUILDER_GROUP: lambda: fits_builder_group(row, package, facts.built),
        SETTING_GROUP: lambda: defined_by(row, package)
        and any(
            (shown["module"], shown["name"]) == (row["module"], row["name"])
            for shown in package["settings"]
        ),
    }
    fits = shapes.get(group)
    return fits is not None and fits()


def outside_rows(package: Mapping[str, Any]) -> dict[Key, Mapping[str, Any]]:
    """The rows of one inventory package that are outside effects and handlers (from (module, name) to row)."""
    return {
        (row["module"], row["name"]): row
        for row in package["exports"]
        if row["kind"] in OUTSIDE_KINDS
    }


def moved_to_root(
    roots: Iterable[str], found: Mapping[Key, Mapping[str, Any]], known: set[Key]
) -> set[tuple[Key, Key]]:
    """Rows whose name moved from a submodule to the entry point: pairs of an inventory (root, name) not in the table and the table's (submodule under the root, name) read as the same row.

    If the submodule still shows the same name (while the forwarding module keeps the old door open), they are read as the same row only when the definition place is the same.
    If it no longer shows it (after the old door is closed), there is no definition to compare, so they are read by name and root alone.
    """
    return {
        ((root, name), (module, name))
        for root, name in set(found) - known
        if root in roots
        for module, known_name in known
        if known_name == name
        and module.startswith(f"{root}.")
        and (
            (module, name) not in found
            or found[(module, name)]["defined_at"] == found[(root, name)]["defined_at"]
        )
    }


def _key_text(module: str, name: str) -> str:
    """One table key in a human-readable form (a public submodule's row is just the submodule's name)."""
    return f"{name}(public submodule)" if module == SUBMODULE else f"{module}.{name}"


def _new_text(directory: str, new: list[Key], found: Mapping[Key, Mapping[str, Any]]) -> str:
    rows = [found[key] for key in new]
    return (
        f"{directory}: {len(new)} public name(s) outside effects and handlers are in neither the known table nor the allowed table — make them effects or handlers,"
        " start the name with _ or move it to a module that starts with _ (hide a public submodule by prefixing its name with _, and show names from the entry point). "
        f"If the rule allows the name, add it to the allowed table {ALLOWED.relative_to(ROOT)} with a group and a basis; if it is a violation that must be shown, "
        "write it in \"added names\" of the known table with a reason:\n"
        + "\n".join(
            f"  {_key_text(row['module'], row['name'])}({row['form']}・{row['kind']}・{row['defined_at']})"
            for row in rows
        )
    )


def _stale_text(directory: str, stale: list[Key]) -> str:
    return (
        f"{directory}: {len(stale)} name(s) are in the known table but not in the inventory — remove fixed names and hidden submodules from the table"
        " (the table only shrinks):\n" + "\n".join(f"  {_key_text(module, name)}" for module, name in stale)
    )


def _stale_allowed_text(directory: str, stale: list[Key]) -> str:
    return (
        f"{directory}: {len(stale)} name(s) are in the allowed table but not in the inventory — remove deleted and hidden names from the allowed table:\n"
        + "\n".join(f"  {_key_text(module, name)}" for module, name in stale)
    )


def _misshapen_text(directory: str, group: str, rows: list[Mapping[str, Any]]) -> str:
    return (
        f"{directory}: {len(rows)} name(s) in the group \"{group}\" of the allowed table have an inventory shape that does not fit the group — "
        f"{ALLOWED_SHAPES[group]}. Fix them, or take them out of the allowed table and return them to a violation or boundary group of the known table:\n"
        + "\n".join(
            f"  {_key_text(row['module'], row['name'])}({row['form']}・{row['reason']}・{row['defined_at']})"
            for row in rows
        )
    )


def unread_native_modules(
    package: Mapping[str, Any], entry: PackageEntry, repo: Path
) -> list[Mapping[str, Any]]:
    """Modules of a package in scope of the rule that has a Rust extension, which the inventory could not import and read literally.

    A literal reading counts only names defined in source, so it does not count the names the entry point shows from the extension (formerly Indexer and SymbolInfo of doeff-indexer).
    Every name in scope is counted, so such a package is installed into the environment and read by import.
    """
    if (
        entry["scope"] != "in scope"
        or not tool.read_layout(repo / "packages" / package["dir"]).maturin
    ):
        return []
    return [module for module in package["modules"] if module["read"] == "text"]


def _unread_native_text(directory: str, modules: list[Mapping[str, Any]]) -> str:
    return (
        f"{directory}: the inventory could not import the modules of a package with a Rust extension and read them literally — a literal reading cannot count the names the entry point shows from"
        " the extension. Put the package in the dev group of the root pyproject and read it in the make sync environment:\n"
        + "\n".join(f"  {module['name']}: {module['error']}" for module in modules)
    )


def _diff_against_tables(
    package: Mapping[str, Any], table: KnownTable, allowed: AllowedTable
) -> tuple[dict[Key, Mapping[str, Any]], set[Key], set[Key], set[tuple[Key, Key]], list[Key]]:
    """Return one inventory package's violations, the names in the two tables, the moved rows, and the new violations in neither table (the one point that check_package and
    tools/public_api_blame.py share for the same matching)."""
    directory = package["dir"]
    found = outside_rows(package)
    known = known_names(table, directory)
    permitted = allowed_names(allowed, directory)
    moved = moved_to_root(package["import_roots"], found, known | permitted)
    new = sorted(set(found) - known - permitted - {root for root, _old in moved})
    return found, known, permitted, moved, new


def unlisted_names(package: Mapping[str, Any], table: KnownTable, allowed: AllowedTable) -> list[Key]:
    """Violations added without being written in a table (a list of (module, name)) — read by tools/public_api_blame.py, the later stage of the daily full verification, to look up the commit that added them."""
    entry = table["packages"].get(package["dir"])
    if package["error"] is not None or entry is None or entry["scope"] == "out of scope":
        return []
    return _diff_against_tables(package, table, allowed)[4]


def check_package(
    package: Mapping[str, Any],
    table: KnownTable,
    allowed: AllowedTable = NOTHING_ALLOWED,
    repo: Path = ROOT,
    commands: Mapping[str, Mapping[str, str]] = ADR_COMMANDS,
) -> None:
    """Match one inventory package against the known table (the violations table) and the allowed table. Stop naming the violations in neither table and the names that are in a table but not
    in the inventory (per table).

    A row whose name moved from a submodule to the entry point (moved_to_root) is read as the same row as the table's submodule row. A name in the allowed table stops with its group named if its inventory shape
    does not fit the placed group's shape (fits_allowed_group — a row moved to the entry point is viewed by the entry point row's shape). The CLI command
    group allows only names in commands (the commands an ADR rule names — default ADR_COMMANDS), and the plugin registration and startup entry groups fit only what the package's pyproject.toml
    under repo places.
    A package in scope of the rule that has a Rust extension stops if the inventory cannot be read by import (unread_native_modules).
    """
    directory = package["dir"]
    assert package["error"] is None, f"could not read {directory}: {package['error']}"
    entry = table["packages"].get(directory)
    assert entry is not None, (
        f"{directory} is not in the known table {TABLE.relative_to(ROOT)} — when adding a package, add its scope and name groups to the table"
    )
    if entry["scope"] == "out of scope":
        return
    found, known, permitted, moved, new = _diff_against_tables(package, table, allowed)
    olds = {old for _root, old in moved}
    stale = sorted(known - set(found) - olds)
    stale_allowed = sorted(permitted - set(found) - olds)
    groups = allowed_groups(allowed, directory)
    shown = {key: groups[key] for key in groups.keys() & found.keys()} | {
        root: groups[old] for root, old in moved if old in groups
    }
    package_dir = repo / "packages" / directory
    facts = GroupFacts(
        writers=frozenset(key for key, group in groups.items() if group == ADR_WRITER_GROUP),
        commands=frozenset(commands.get(directory, {})),
        plugin_modules=tool.plugin_modules(tool.read_layout(package_dir)),
        startup_calls=tool.startup_calls(package_dir),
        built=frozenset(
            found[key]["returns"]
            for key, group in shown.items()
            if group == BUILDER_GROUP and found[key]["form"] == tool.BUILDER
        ),
    )
    misshapen = {
        group: [
            found[key]
            for key in sorted(shown)
            if shown[key] == group and not fits_allowed_group(group, found[key], package, facts)
        ]
        for group in ALLOWED_GROUPS
    }
    unread = unread_native_modules(package, entry, repo)
    problems = [
        *([_unread_native_text(directory, unread)] if unread else []),
        *([_new_text(directory, new, found)] if new else []),
        *([_stale_text(directory, stale)] if stale else []),
        *([_stale_allowed_text(directory, stale_allowed)] if stale_allowed else []),
        *(_misshapen_text(directory, group, rows) for group, rows in misshapen.items() if rows),
    ]
    assert not problems, "\n".join(problems)


def decision_problem(decision: str) -> str | None:
    """Problem in the basis field (None if none). A basis is the ADR rule that allows the row (ADR-DOE-API-001 R<number>) or, for a row with no rule, one English
    sentence giving the reason it is allowed (one line ending with .) — record ids and issue numbers that outside readers cannot look up are not written."""
    text = decision.strip()
    if PRIVATE_MARK.search(text):
        return f"basis {decision!r} contains a record id or an issue number (write an ADR rule or one sentence giving the reason it is allowed)"
    if DECISION_RULE.fullmatch(text) or (text.endswith(".") and "\n" not in text and len(text) > 1):
        return None
    return f"basis {decision!r} is neither an ADR rule (ADR-DOE-API-001 R<number>) nor one sentence ending with . giving the reason it is allowed"


def _added_row_problems(
    row: AddedName, table: KnownTable, listed: set[tuple[str, str, str]]
) -> list[str]:
    """Problems of one "added names" row: fields, package, reason, and overlap with the "names" groups."""
    where = f"added names {row.get('package')} {row.get('module')}.{row.get('name')}"
    if set(row) != ADDED_FIELDS:
        return [
            f"{where}: the fields are package, module, name, and reason (allowed things go in the allowed table {ALLOWED.relative_to(ROOT)})"
        ]
    checks = (
        (row["package"] not in table["packages"], "the package is not in the table"),
        (not row["reason"].strip(), "the reason is empty"),
        ((row["package"], row["module"], row["name"]) in listed, "it is also in the names groups"),
    )
    return [f"{where}: {text}" for failed, text in checks if failed]


def shape_problems(table: KnownTable, package_dirs: Iterable[str]) -> list[str]:
    """Problems of the table's shape: missing or extra dirs, scope, group names, order and overlap of names, and the reasons in "added names"."""
    dirs = set(package_dirs)
    listed_dirs = set(table["packages"])
    entries = sorted(table["packages"].items())
    placements = {
        (package_dir, group, module, name)
        for package_dir, entry in entries
        for group, modules in entry["names"].items()
        for module, names in modules.items()
        for name in names
    }
    groups_per_name = collections.Counter(
        (package_dir, module, name) for package_dir, _group, module, name in placements
    )
    return [
        *(f"in packages/ but not in the table: {name}" for name in sorted(dirs - listed_dirs)),
        *(f"in the table but not in packages/: {name}" for name in sorted(listed_dirs - dirs)),
        *(
            f"{package_dir}: scope {entry.get('scope')!r} is none of {SCOPES}"
            for package_dir, entry in entries
            if entry.get("scope") not in SCOPES
        ),
        *(
            f"{package_dir}: a package out of scope has a basis (an ADR rule or one sentence giving the reason) and no names groups"
            for package_dir, entry in entries
            if entry.get("scope") == "out of scope"
            and ("basis" not in entry or decision_problem(entry["basis"]) is not None or bool(entry["names"]))
        ),
        *(
            f"{package_dir}: the allowed group {group!r} is not placed in the known table — place it in the allowed table {ALLOWED.relative_to(ROOT)} "
            "with a group and a basis (the known table holds only violations and boundaries)"
            if group.startswith(ALLOWED_HEAD)
            else f"{package_dir}: the group name {group!r} does not start with any of {GROUP_HEADS}"
            for package_dir, entry in entries
            for group in entry["names"]
            if not group.startswith(GROUP_HEADS)
        ),
        *(
            f"{package_dir} {group} {module}: names must be non-empty, sorted, and not duplicated"
            for package_dir, entry in entries
            for group, modules in entry["names"].items()
            for module, names in modules.items()
            if not names or names != sorted(set(names))
        ),
        *(
            f"{package_dir}: {module}.{name} is in two or more groups"
            for (package_dir, module, name), count in sorted(groups_per_name.items())
            if count > 1
        ),
        *(
            problem
            for row in table["added_names"]
            for problem in _added_row_problems(row, table, set(groups_per_name))
        ),
    ]


def _allowed_row_problems(
    row: AllowedName, table: KnownTable, violations: set[tuple[str, str, str]]
) -> list[str]:
    """Problems of one row of the allowed table: fields, group, basis, package, and overlap with the known table."""
    where = f"allowed {row.get('package')} {row.get('module')}.{row.get('name')}"
    if set(row) != ALLOWED_FIELDS:
        return [f"{where}: the fields are package, module, name, group, and basis (a row without a basis cannot be placed)"]
    entry = table["packages"].get(row["package"])
    checks = (
        (
            row["group"] not in ALLOWED_GROUPS,
            f"group {row['group']!r} is none of {ALLOWED_GROUPS} (an allowed group has its shape checked by the inventory)",
        ),
        (
            (problem := decision_problem(row["basis"])) is not None,
            problem or "",
        ),
        (entry is None, "the package is not in the known table"),
        (
            entry is not None and entry["scope"] == "out of scope",
            "names of a package out of scope are not allowed (names are not matched)",
        ),
        (
            (row["package"], row["module"], row["name"]) in violations,
            "it is also in the known table (one name is placed in only one table)",
        ),
    )
    return [f"{where}: {text}" for failed, text in checks if failed]


def allowed_problems(allowed: AllowedTable, table: KnownTable) -> list[str]:
    """Problems of the allowed table's shape: per-row fields, group, basis, package, and overlap with the known table, and the order and overlap of rows."""
    rows = allowed["allowed"]
    violations = listed_names(table) | {
        (row["package"], row["module"], row["name"]) for row in table["added_names"]
    }
    keys = [(row.get("package", ""), row.get("module", ""), row.get("name", "")) for row in rows]
    return [
        *(problem for row in rows for problem in _allowed_row_problems(row, table, violations)),
        *(
            ["rows of the allowed table must be sorted by (package, module, name) and not duplicated"]
            if keys != sorted(set(keys))
            else []
        ),
    ]


def grown_names(
    first: KnownTable, current: KnownTable, first_submodules: KnownTable | None = None
) -> list[tuple[str, str, str]]:
    """Names in the current "names" groups that are not in the "names" groups of the first version (where the table has grown).

    Public submodule rows are compared with the version at which the submodule rows were added to the table (first_submodules). While that version does not exist yet (before the commit
    that adds submodule rows), they are not compared.
    """
    listed = listed_names(current)
    names = {row for row in listed if row[1] != SUBMODULE} - listed_names(first)
    submodules = (
        {row for row in listed if row[1] == SUBMODULE} - listed_names(first_submodules)
        if first_submodules is not None
        else set()
    )
    return sorted(names | submodules)


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *arguments], capture_output=True, text=True, check=True
    ).stdout


# The keys of the table before it was written in English (2026-11-05): versions read from git history are read with the
# current keys, so the only-shrinks comparison keeps reading the first version.
_KEYS_BEFORE_ENGLISH: dict[str, str] = {
    "\u8aac\u660e": "description",
    "\u7bc4\u56f2": "scope",
    "\u540d": "names",
    "\u8db3\u3057\u305f\u540d": "added_names",
    "\u8a33": "reason",
}


def _with_current_keys(value: object) -> object:
    """A table version read from git history, with the keys it had before the English rewrite renamed to the current ones."""
    match value:
        case dict():
            return {_KEYS_BEFORE_ENGLISH.get(key, key): _with_current_keys(item) for key, item in value.items()}
        case list():
            return [_with_current_keys(item) for item in value]
        case _:
            return value


def _version_at(commit: str, relative: str) -> KnownTable:
    return _with_current_keys(json.loads(_git("show", f"{commit}:{relative}")))


def first_version() -> KnownTable | None:
    """The version at the commit that added the known table (the first version in git history). None while it is not yet committed."""
    relative = TABLE.relative_to(ROOT).as_posix()
    added = _git("log", "--diff-filter=A", "--format=%H", "--", relative).split()
    if not added:
        assert not _git("ls-files", "--", relative).strip(), (
            f"{relative} is tracked but the commit that added it is not in the history — the counterpart for the only-shrinks comparison cannot be read"
        )
        return None
    return _version_at(added[-1], relative)


def first_submodule_version() -> KnownTable | None:
    """The version at the commit that added public submodule rows to the known table (the first version in which the row's field string appears in history). None if there is none yet."""
    relative = TABLE.relative_to(ROOT).as_posix()
    marker = json.dumps(SUBMODULE)
    added = _git("log", "--format=%H", "-S", marker, "--", relative).split()
    if not added:
        committed = subprocess.run(
            ["git", "-C", str(ROOT), "show", f"HEAD:{relative}"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        assert marker not in committed, (
            f"{relative} has submodule rows in its committed version but the commit that added them is not in the history — the counterpart for the only-shrinks comparison cannot be read"
        )
        return None
    return _version_at(added[-1], relative)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize("package_dir", [path.name for path in tool.package_dirs(ROOT)])
def test_public_api_outside_effects_and_handlers_is_in_the_known_table(
    package_dir: str, deadline_scale_factor: float
) -> None:
    """Among a package's public names, those outside effects and handlers are exactly the names in the known table and the allowed table (add or fix, and match
    the tables)."""
    [package] = tool.inventory(ROOT, [package_dir], jobs=1, timeout=_PROBE_SECONDS * deadline_scale_factor)
    check_package(package, _read_table(), _read_allowed())


def test_the_known_table_has_one_well_formed_entry_per_package_dir() -> None:
    """The table has one entry per dir under packages/, and its scope, group names, order of names, and the reasons in "added names" are well formed. It has no
    allowed groups."""
    problems = shape_problems(_read_table(), (path.name for path in tool.package_dirs(ROOT)))
    assert not problems, "\n".join(problems)


def test_the_allowed_table_is_well_formed() -> None:
    """Rows of the allowed table: the group is one whose shape is checked, the basis is an ADR rule or one sentence giving the reason, the package is in scope in the known table, rows are sorted by key, and they do not overlap the known table."""
    problems = allowed_problems(_read_allowed(), _read_table())
    assert not problems, "\n".join(problems)


def test_the_known_table_only_shrinks_from_its_first_version() -> None:
    """The table's "names" groups only shrink from the version at the commit that added the table (a name that can only be added goes in "added names" with a reason)."""
    first = first_version()
    if first is None:
        return
    grown = grown_names(first, _read_table(), first_submodule_version())
    assert not grown, (
        "The known table's \"names\" groups have names not in the first version — the table only shrinks. A name that can only be added goes in \"added names\" with a reason:\n"
        + "\n".join(f"  {package_dir} {module}.{name}" for package_dir, module, name in grown)
    )


# --- Submodules named like a name the entry point shows (ADR-DOE-API-001 R34) ---

#: Endings of child module files (for extension modules, the name is up to the first . after it).
_CHILD_SUFFIXES = (".py", ".pyi", ".hy")
_EXTENSION_SUFFIXES = (".so", ".pyd")


def _bound_by_entry(node: ast.stmt, root: str) -> Iterator[tuple[str, int]]:
    """The names and lines that one top-level statement of the entry point binds (an import that binds the child module itself and ``*`` are not counted)."""
    # Do not write a match statement: the commit hook's semgrep cannot read a match statement as syntax (same as the former move tool).
    if isinstance(node, (ast.If, ast.Try)):
        handlers = node.handlers if isinstance(node, ast.Try) else []
        finals = node.finalbody if isinstance(node, ast.Try) else []
        for inner in [*node.body, *(s for h in handlers for s in h.body), *node.orelse, *finals]:
            yield from _bound_by_entry(inner, root)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        yield node.name, node.lineno
    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Name):
                continue
            if target.id == "_LAZY_EXPORTS" and isinstance(node.value, ast.Dict):
                yield from (
                    (key.value, key.lineno)
                    for key in node.value.keys
                    if isinstance(key, ast.Constant) and isinstance(key.value, str)
                )
            else:
                yield target.id, node.lineno
    elif isinstance(node, ast.ImportFrom):
        own = (node.level == 1 and node.module is None) or (node.level == 0 and node.module == root)
        for alias in node.names:
            name = alias.asname or alias.name
            if alias.name != "*" and not (own and alias.name in (name, f"_{name}")):
                yield name, node.lineno


def _is_child(path: Path) -> bool:
    """Whether this is a child directly under the package that does not start with _ (a module file, a package dir, or a namespace dir)."""
    if path.name.startswith(("_", ".")):
        return False
    if path.is_dir():
        return any(any(path.glob(f"*{suffix}")) for suffix in _CHILD_SUFFIXES)
    return path.name.endswith(_CHILD_SUFFIXES + _EXTENSION_SUFFIXES)


def _child_names(package: Path) -> dict[str, Path]:
    """From the names of children directly under the package to paths (for a .py and a .pyi of the same name, the file that comes first in name order)."""
    return {
        path.name.split(".")[0]: path
        for path in sorted(package.iterdir(), reverse=True)
        if _is_child(path)
    }


#: Entry files that make a dir a package (a Hy entry can be imported too — the fresh-process check imports it).
_ENTRY_FILES = ("__init__.py", "__init__.hy")


def entry_packages(root_dir: Path, root: str) -> list[tuple[str, Path]]:
    """The names and dirs of the import root and the packages under it (including _ packages) — dirs that have an entry file (in name order).

    The entry of a package under the root shows names like the root's, and an old-path import of a moved package overwrites that package's attribute with a child module of the same name
    (the import of doeff_agents.effects.agent overwrote the agent of doeff_agents._effects).
    """
    if not root_dir.is_dir():
        return []
    dirs = [root_dir, *(path for path in root_dir.rglob("*") if path.is_dir())]
    return sorted(
        (".".join([root, *relative.parts]), path)
        for path in dirs
        for relative in [path.relative_to(root_dir)]
        if all(part.isidentifier() and part != "__pycache__" for part in relative.parts)
        and any((path / name).is_file() for name in _ENTRY_FILES)
    )


def written_entry_name_clashes(package_dir: Path) -> list[str]:
    """Literal check: children (including forwarding modules) with the same name as a public name bound by the ``__init__.py`` of the import root and of the packages under it (including _ packages).

    It does not depend on import order — it is red even when a forwarding module replaces the package's class and discards the binding (the former hide workaround).
    """
    repo = package_dir.parent.parent
    entries = [
        (name, path, path / "__init__.py")
        for root in tool.read_layout(package_dir).roots
        for name, path in entry_packages(root.path, root.name)
        if (path / "__init__.py").is_file()
    ]
    return [
        f"{package}.{name}: a child {child.relative_to(repo)} has the same name as the name shown at line {bound[name]} of the entry"
        f" {init.relative_to(repo)}"
        for package, path, init in entries
        for bound in [_entry_bindings(init, package)]
        for name, child in sorted(_child_names(path).items())
        if name in bound
    ]


def _entry_bindings(init: Path, root: str) -> dict[str, int]:
    """From the public names an entry file binds at top level to the line that binds them first."""
    tree = ast.parse(init.read_text(encoding="utf-8"))
    pairs = [pair for node in tree.body for pair in _bound_by_entry(node, root)]
    return {name: line for name, line in reversed(pairs) if not name.startswith("_")}


#: Fresh-process check: import the root and note the entry's public names (dir — including lazy names) that are not modules, import the packages under the root
#: (including _ packages) one by one and note their entry names likewise, import the public modules (including the old paths of forwarding modules and the old names under
#: moved packages) one by one, and after each see whether a noted entry name became a module.
#: Finally read all the entry names (to force lazy names to be created) and count them likewise if a module is returned. Modules whose import failed do not bind names, so they are not
#: counted and are listed in the outcome (shown in the red sentence).
_IMPORTED_PROBE = r"""
import importlib
import importlib.util
import json
import sys
import types

payload = json.loads(sys.argv[1])
sys.path[:0] = payload["first"]
sys.path.extend(place for place in payload["last"] if place not in sys.path)
if importlib.util.find_spec("hy") is not None:
    importlib.import_module("hy")
entries = {root: importlib.import_module(root) for root in payload["roots"]}


def public_names(package):
    module = entries[package]
    return [
        (package, name)
        for name in dir(module)
        if not name.startswith("_") and not isinstance(module.__dict__.get(name), types.ModuleType)
    ]


root_names = [pair for root in payload["roots"] for pair in public_names(root)]


def failure(label, error):
    return [label, f"{type(error).__name__}: {error}"[:200]]


def imported(name):
    try:
        importlib.import_module(name)
    except Exception as error:  # the failure is written into the result, not dropped
        return failure(name, error)
    return None


def bound_modules(names):
    return {
        (root, name): entries[root].__dict__[name].__name__
        for root, name in names
        if isinstance(entries[root].__dict__.get(name), types.ModuleType)
    }


def read(root, name):
    try:
        return getattr(entries[root], name), None
    except Exception as error:  # the failure is written into the result, not dropped
        return None, failure(f"{root}.{name}", error)


# The packages below the roots are imported first (the roots' names are watched meanwhile); their names are
# watched from then on, while the public modules and old paths are imported one by one.
package_steps = [(package, imported(package), bound_modules(root_names)) for package in payload["packages"]]
loaded = [package for package in payload["packages"] if package in sys.modules]
entries = {**entries, **{package: sys.modules[package] for package in loaded}}
names = [*root_names, *(pair for package in loaded for pair in public_names(package))]
steps = [*package_steps, *((name, imported(name), bound_modules(names)) for name in payload["modules"])]
values = {(root, name): read(root, name) for root, name in names}
last = {
    key: value.__name__
    for key, (value, _error) in values.items()
    if isinstance(value, types.ModuleType)
}
first = {
    key: [module, after]
    for after, bound in [("(after reading all the entry names)", last), *reversed([(n, b) for n, _f, b in steps])]
    for key, module in bound.items()
}
print(json.dumps({
    "found": [[root, name, *first[(root, name)]] for root, name in sorted(first)],
    "failed": [*(f for _n, f, _b in steps if f is not None), *(e for _v, e in values.values() if e is not None)],
}, ensure_ascii=False))
"""


def imported_entry_name_clashes(package_dir: Path) -> tuple[list[str], list[str]]:
    """Fresh-process check: the names of entries (of the root and of packages under it) that became modules after importing the packages under the root and all the public modules and old paths,
    and the imports that failed."""
    layout = tool.read_layout(package_dir)
    roots = [root for root in layout.roots if root.path.is_dir()]
    if not roots:
        return [], []
    places = [str(root.path.parent) for root in roots]
    payload = {
        "first": [] if layout.maturin else places,
        "last": places if layout.maturin else [],
        "roots": [root.name for root in roots],
        "packages": [
            name for root in roots for name, _path in entry_packages(root.path, root.name) if name != root.name
        ],
        "modules": [name for name, _path in tool.public_modules(layout) if "." in name],
    }
    done = subprocess.run(
        [sys.executable, "-c", _IMPORTED_PROBE, json.dumps(payload)],
        capture_output=True,
        text=True,
        timeout=_PROBE_SECONDS,
        check=False,
        cwd=package_dir,
    )
    assert done.returncode == 0, f"{package_dir.name}: could not read the entry in a fresh process:\n{done.stderr[-2000:]}"
    result = json.loads(done.stdout.strip().splitlines()[-1])
    return (
        [
            f"{root}.{name}: after importing {after}, the entry's name became the module {module}"
            for root, name, module, after in result["found"]
        ],
        [f"{name}: {error}" for name, error in result["failed"]],
    )


def check_entry_names(package_dir: Path) -> None:
    """That no submodule is named like a name the package's entry shows (both the literal check and the fresh-process check — R34)."""
    imported, failed = imported_entry_name_clashes(package_dir)
    clashes = [*written_entry_name_clashes(package_dir), *imported]
    assert not clashes, (
        f"{package_dir.name}: do not place a submodule (including a forwarding module) named like a name the entry shows"
        "(ADR-DOE-API-001 R34 — an old-path import bound the parent's attribute to a module and hid the entry's name):\n"
        + "\n".join(f"  {line}" for line in clashes)
        + ("\n  imports that failed (not counted because they do not bind names): " + "; ".join(failed) if failed else "")
    )


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize("package_dir", [path.name for path in tool.package_dirs(ROOT)])
def test_no_submodule_is_named_like_a_name_its_entry_shows(package_dir: str) -> None:
    """Do not place under the root a child with the same name as a name the entry (the import root's __init__) shows, and after importing all the public modules and old paths in a fresh process, the entry's
    names do not turn into modules (ADR-DOE-API-001 R34)."""
    check_entry_names(ROOT / "packages" / package_dir)


# --- Failure cases: that the same matching returns red and green on throwaway packages ---

FIXTURE_DIR = "doeff-fixture-api"
FIXTURE_ROOT = "doeff_fixture_api"
FIXTURE_PYPROJECT = f"""
[project]
name = "{FIXTURE_DIR}"
version = "0"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/{FIXTURE_ROOT}"]
"""
EFFECTS_AND_HANDLERS = '''
from doeff import EffectBase, Pass, handler


class PingEffect(EffectBase):
    """The one effect."""


@handler
def ping_handler(effect: PingEffect, k):
    yield Pass()
'''
OUTLIER = '''

def helper(x: int) -> int:
    """A plain function: neither an effect nor a handler."""
    return x + 1
'''


def _fixture_repo(tmp_path: Path, source: str) -> Path:
    """A throwaway repo with one package under packages/ (the contents of __init__ are source)."""
    return _fixture_tree(tmp_path, {"__init__.py": source})


def _fixture_tree(tmp_path: Path, files: Mapping[str, str]) -> Path:
    """A throwaway repo with one package under packages/ (from the names of files under the import root to their contents)."""
    package = tmp_path / "packages" / FIXTURE_DIR
    (package / "src" / FIXTURE_ROOT).mkdir(parents=True, exist_ok=True)
    (package / "pyproject.toml").write_text(textwrap.dedent(FIXTURE_PYPROJECT), encoding="utf-8")
    for relative, source in files.items():
        target = package / "src" / FIXTURE_ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(textwrap.dedent(source), encoding="utf-8")
    return tmp_path


def _fixture_table(
    names: Mapping[str, list[str]], added: list[AddedName] | None = None
) -> KnownTable:
    groups = {"Violation — move to an inner module": dict(names)} if names else {}
    return {
        "packages": {FIXTURE_DIR: {"scope": "in scope", "names": groups}},
        "added_names": added or [],
    }


def _fixture_allowed(helpers: Iterable[str], decision: str = FIXTURE_DECISION) -> AllowedTable:
    """An allowed table that places a throwaway package's entry names in the group "Allowed — program helper"."""
    return _fixture_grouped({(FIXTURE_ROOT, name): HELPER_GROUP for name in helpers}, decision)


def _fixture_grouped(groups: Mapping[Key, str], decision: str = FIXTURE_DECISION) -> AllowedTable:
    """An allowed table that places a throwaway package's (module, name) in their respective groups."""
    return {
        "allowed": [
            {
                "package": FIXTURE_DIR,
                "module": module,
                "name": name,
                "group": groups[(module, name)],
                "basis": decision,
            }
            for module, name in sorted(groups)
        ]
    }


def _read_fixture(repo: Path) -> dict[str, Any]:
    [package] = tool.inventory(repo, [FIXTURE_DIR], jobs=1, timeout=_PROBE_SECONDS)
    return package


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_an_added_outlier_turns_red_and_removing_it_turns_green(tmp_path: Path) -> None:
    """A package of only effects and handlers is green; adding one violating function turns it red (naming it); removing it makes it green again."""
    table = _fixture_table({})
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS)
    check_package(_read_fixture(repo), table)

    _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + OUTLIER)
    with pytest.raises(AssertionError, match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.helper"):
        check_package(_read_fixture(repo), table)

    _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS)
    check_package(_read_fixture(repo), table)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_fixed_name_left_in_the_table_turns_red(tmp_path: Path) -> None:
    """Fixing (removing) a violation in the table but leaving it in the table is red — naming where the table was not shrunk."""
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS)
    table = _fixture_table({FIXTURE_ROOT: ["helper"]})
    with pytest.raises(AssertionError, match=r"in the known table but not in the inventory[\s\S]*doeff_fixture_api\.helper"):
        check_package(_read_fixture(repo), table)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_an_outlier_added_with_a_reason_line_is_green(tmp_path: Path) -> None:
    """A violation that can only be added is green if written in "added names" with a reason. A missing reason turns the table shape check red."""
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + OUTLIER)
    row: AddedName = {
        "package": FIXTURE_DIR,
        "module": FIXTURE_ROOT,
        "name": "helper",
        "reason": "a place where users assemble values directly (example)",
    }
    table = _fixture_table({}, [row])
    check_package(_read_fixture(repo), table)
    assert shape_problems(table, [FIXTURE_DIR]) == []

    lacking = _fixture_table({}, [{**row, "reason": " "}])
    assert shape_problems(lacking, [FIXTURE_DIR]) == [
        f"added names {FIXTURE_DIR} {FIXTURE_ROOT}.helper: the reason is empty",
    ]


#: A package with a Rust extension (the maturin mixed layout — the entry is under python/, the extension is the inner _native). It is not built, so it is absent from the environment.
NATIVE_PYPROJECT = f"""
[project]
name = "{FIXTURE_DIR}"
version = "0"

[build-system]
requires = ["maturin>=1.13,<2.0"]
build-backend = "maturin"

[tool.maturin]
python-source = "python"
module-name = "{FIXTURE_ROOT}._native"
"""


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_native_package_in_scope_that_the_inventory_cannot_import_turns_red(
    tmp_path: Path,
) -> None:
    """If the inventory cannot read a package with a Rust extension by import (it is absent from the environment), a literal reading does not count the names the entry shows from the extension —
    it is red if the package is in scope of the rule (the inventory had not counted Indexer and SymbolInfo of doeff-indexer). A package awaiting a scope answer, whose names are not
    decided, is green by the same reading."""
    package = tmp_path / "packages" / FIXTURE_DIR
    (package / "python" / FIXTURE_ROOT).mkdir(parents=True)
    (package / "pyproject.toml").write_text(textwrap.dedent(NATIVE_PYPROJECT), encoding="utf-8")
    (package / "python" / FIXTURE_ROOT / "__init__.py").write_text(
        f"from {FIXTURE_ROOT}._native import Indexer, SymbolInfo\n", encoding="utf-8"
    )
    found = _read_fixture(tmp_path)
    assert not found["exports"], "a literal reading does not count names the extension shows (the premise of this check)"
    with pytest.raises(
        AssertionError,
        match=rf"{FIXTURE_DIR}: the inventory could not import the modules of a package with a Rust extension",
    ):
        check_package(found, _fixture_table({}), repo=tmp_path)

    waiting = _fixture_table({})
    waiting["packages"][FIXTURE_DIR]["scope"] = "awaiting scope answer"
    check_package(found, waiting, repo=tmp_path)


FACTORY = '''

from doeff.program import ProgramHandler


def ping_handlers() -> ProgramHandler:
    """A handler factory: calling it gives the handler."""
    return ping_handler
'''
NOT_A_FACTORY = '''

from doeff.program import ProgramHandler


def ping_handlers() -> ProgramHandler:
    """Read as a factory by its return type; calling it gives a function without the handler marker."""
    return lambda program: program
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_factory_whose_call_is_not_a_handler_turns_red(tmp_path: Path) -> None:
    """A name read as a function that returns a handler by its return type (a factory) is also a violation if calling it returns a value without the marker _doeff_is_handler_fn — red (naming it).
    Fixing it to return a handler makes it green, and the inventory counts the name as a handler whose marker it confirmed by calling it (ADR-DOE-API-001 R3).
    """
    table = _fixture_table({})
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + NOT_A_FACTORY)
    with pytest.raises(
        AssertionError,
        match=r"in neither the known table nor the allowed table[\s\S]*"
        + re.escape(f"{FIXTURE_ROOT}.ping_handlers({tool.NOT_A_FACTORY}・other"),
    ):
        check_package(_read_fixture(repo), table)

    _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + FACTORY)
    package = _read_fixture(repo)
    check_package(package, table)
    [row] = [row for row in package["exports"] if row["name"] == "ping_handlers"]
    assert (row["kind"], row["form"]) == ("handler", "handler を返す関数")
    assert row["reason"].endswith("calling it returns a handler")


#: The four shapes of a function that returns a program: a helper that only emits effects, three that go beyond a helper's shape (applying a handler value,
#: wrapping with WithHandler, a function of the same package inside applying a handler), and one that answers an effect.
PROGRAM_FUNCTIONS = '''

from doeff import DoExpr, Resume, WithHandlerType, do


@do
def pinging():
    """A program helper: its program only emits the effect."""
    return (yield PingEffect())


@do
def pinging_handled():
    """Past a helper: applies a handler to the program it builds."""
    return (yield ping_handler(pinging()))


@do
def pinging_with_handler():
    """Past a helper: installs a handler with WithHandler."""
    return (yield WithHandlerType(ping_handler, pinging()))


def _install(program):
    return ping_handler(program)


def pinging_wrapped() -> DoExpr:
    """Past a helper: the function of its package it calls installs the handler."""
    return _install(pinging())


@do
def answering(effect, cont):
    """Past a helper: answers an effect (Resume is handler control)."""
    return (yield Resume(cont, 1))
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_a_program_helper_can_be_put_into_the_helper_group(tmp_path: Path) -> None:
    """The group "Allowed — program helper" of the allowed table can hold only names whose inventory shape is a program helper (ADR-DOE-API-001 R18).

    A program function that only emits effects is green if placed in the allowed table, and stays a red violation if it is in neither table. Placing a function that installs a handler
    (applying a handler value, WithHandler, or a function of the same package inside applying one), a function that answers an effect, or a function that does not return a program in the allowed
    table is red (naming the name and shape). A name that is in the allowed table but not in the inventory is also red (a deleted name left in the table).
    """
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + PROGRAM_FUNCTIONS + OUTLIER)
    package = _read_fixture(repo)
    forms = {row["name"]: row["form"] for row in package["exports"]}
    assert forms["pinging"] == tool.PROGRAM_HELPER
    rest = ["answering", "helper", "pinging_handled", "pinging_with_handler", "pinging_wrapped"]

    def tables(helpers: list[str]) -> tuple[KnownTable, AllowedTable]:
        listed = sorted(set(rest) - set(helpers))
        return _fixture_table({FIXTURE_ROOT: listed} if listed else {}), _fixture_allowed(helpers)

    check_package(package, *tables(["pinging"]))
    with pytest.raises(
        AssertionError, match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.pinging"
    ):
        check_package(package, *tables([]))

    past_a_helper = [
        ("pinging_handled", tool.HELPER_INSTALLS, "pinging_handled reads ping_handler"),
        (
            "pinging_with_handler",
            tool.HELPER_INSTALLS,
            "pinging_with_handler reads WithHandlerType",
        ),
        ("pinging_wrapped", tool.HELPER_INSTALLS, "_install reads ping_handler"),
        ("answering", tool.HELPER_ANSWERS, "answering reads Resume"),
        ("helper", tool.CONVERSION, "return type int"),
    ]
    for name, form, said in past_a_helper:
        with pytest.raises(AssertionError, match=re.escape(HELPER_GROUP)) as failure:
            check_package(package, *tables(["pinging", name]))
        assert f"{FIXTURE_ROOT}.{name}({form}・" in str(failure.value)
        assert said in str(failure.value)

    with pytest.raises(
        AssertionError,
        match=r"1 name\(s\) are in the allowed table but not in the inventory[\s\S]*doeff_fixture_api\.gone",
    ):
        check_package(package, *tables(["gone", "pinging"]))


CONVERSIONS = '''

import time
from datetime import datetime, timedelta, timezone

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ONE_MS = timedelta(milliseconds=1)


def _aware(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("at must be timezone-aware")
    return at


def ms_of(at: datetime) -> int:
    """A conversion: a value in, a value out (the function of its package it reads is one too)."""
    return (_aware(at) - _EPOCH) // _ONE_MS


def _stamp() -> float:
    return time.time()


def ms_now() -> int:
    """Past a conversion: the function of its package it calls reads the clock."""
    return int(_stamp() * 1000)


def ms_today() -> int:
    """Past a conversion: reads the clock by an attribute."""
    return (datetime.now(timezone.utc) - _EPOCH) // _ONE_MS


def pinged(n: int) -> int:
    """Past a conversion: makes an effect."""
    PingEffect()
    return n
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_a_conversion_can_be_put_into_the_conversion_group(tmp_path: Path) -> None:
    """The group "Allowed — conversion function" of the allowed table can hold only names whose inventory shape is a conversion function (ADR-DOE-API-001 R35).

    A function that only takes a value and returns a value (the same for a function of the same package it reads inside) is green if placed in the allowed table, and stays a red violation
    if it is in neither table. Placing a function whose inner function of the same package reads the clock, reads the clock through an attribute, or creates an effect is red (naming the group, name, shape, and name read).
    """
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + CONVERSIONS)
    package = _read_fixture(repo)
    forms = {row["name"]: row["form"] for row in package["exports"]}
    assert forms["ms_of"] == tool.CONVERSION
    rest = ["ms_now", "ms_today", "pinged"]

    def tables(conversions: list[str]) -> tuple[KnownTable, AllowedTable]:
        listed = sorted(set(rest) - set(conversions))
        allowed = _fixture_grouped(
            {(FIXTURE_ROOT, name): CONVERSION_GROUP for name in conversions},
            FIXTURE_DECISION,
        )
        return _fixture_table({FIXTURE_ROOT: listed} if listed else {}), allowed

    check_package(package, *tables(["ms_of"]))
    with pytest.raises(AssertionError, match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.ms_of"):
        check_package(package, *tables([]))

    past_a_conversion = [
        ("ms_now", "_stamp reads time"),
        ("ms_today", "ms_today reads now"),
        ("pinged", "pinged reads PingEffect"),
    ]
    for name, said in past_a_conversion:
        with pytest.raises(AssertionError, match=re.escape(CONVERSION_GROUP)) as failure:
            check_package(package, *tables(["ms_of", name]))
        assert f"{FIXTURE_ROOT}.{name}({tool.PLAIN_FUNCTION}・" in str(failure.value)
        assert said in str(failure.value)


#: A class that is an exception defined by the package, and a class that is not an exception.
EXCEPTIONS = '''


class PingFailed(Exception):
    """An exception the handler raises: its package defines it."""


class PingNote:
    """A class that is not an exception."""
'''
#: A submodule that re-exports an exception of the standard library through __all__ (a child of BaseException, but not a name the package defined).
REEXPORTED_EXCEPTION = '''
from json import JSONDecodeError

__all__ = ["JSONDecodeError"]
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_an_exception_class_of_its_package_can_be_put_into_the_exception_group(
    tmp_path: Path,
) -> None:
    """The group "Allowed — exception type" of the allowed table can hold only names whose inventory shape is an exception class and that the package defined
    (ADR-DOE-API-001 R2).

    A class that is a child of BaseException defined by the package is green if placed in the allowed table, and stays a red violation if it is in neither table. Placing a class that is not an exception,
    or a name that re-exports an exception of the standard library through a submodule's __all__, is red (naming the group, name, shape, and definition place).
    """
    repo = _fixture_tree(
        tmp_path,
        {"__init__.py": EFFECTS_AND_HANDLERS + EXCEPTIONS, "errors.py": REEXPORTED_EXCEPTION},
    )
    package = _read_fixture(repo)
    errors = f"{FIXTURE_ROOT}.errors"
    forms = {(row["module"], row["name"]): row["form"] for row in package["exports"]}
    assert forms[(FIXTURE_ROOT, "PingFailed")] == forms[(errors, "JSONDecodeError")] == tool.EXCEPTION_CLASS
    assert forms[(FIXTURE_ROOT, "PingNote")] == "class"
    outliers = {FIXTURE_ROOT: ["PingNote"], errors: ["JSONDecodeError"], SUBMODULE: [errors]}

    def tables(exceptions: list[Key]) -> tuple[KnownTable, AllowedTable]:
        listed = {
            module: [name for name in names if (module, name) not in exceptions]
            for module, names in outliers.items()
        }
        allowed = _fixture_grouped(
            {key: EXCEPTION_GROUP for key in exceptions}, FIXTURE_DECISION
        )
        return _fixture_table({m: n for m, n in listed.items() if n}), allowed

    defined = (FIXTURE_ROOT, "PingFailed")
    check_package(package, *tables([defined]))
    with pytest.raises(AssertionError, match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.PingFailed"):
        check_package(package, *tables([]))

    misplaced = [
        ((FIXTURE_ROOT, "PingNote"), f"{FIXTURE_ROOT}.PingNote(class・class・"),
        (
            (errors, "JSONDecodeError"),
            f"{errors}.JSONDecodeError({tool.EXCEPTION_CLASS}・a subclass of BaseException・json.decoder)",
        ),
    ]
    for key, said in misplaced:
        with pytest.raises(AssertionError, match=re.escape(f'group "{EXCEPTION_GROUP}"')) as failure:
            check_package(package, *tables([defined, key]))
        assert said in str(failure.value)
        assert ALLOWED_SHAPES[EXCEPTION_GROUP] in str(failure.value)

#: The types and builders of values representing the shape of a program (the shape of AgentSpec and agent! of doeff-conductor), and things beyond a builder's shape: functions that emit or
#: create effects, whose inner functions of the same package read the environment or files, functions that return a class that is not shown, functions that return outside value types, and a class with no builder.
BUILDERS = '''

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StepSpec:
    """A value of a program's shape: what one step of a workflow does."""

    name: str


@dataclass(frozen=True)
class _DraftSpec:
    name: str


@dataclass(frozen=True)
class TraceNote:
    """A class no builder returns."""

    text: str


def _normalized(name: str) -> str:
    return name.strip().lower()


def step(name: str) -> StepSpec:
    """A builder: a value of its package's shown type in, nothing outside read (its package's function too)."""
    return StepSpec(name=_normalized(name))


def step_yielding(name: str) -> StepSpec:
    """Past a builder: emits an effect."""
    yield PingEffect()
    return StepSpec(name=name)


def step_pinged(name: str) -> StepSpec:
    """Past a builder: makes an effect."""
    PingEffect()
    return StepSpec(name=name)


def _home() -> str:
    return os.environ["HOME"]


def step_at_home() -> StepSpec:
    """Past a builder: the function of its package it calls reads the environment."""
    return StepSpec(name=_home())


def step_from(path: str) -> StepSpec:
    """Past a builder: reads a file."""
    return StepSpec(name=Path(path).read_text())


def draft(name: str) -> _DraftSpec:
    """Past a builder: its package does not show the class it returns."""
    return _DraftSpec(name=name)


def step_count(name: str) -> int:
    """Past a builder: a conversion — it returns no class of its package."""
    return len(_normalized(name))
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_a_builder_and_the_class_it_returns_can_be_put_into_the_builder_group(
    tmp_path: Path,
) -> None:
    """The group "Allowed — program-shape builder and type" of the allowed table can hold only builders whose inventory shape is a builder and whose returned class is a name the package
    shows, and classes that the group's builders return (ADR-DOE-API-001 R37).

    A builder (takes a value and returns a value of a class the same package shows, reading no function of the same package, effect, or I/O inside) and its type are green if placed
    in the allowed table, and stay red violations if they are in neither table. Placing a function that emits (yield) or creates an effect, whose inner function of the same package reads the
    environment, or that reads a file, a function that returns a class that is not shown, a function that returns outside value types, or a class that no builder of the group returns (a class with no
    builder, or a class placed as a type alone without its builder in the group) is red (naming the group, name, shape, and the name read or class returned).
    """
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + BUILDERS)
    package = _read_fixture(repo)
    rows = {row["name"]: row for row in package["exports"]}
    step_spec = tool.built_class("StepSpec", rows["StepSpec"]["defined_at"])
    assert (rows["step"]["form"], rows["step"].get("returns")) == (tool.BUILDER, step_spec)
    assert rows["draft"]["form"] == tool.BUILDER
    assert rows["step_count"]["form"] == tool.CONVERSION
    shapes = ["StepSpec", "step"]
    rest = (
        "TraceNote",
        "draft",
        "step_at_home",
        "step_count",
        "step_from",
        "step_pinged",
        "step_yielding",
    )

    def tables(names: list[str], known: Iterable[str] = ()) -> tuple[KnownTable, AllowedTable]:
        listed = sorted((set(rest) | set(known)) - set(names))
        allowed = _fixture_grouped(
            {(FIXTURE_ROOT, name): BUILDER_GROUP for name in names},
            FIXTURE_DECISION,
        )
        return _fixture_table({FIXTURE_ROOT: listed} if listed else {}), allowed

    check_package(package, *tables(shapes))
    with pytest.raises(AssertionError, match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.step\("):
        check_package(package, *tables([]))

    past_a_builder = [
        ("step_yielding", f"{tool.PLAIN_FUNCTION}・", "step_yielding reads yield"),
        ("step_pinged", f"{tool.PLAIN_FUNCTION}・", "step_pinged reads PingEffect"),
        ("step_at_home", f"{tool.PLAIN_FUNCTION}・", "_home reads environ"),
        ("step_from", f"{tool.PLAIN_FUNCTION}・", "step_from reads read_text"),
        ("draft", f"{tool.BUILDER}・", "a class _DraftSpec("),
        ("step_count", f"{tool.CONVERSION}・", "return type int"),
        ("TraceNote", "class・class・", "class・class・"),
    ]
    for name, form, said in past_a_builder:
        with pytest.raises(AssertionError, match=re.escape(f'group "{BUILDER_GROUP}"')) as failure:
            check_package(package, *tables([*shapes, name]))
        assert f"{FIXTURE_ROOT}.{name}({form}" in str(failure.value)
        assert said in str(failure.value)
        assert ALLOWED_SHAPES[BUILDER_GROUP] in str(failure.value)

    with pytest.raises(AssertionError, match=re.escape(f'group "{BUILDER_GROUP}"')) as failure:
        check_package(package, *tables(["StepSpec"], known=["step"]))
    assert f"{FIXTURE_ROOT}.StepSpec(class・class・" in str(failure.value)



#: A package with two effect classes that have value types in their fields (a dataclass and a class written with __init__), and a class that appears in no effect's fields.
VALUE_TYPES = '''
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from doeff import EffectBase, Pass, handler


class Mode(Enum):
    """A value type reached through Payload's field."""

    FAST = "fast"


@dataclass(frozen=True)
class Payload:
    """A value type: SendEffect's field is a list of it."""

    mode: Mode
    text: str


class Hint:
    """A value type: NoteEffect's __init__ takes it."""


class Unrelated:
    """A class no effect's field is typed with."""


@dataclass(frozen=True)
class SendEffect(EffectBase):
    """An effect written as a dataclass."""

    payloads: list[Payload]


class NoteEffect(EffectBase):
    """An effect written with __init__."""

    def __init__(self, *, hint: Hint | None = None) -> None:
        super().__init__()
        self.hint = hint


@handler
def send_handler(effect: SendEffect, k):
    yield Pass()
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_an_effect_value_type_can_be_put_into_the_value_type_group(tmp_path: Path) -> None:
    """The group "Allowed — effect value type" of the allowed table can hold only classes and type aliases that appear in the annotations of the fields of effect classes shown by the same package
    (ADR-DOE-API-001 R2).

    Classes that appear in the type arguments of a dataclass effect's fields (list[Payload]), the arguments of an effect written with __init__ (Hint | None), and the fields of value types
    the package defines (Mode of Payload.mode) are green if placed in the allowed table, and stay red violations if they are in neither table. Placing the class Unrelated, which appears in no
    effect's fields, is red (naming the group, name, and shape).
    """
    repo = _fixture_repo(tmp_path, VALUE_TYPES)
    package = _read_fixture(repo)
    places = {shown["name"]: shown["fields"] for shown in package["value_types"]}
    assert places == {
        "Payload": ["SendEffect.payloads"],
        "Mode": ["Payload.mode"],
        "Hint": ["NoteEffect.hint"],
    }
    value_types = ["Hint", "Mode", "Payload"]

    def tables(placed: list[str], listed: list[str]) -> tuple[KnownTable, AllowedTable]:
        allowed = _fixture_grouped(
            {(FIXTURE_ROOT, name): VALUE_TYPE_GROUP for name in placed},
            FIXTURE_DECISION,
        )
        return _fixture_table({FIXTURE_ROOT: listed} if listed else {}), allowed

    check_package(package, *tables(value_types, ["Unrelated"]))
    with pytest.raises(
        AssertionError, match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.Payload"
    ):
        check_package(package, *tables(["Hint", "Mode"], ["Unrelated"]))
    with pytest.raises(AssertionError, match=re.escape(f'group "{VALUE_TYPE_GROUP}"')) as failure:
        check_package(package, *tables([*value_types, "Unrelated"], []))
    assert f"{FIXTURE_ROOT}.Unrelated(class・" in str(failure.value)
    assert ALLOWED_SHAPES[VALUE_TYPE_GROUP] in str(failure.value)


#: The names of a package with an effect whose answer is written in EffectBase[T], an effect whose answer is written only in a docstring, and a class that appears in no effect's answer.
ANSWERS = '''

from dataclasses import dataclass
from enum import Enum


class Mood(Enum):
    """A field type of an answer class."""

    CALM = "calm"


@dataclass(frozen=True)
class Reading:
    """A member of ReadEffect's answer."""

    mood: Mood


@dataclass(frozen=True)
class Calm(Reading):
    """A case of the answer a caller matches."""


@dataclass(frozen=True)
class Refused:
    """The other member of ReadEffect's answer."""


ReadAnswer = Reading | Refused


class ReadEffect(EffectBase[ReadAnswer]):
    """An effect that declares its answer type."""


@dataclass(frozen=True)
class Echoed:
    """The answer of EchoEffect, said in its docstring only."""


class EchoEffect(EffectBase):
    """An effect that names its answer in the docstring only.

    Yields: Echoed
    """


@dataclass(frozen=True)
class Settings:
    """A class no effect answers."""


from collections.abc import Callable


@dataclass(frozen=True)
class Event:
    """The argument of a listener: no effect answers it."""


Listener = Callable[[Event], object]


class PeekEffect(EffectBase[object]):
    """An effect whose answer is any value."""


class FactoryEffect(EffectBase[Callable[..., int]]):
    """An effect that answers a function."""
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_an_answer_type_can_be_put_into_the_answer_type_group(tmp_path: Path) -> None:
    """The group "Allowed — effect answer type" of the allowed table can hold only names that the inventory reads as a package's effect answer type
    (ADR-DOE-API-001 R2).

    The T of EffectBase[T] (a type alias of a sum type) and its classes, the types in the fields of an answer class (Enum), and the children of an answer class are green if placed in the allowed table, and
    stay red violations if they are in neither table. Placing the answer class of an effect whose answer is written only in a docstring, or a class that appears in no effect's answer, is red
    (naming the group, name, and shape). Even if object and Callable appear in an answer, a Callable alias taking a class that no effect answers as an argument (Listener) is not an answer type — the types in a
    Callable's argument list are read too.
    """
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + ANSWERS)
    package = _read_fixture(repo)
    forms = {row["name"]: row["form"] for row in package["exports"]}
    assert (forms["ReadAnswer"], forms["Mood"], forms["Settings"], forms["Listener"]) == (
        "型",
        "Enum",
        "class",
        "型",
    )
    places = {shown["name"]: shown["answers"] for shown in package["answer_types"]}
    assert places == {
        "ReadAnswer": ["ReadEffect"],
        "Reading": ["ReadEffect"],
        "Refused": ["ReadEffect"],
        "Mood": ["Calm.mood", "Reading.mood"],
        "Calm": ["Reading の子"],
    }
    answers = ["Calm", "Mood", "ReadAnswer", "Reading", "Refused"]
    rest = ["Echoed", "Event", "Listener", "Settings"]

    def tables(placed: list[str]) -> tuple[KnownTable, AllowedTable]:
        listed = sorted(set(rest) - set(placed))
        allowed = _fixture_grouped(
            {(FIXTURE_ROOT, name): ANSWER_TYPE_GROUP for name in placed}, FIXTURE_DECISION
        )
        return _fixture_table({FIXTURE_ROOT: listed} if listed else {}), allowed

    check_package(package, *tables(answers))
    with pytest.raises(AssertionError, match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.Reading"):
        check_package(package, *tables([name for name in answers if name != "Reading"]))
    for name in rest:
        with pytest.raises(AssertionError, match=re.escape(f'group "{ANSWER_TYPE_GROUP}"')) as failure:
            check_package(package, *tables([*answers, name]))
        assert f"{FIXTURE_ROOT}.{name}({forms[name]}・" in str(failure.value)
        assert ALLOWED_SHAPES[ANSWER_TYPE_GROUP] in str(failure.value)


#: The names of a package in which an effect waiting on a body-named type declares a bus event, a child effect that only inherits the declaration, and a class of an
#: event that no waiting effect declares.
BUS_EVENTS = '''

from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class Cause:
    """A field type of a bus event."""


@dataclass(frozen=True)
class Fired:
    """An event the package's handler publishes on the bus: the wait declares it."""

    cause: Cause


@dataclass(frozen=True)
class Undeclared:
    """An event class no wait declares."""


class AwaitEventEffect(EffectBase):
    """Wait for the next event of the types the caller names: no one T to write."""

    __doeff_bus_events__: ClassVar[tuple[type[Any], ...]] = (Fired,)


class AwaitEventsEffect(AwaitEventEffect):
    """A wait that inherits the declaration without declaring it itself."""
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_bus_event_a_wait_declares_can_be_put_into_the_answer_type_group(tmp_path: Path) -> None:
    """An effect waiting on a body-named type cannot write one type in EffectBase[T], so the package declares the events it puts on the bus in its own
    __doeff_bus_events__, and the inventory reads them as the waiting effect's answer type (ADR-DOE-API-001 R2).

    A declared event and the types in its fields are green if placed in the group "Allowed — effect answer type", and stay red violations if they are in neither table. A child effect that only
    inherits the declaration does not re-read the declaration (the place of the answer is only the effect that declared it). Placing the class of an event that no waiting effect declares
    is red (naming the group, name, and shape).
    """
    repo = _fixture_repo(tmp_path, EFFECTS_AND_HANDLERS + BUS_EVENTS)
    package = _read_fixture(repo)
    places = {shown["name"]: shown["answers"] for shown in package["answer_types"]}
    assert places == {"Fired": ["AwaitEventEffect の bus の event"], "Cause": ["Fired.cause"]}
    events = ["Cause", "Fired"]

    def tables(placed: list[str]) -> tuple[KnownTable, AllowedTable]:
        listed = sorted({"Undeclared"} - set(placed))
        allowed = _fixture_grouped(
            {(FIXTURE_ROOT, name): ANSWER_TYPE_GROUP for name in placed}, FIXTURE_DECISION
        )
        return _fixture_table({FIXTURE_ROOT: listed} if listed else {}), allowed

    check_package(package, *tables(events))
    with pytest.raises(AssertionError, match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.Fired"):
        check_package(package, *tables(["Cause"]))
    with pytest.raises(AssertionError, match=re.escape(f'group "{ANSWER_TYPE_GROUP}"')) as failure:
        check_package(package, *tables([*events, "Undeclared"]))
    assert f"{FIXTURE_ROOT}.Undeclared(class・" in str(failure.value)
    assert ALLOWED_SHAPES[ANSWER_TYPE_GROUP] in str(failure.value)


#: The names of a package with a factory taking a setting type as an argument, setting types followed through fields (including the return type of a Protocol property), and a class and a function that appear in no argument
#: (annotations are literal).
SETTINGS = '''
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from doeff import EffectBase, Pass, handler
from doeff.program import ProgramHandler


class PingEffect(EffectBase):
    """The one effect."""


@handler
def ping_handler(effect: PingEffect, k):
    yield Pass()


class Level(Enum):
    """A setting type reached through Settings' field."""

    LOW = "low"


@dataclass(frozen=True)
class Mark:
    """A setting type reached through the property of the Protocol a Settings field is typed with."""

    name: str


class _Marked(Protocol):
    """The shape of a class Settings names (inside the package — not shown): it has marks to read."""

    @property
    def marks(self) -> tuple[Mark, ...]: ...


@dataclass(frozen=True)
class Settings:
    """A setting type: ping_handlers takes it."""

    level: Level
    marked: type[_Marked]


@dataclass(frozen=True)
class Hint:
    """A setting type: ping_handlers takes a sequence of it."""


class Unrelated:
    """A class no handler or factory takes."""


def ping_handlers(settings: Settings, hints: Sequence[Hint] = ()) -> ProgramHandler:
    """A handler factory: its parameters name the types it is built with."""
    return ping_handler


def helper(x: int) -> int:
    """A plain function: neither an effect nor a handler."""
    return x + 1
'''
#: A submodule that re-exports a class of the standard library through __all__ and also names it in a factory's argument (not a type the package defined).
REEXPORTED_SETTING = '''
from pathlib import Path

from doeff.program import ProgramHandler

from doeff_fixture_api import ping_handler

__all__ = ["Path", "rooted_ping_handlers"]


def rooted_ping_handlers(root: Path) -> ProgramHandler:
    """A handler factory whose parameter names a class the standard library defines."""
    return ping_handler
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_a_type_a_handler_takes_can_be_put_into_the_setting_group(tmp_path: Path) -> None:
    """The group "Allowed — handler setting type" of the allowed table can hold only types that the package defined and that the inventory reads as appearing in the annotations of the arguments of handlers and factories
    shown by the same package (following the fields of setting types the package defined) (ADR-DOE-API-001 R2).

    The types of a factory's arguments (literal annotations), types inside type arguments (Sequence[Hint]), types in the fields of a setting type (Level of Settings.level), and the return types of the read-only fields
    (properties) of a Protocol that a field names (Mark of marks in type[_Marked] of Settings.marked) are green if placed in the allowed table, and stay red violations if they are in neither table.
    Placing a class or function that appears in no argument, or a name that re-exports the standard library class Path through a submodule's __all__ (it appears in a factory's argument but is not a type the package
    defined), is red (naming the group, name, and shape).
    """
    repo = _fixture_tree(tmp_path, {"__init__.py": SETTINGS, "rooted.py": REEXPORTED_SETTING})
    package = _read_fixture(repo)
    rooted = f"{FIXTURE_ROOT}.rooted"
    places = {(shown["module"], shown["name"]): shown["parameters"] for shown in package["settings"]}
    assert places == {
        (FIXTURE_ROOT, "Settings"): ["ping_handlers(settings)"],
        (FIXTURE_ROOT, "Hint"): ["ping_handlers(hints)"],
        (FIXTURE_ROOT, "Level"): ["Settings.level"],
        (FIXTURE_ROOT, "Mark"): ["_Marked.marks"],
        (rooted, "Path"): ["rooted_ping_handlers(root)"],
    }
    settings = [
        (FIXTURE_ROOT, "Hint"),
        (FIXTURE_ROOT, "Level"),
        (FIXTURE_ROOT, "Mark"),
        (FIXTURE_ROOT, "Settings"),
    ]
    rest = [(FIXTURE_ROOT, "Unrelated"), (FIXTURE_ROOT, "helper"), (rooted, "Path")]

    def tables(placed: list[Key]) -> tuple[KnownTable, AllowedTable]:
        listed = {
            module: sorted(name for each, name in set(rest) - set(placed) if each == module)
            for module in (FIXTURE_ROOT, rooted)
        }
        allowed = _fixture_grouped(dict.fromkeys(placed, SETTING_GROUP), FIXTURE_DECISION)
        known = {module: names for module, names in listed.items() if names} | {SUBMODULE: [rooted]}
        return _fixture_table(known), allowed

    check_package(package, *tables(settings))
    with pytest.raises(AssertionError, match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.Settings"):
        check_package(package, *tables([key for key in settings if key[1] != "Settings"]))
    said = {
        (FIXTURE_ROOT, "Unrelated"): f"{FIXTURE_ROOT}.Unrelated(class・",
        (FIXTURE_ROOT, "helper"): f"{FIXTURE_ROOT}.helper({tool.CONVERSION}・",
        (rooted, "Path"): f"{rooted}.Path(class・class・pathlib)",
    }
    for key in rest:
        with pytest.raises(AssertionError, match=re.escape(f'group "{SETTING_GROUP}"')) as failure:
            check_package(package, *tables([*settings, key]))
        assert said[key] in str(failure.value)
        assert ALLOWED_SHAPES[SETTING_GROUP] in str(failure.value)


#: A package declaration with a module that shows only macros, a module (Hy) that also shows names outside macros, and one console script.
MACRO_MODULE = """
(defmacro ping! []
  "A macro module's macro."
  '(PingEffect))
"""
MIXED_MODULE = """
(defmacro pong! []
  "A macro beside a function."
  1)

(defn plain [x]
  "A plain function beside the macro."
  x)
"""
COMMAND_PYPROJECT = (
    FIXTURE_PYPROJECT + f'\n[project.scripts]\nfixture-command = "{FIXTURE_ROOT}:helper"\n'
)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_a_macro_or_a_command_can_be_put_into_the_macro_and_command_groups(
    tmp_path: Path,
) -> None:
    """The group "Allowed — Hy macro" can hold only Hy macro names and rows of public submodules that show only macros, and the group "Allowed — CLI command" can hold only the rows of console scripts
    that an ADR rule names (ADR-DOE-API-001 R7, R14, R15, R31).

    A macro module (a submodule whose name has no _), macros, and a console script are green if placed in the allowed table. Placing a submodule that also shows names outside macros, a function that is not a macro, or a console
    script in the macro group, or a macro or function in the CLI command group, is red (naming the group, name, and shape).
    Placing a console script that no ADR rule names in the CLI command group is also red (test_a_command_the_adr_does_not_name_turns_red).
    """
    repo = _fixture_tree(
        tmp_path,
        {
            "__init__.py": EFFECTS_AND_HANDLERS + OUTLIER,
            "macros.hy": MACRO_MODULE,
            "mixed.hy": MIXED_MODULE,
        },
    )
    (repo / "packages" / FIXTURE_DIR / "pyproject.toml").write_text(
        textwrap.dedent(COMMAND_PYPROJECT), encoding="utf-8"
    )
    package = _read_fixture(repo)
    macros, mixed = f"{FIXTURE_ROOT}.macros", f"{FIXTURE_ROOT}.mixed"
    forms = {(row["module"], row["name"]): row["form"] for row in package["exports"]}
    assert forms[(macros, "ping!")] == forms[(mixed, "pong!")] == tool.MACRO_FORM
    assert forms[("(console script)", "fixture-command")] == tool.CONSOLE_SCRIPT
    placed = {
        (macros, "ping!"): MACRO_GROUP,
        (SUBMODULE, macros): MACRO_GROUP,
        (mixed, "pong!"): MACRO_GROUP,
        ("(console script)", "fixture-command"): COMMAND_GROUP,
    }
    outliers = {FIXTURE_ROOT: ["helper"], mixed: ["plain"], SUBMODULE: [mixed]}

    def tables(moved: Mapping[Key, str]) -> tuple[KnownTable, AllowedTable]:
        listed = {
            module: [name for name in names if (module, name) not in moved]
            for module, names in outliers.items()
        }
        return _fixture_table({m: n for m, n in listed.items() if n}), _fixture_grouped(
            {**placed, **moved}
        )

    check_package(package, *tables({}), commands=FIXTURE_COMMANDS)
    misplaced = [
        ((SUBMODULE, mixed), MACRO_GROUP, f"{mixed}(public submodule)(公開の submodule・"),
        ((FIXTURE_ROOT, "helper"), MACRO_GROUP, f"{FIXTURE_ROOT}.helper({tool.CONVERSION}・"),
        (
            ("(console script)", "fixture-command"),
            MACRO_GROUP,
            f"(console script).fixture-command({tool.CONSOLE_SCRIPT}・",
        ),
        ((macros, "ping!"), COMMAND_GROUP, f"{macros}.ping!({tool.MACRO_FORM}・"),
        ((FIXTURE_ROOT, "helper"), COMMAND_GROUP, f"{FIXTURE_ROOT}.helper({tool.CONVERSION}・"),
    ]
    for key, group, said in misplaced:
        with pytest.raises(AssertionError, match=re.escape(f'group "{group}"')) as failure:
            check_package(package, *tables({key: group}), commands=FIXTURE_COMMANDS)
        assert said in str(failure.value)
        assert ALLOWED_SHAPES[group] in str(failure.value)


#: A table that reads a throwaway package's console script as a command that an ADR rule names (the shape of ADR_COMMANDS).
FIXTURE_COMMANDS = {FIXTURE_DIR: {"fixture-command": "R7"}}
#: A throwaway ADR that looks up the name in the rule text (only R7 names fixture-command).
FIXTURE_RULES = """
    [(rule R7 "The CLI command a person types (fixture-command) stays.")
     (rule R8 "code does not start a process with a subcommand of the CLI command (fixture-command-daemon was removed).")]
"""


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_command_the_adr_does_not_name_turns_red(tmp_path: Path) -> None:
    """The group "Allowed — CLI command" can hold only commands that an ADR-DOE-API-001 rule names (ADR_COMMANDS) — a throwaway package's
    console script is green if an ADR rule names it, and red if not, even though its shape is a console script (naming the name and shape). Even if it is in the table of command and rule pairs,
    it is red if the rule text does not name it (a different name that matches only part of the name, fixture-command-daemon, is not counted as named)."""
    repo = _fixture_tree(tmp_path, {"__init__.py": EFFECTS_AND_HANDLERS})
    (repo / "packages" / FIXTURE_DIR / "pyproject.toml").write_text(
        textwrap.dedent(COMMAND_PYPROJECT), encoding="utf-8"
    )
    package = _read_fixture(repo)
    command = ("(console script)", "fixture-command")
    tables = _fixture_table({}), _fixture_grouped({command: COMMAND_GROUP})
    check_package(package, *tables, commands=FIXTURE_COMMANDS)
    with pytest.raises(AssertionError, match=re.escape(f'group "{COMMAND_GROUP}"')) as failure:
        check_package(package, *tables, commands={})
    assert f"(console script).fixture-command({tool.CONSOLE_SCRIPT}・" in str(failure.value)
    assert ALLOWED_SHAPES[COMMAND_GROUP] in str(failure.value)

    rules = adr_rules(textwrap.dedent(FIXTURE_RULES))
    assert unnamed_commands(rules, FIXTURE_COMMANDS) == []
    assert unnamed_commands(rules, {FIXTURE_DIR: {"fixture-command": "R8"}}) == [
        f"{FIXTURE_DIR}: command fixture-command: the text of rule R8 does not name this name"
    ]
    assert unnamed_commands(rules, {FIXTURE_DIR: {"fixture-command": "R9"}}) == [
        f"{FIXTURE_DIR}: command fixture-command: rule R9 is not in the ADR"
    ]


def adr_rules(text: str) -> dict[str, str]:
    """From the text of the ADR file, the rule ids (R1 and so on) to the rule text."""
    return {
        match["id"]: match["text"]
        for match in re.finditer(r'\(rule (?P<id>R\d+) "(?P<text>(?:[^"\\]|\\.)*)"', text)
    }


def unnamed_commands(
    rules: Mapping[str, str], commands: Mapping[str, Mapping[str, str]]
) -> list[str]:
    """The command and rule pairs whose rule is not in the ADR or whose rule text does not name the name (read as named only where the characters before and after the name are not name characters)."""
    return [
        f"{directory}: command {name}: rule {rule} is not in the ADR"
        if rule not in rules
        else f"{directory}: command {name}: the text of rule {rule} does not name this name"
        for directory, named in sorted(commands.items())
        for name, rule in sorted(named.items())
        if rule not in rules
        or re.search(rf"(?<![\w.-]){re.escape(name)}(?![\w-])", rules[rule]) is None
    ]


def test_every_command_the_guard_allows_is_named_by_its_rule() -> None:
    """Every command in ADR_COMMANDS has a rule text that names it (ADR-DOE-API-001 R7, R23, R24, R27, R28, R31, R33) — adding a command without revising the rule
    is red."""
    assert not (
        problems := unnamed_commands(adr_rules(ADR_FILE.read_text(encoding="utf-8")), ADR_COMMANDS)
    ), "\n".join(problems)


#: A package declaration with a pytest plugin registration, a startup .pth, and a name outside the plugin module.
PLUGIN_PYPROJECT = (
    FIXTURE_PYPROJECT
    + f"""
[project.entry-points.pytest11]
fixture = "{FIXTURE_ROOT}"

[tool.hatch.build.targets.wheel.force-include]
"src/{FIXTURE_ROOT}.pth" = "{FIXTURE_ROOT}.pth"
"""
)
PLUGIN_ROOT = '''

def declare_slow(test, reason: str) -> None:
    """A declaration the plugin reads off a test function."""
    test.slow_reason = reason


def install() -> None:
    """What the .pth calls at every start of the venv's Python."""
'''
PLUGIN_TOOLS = '''
def tool_helper(x: int) -> int:
    """A plain function of a public submodule (not the plugin's module)."""
    return x + 1
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_only_a_plugin_registration_or_a_startup_call_can_be_put_into_their_groups(
    tmp_path: Path,
) -> None:
    """The group "Allowed — pytest plugin registration" can hold only pytest11 entry point rows and names the plugin module shows, and the group "Allowed —
    startup entry" can hold only names that a .pth shipped by the package calls at every startup (ADR-DOE-API-001 R10, R33, R36).

    A throwaway package's pytest11 registration, plugin declaration, and the install that the .pth calls are green if placed in their respective groups. Placing a name outside the plugin
    module in the plugin group, or a name the .pth does not call in the startup entry group, is red (naming the group, name, and shape). Install is also red if no .pth is shipped.
    """
    repo = _fixture_tree(
        tmp_path,
        {"__init__.py": EFFECTS_AND_HANDLERS + PLUGIN_ROOT, "tools.py": PLUGIN_TOOLS},
    )
    package_dir = repo / "packages" / FIXTURE_DIR
    (package_dir / "pyproject.toml").write_text(textwrap.dedent(PLUGIN_PYPROJECT), encoding="utf-8")
    (package_dir / "src" / f"{FIXTURE_ROOT}.pth").write_text(
        f"import {FIXTURE_ROOT}; {FIXTURE_ROOT}.install()\n", encoding="utf-8"
    )
    package = _read_fixture(repo)
    tools = f"{FIXTURE_ROOT}.tools"
    forms = {(row["module"], row["name"]): row["form"] for row in package["exports"]}
    assert forms[("(entry point)", "pytest11:fixture")] == PYTEST_PLUGIN_FORM
    placed = {
        ("(entry point)", "pytest11:fixture"): PLUGIN_GROUP,
        (FIXTURE_ROOT, "declare_slow"): PLUGIN_GROUP,
        (FIXTURE_ROOT, "install"): STARTUP_GROUP,
    }
    outliers = {tools: ["tool_helper"], SUBMODULE: [tools]}

    def tables(moved: Mapping[Key, str]) -> tuple[KnownTable, AllowedTable]:
        listed = {
            module: [name for name in names if (module, name) not in moved]
            for module, names in outliers.items()
        }
        return _fixture_table({m: n for m, n in listed.items() if n}), _fixture_grouped(
            {**placed, **moved}, FIXTURE_DECISION
        )

    check_package(package, *tables({}), repo=repo)
    misplaced = [
        (
            (tools, "tool_helper"),
            PLUGIN_GROUP,
            f"{tools}.tool_helper({forms[(tools, 'tool_helper')]}・",
        ),
        ((FIXTURE_ROOT, "declare_slow"), STARTUP_GROUP, f"{FIXTURE_ROOT}.declare_slow("),
    ]
    for key, group, said in misplaced:
        with pytest.raises(AssertionError, match=re.escape(f'group "{group}"')) as failure:
            check_package(package, *tables({key: group}), repo=repo)
        assert said in str(failure.value)
        assert ALLOWED_SHAPES[group] in str(failure.value)

    (package_dir / "src" / f"{FIXTURE_ROOT}.pth").unlink()
    with pytest.raises(AssertionError, match=re.escape(f'group "{STARTUP_GROUP}"')) as failure:
        check_package(package, *tables({}), repo=repo)
    assert f"{FIXTURE_ROOT}.install(" in str(failure.value)


def test_an_allowed_group_without_a_check_is_not_well_formed() -> None:
    """An allowed group can hold only groups whose shape the inventory checks (ALLOWED_GROUPS) — allowing a name in a group with no check turns the allowed table's shape check
    red. Placing an allowed group in the known table is also red (the known table holds only violations and boundaries, and can be empty)."""
    table = _fixture_table({})
    allowed = _fixture_allowed(["helper"])
    assert allowed_problems(allowed, table) == []
    allowed["allowed"][0]["group"] = "Allowed — handy function"
    assert allowed_problems(allowed, table) == [
        f"allowed {FIXTURE_DIR} {FIXTURE_ROOT}.helper: group 'Allowed — handy function' is none of {ALLOWED_GROUPS} (an allowed group has its shape checked by the inventory)"
    ]

    table["packages"][FIXTURE_DIR]["names"] = {HELPER_GROUP: {FIXTURE_ROOT: ["helper"]}}
    assert shape_problems(table, [FIXTURE_DIR]) == [
        f"{FIXTURE_DIR}: the allowed group {HELPER_GROUP!r} is not placed in the known table — place it in the allowed table {ALLOWED.relative_to(ROOT)} "
        "with a group and a basis (the known table holds only violations and boundaries)"
    ]


def test_an_allowed_row_without_a_reason_is_not_well_formed() -> None:
    """The allowed table can be added to with a basis: a row whose basis is an ADR rule or one English sentence giving the reason it is allowed is green, and a row without a basis field, an empty basis, or a basis containing a record
    id or an issue number is red.

    A row that places the same name as the known table, a row not sorted by key, and a row of a package out of scope are also red. The old way of allowing something by adding it with the field "group" in "added names"
    is closed (an "added names" row with the field "group" turns the known table's shape check red).
    """
    table = _fixture_table({})
    where = f"allowed {FIXTURE_DIR} {FIXTURE_ROOT}.pinging"
    assert allowed_problems(_fixture_allowed(["pinging"], "ADR-DOE-API-001 R25"), table) == []
    assert allowed_problems(_fixture_allowed(["pinging"], "Only emits effects for its callers."), table) == []
    assert allowed_problems(_fixture_allowed(["pinging"], " "), table) == [
        f"{where}: basis ' ' is neither an ADR rule (ADR-DOE-API-001 R<number>) nor one sentence ending with . giving the reason it is allowed"
    ]
    tracked = "ki-" + "0123456789ab"
    assert allowed_problems(_fixture_allowed(["pinging"], tracked), table) == [
        f"{where}: basis {tracked!r} contains a record id or an issue number (write an ADR rule or one sentence giving the reason it is allowed)"
    ]
    assert allowed_problems(_fixture_allowed(["pinging"], "Allowed in #123."), table) == [
        f"{where}: basis 'Allowed in #123.' contains a record id or an issue number (write an ADR rule or one sentence giving the reason it is allowed)"
    ]
    lacking = cast(
        AllowedTable,
        {
            "allowed": [
                {
                    "package": FIXTURE_DIR,
                    "module": FIXTURE_ROOT,
                    "name": "pinging",
                    "group": HELPER_GROUP,
                }
            ]
        },
    )
    assert allowed_problems(lacking, table) == [
        f"{where}: the fields are package, module, name, group, and basis (a row without a basis cannot be placed)"
    ]

    assert allowed_problems(
        _fixture_allowed(["pinging"]), _fixture_table({FIXTURE_ROOT: ["pinging"]})
    ) == [f"{where}: it is also in the known table (one name is placed in only one table)"]
    unordered = _fixture_allowed(["pinging", "sequence"])
    unordered["allowed"].reverse()
    assert allowed_problems(unordered, table) == [
        "rows of the allowed table must be sorted by (package, module, name) and not duplicated"
    ]
    outside = _fixture_table({})
    outside["packages"][FIXTURE_DIR] = {"scope": "out of scope", "names": {}, "basis": FIXTURE_DECISION}
    assert allowed_problems(_fixture_allowed(["pinging"]), outside) == [
        f"{where}: names of a package out of scope are not allowed (names are not matched)"
    ]

    old_path = cast(
        AddedName,
        {
            "package": FIXTURE_DIR,
            "module": FIXTURE_ROOT,
            "name": "pinging",
            "reason": "a helper shown after the table was added (example)",
            "group": HELPER_GROUP,
        },
    )
    assert shape_problems(_fixture_table({}, [old_path]), [FIXTURE_DIR]) == [
        f"added names {FIXTURE_DIR} {FIXTURE_ROOT}.pinging: the fields are package, module, name, and reason"
        f" (allowed things go in the allowed table {ALLOWED.relative_to(ROOT)})"
    ]


def test_a_name_put_into_the_listed_groups_turns_red() -> None:
    """Adding a name that is not in the first version to the "names" groups makes the only-shrinks check name it (removing is green)."""
    first = _fixture_table({FIXTURE_ROOT: ["a", "b"]})
    assert grown_names(first, _fixture_table({FIXTURE_ROOT: ["a"]})) == []
    assert grown_names(first, _fixture_table({FIXTURE_ROOT: ["a", "b", "c"]})) == [
        (FIXTURE_DIR, FIXTURE_ROOT, "c")
    ]


def test_a_submodule_row_put_into_the_table_after_it_had_submodule_rows_turns_red() -> None:
    """Submodule rows only shrink from the version at which the submodule rows were added (even if a row was not in the first version, it is not read as grown if it is in that version)."""
    first = _fixture_table({FIXTURE_ROOT: ["a"]})
    with_submodules = _fixture_table({FIXTURE_ROOT: ["a"], SUBMODULE: [f"{FIXTURE_ROOT}.x"]})
    assert grown_names(first, with_submodules, with_submodules) == []
    assert grown_names(first, _fixture_table({FIXTURE_ROOT: ["a"]}), with_submodules) == []
    more = _fixture_table(
        {FIXTURE_ROOT: ["a"], SUBMODULE: [f"{FIXTURE_ROOT}.x", f"{FIXTURE_ROOT}.y"]}
    )
    assert grown_names(first, more, with_submodules) == [
        (FIXTURE_DIR, SUBMODULE, f"{FIXTURE_ROOT}.y")
    ]


# --- Failure cases: public submodules, and the fix of moving names from a submodule to the entry point (ADR-DOE-API-001 R5) ---

ANOTHER_EFFECT = '''
from doeff import EffectBase


class PongEffect(EffectBase):
    """An effect in a submodule: the names are fine, the public submodule is not."""
'''
#: The submodule before moving to the entry point: R2's type (a result type), a constant (moved to the entry point), and a violating function (left inside _).
TYPES = '''
from dataclasses import dataclass


@dataclass(frozen=True)
class PingResult:
    """The answer to a ping (a result type, shown from the root)."""

    count: int


LIMIT = 3


def helper(x: int) -> int:
    """An outlier: stays inside the hidden module."""
    return x + 1
'''
#: Two forms in which the entry shows the names of _types: a form imported immediately and a lazy import form (__getattr__, __dir__, TYPE_CHECKING).
ROOT_SHOWS_TYPES = {
    "eager": f"""
from {FIXTURE_ROOT}._types import LIMIT as LIMIT, PingResult as PingResult
""",
    "lazy": f"""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from {FIXTURE_ROOT}._types import LIMIT as LIMIT, PingResult as PingResult

_LAZY = ("LIMIT", "PingResult")


def __getattr__(name):
    if name in _LAZY:
        from {FIXTURE_ROOT} import _types

        return _types.__dict__[name]
    raise AttributeError(name)


def __dir__():
    return sorted([*globals(), *_LAZY])
""",
}
#: Two forms of forwarding module left at the original path: a form that copies names with *, and a form that replaces the sys.modules entry with _types.
SHIM = {
    "star": f"""
from {FIXTURE_ROOT}._types import *
""",
    "alias": f"""
import sys

from {FIXTURE_ROOT} import _types

sys.modules[__name__] = _types
""",
}
#: A different type of the same name that the entry defines itself (not a moved name).
ANOTHER_PING_RESULT = '''


class PingResult:
    """A different definition with the known name."""
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_an_added_public_submodule_turns_red_and_hiding_it_turns_green(tmp_path: Path) -> None:
    """Adding a submodule without _ is red even if the names it shows are only effects (naming the submodule) — hiding it by prefixing the name with _ is green."""
    table = _fixture_table({})
    check_package(_read_fixture(_fixture_repo(tmp_path / "root", EFFECTS_AND_HANDLERS)), table)

    public = _fixture_tree(
        tmp_path / "public", {"__init__.py": EFFECTS_AND_HANDLERS, "extra.py": ANOTHER_EFFECT}
    )
    with pytest.raises(
        AssertionError,
        match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.extra\(public submodule\)",
    ):
        check_package(_read_fixture(public), table)

    hidden = _fixture_tree(
        tmp_path / "hidden", {"__init__.py": EFFECTS_AND_HANDLERS, "_extra.py": ANOTHER_EFFECT}
    )
    check_package(_read_fixture(hidden), table)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
@pytest.mark.parametrize(("root_form", "shim_form"), [("eager", "star"), ("lazy", "alias")])
def test_moving_names_from_a_submodule_to_the_root_keeps_the_table_green(
    tmp_path: Path, root_form: str, shim_form: str
) -> None:
    """Moving names from a submodule to the entry point is green without rewriting the table's module (stage 1 — while the forwarding module remains).

    When the forwarding module is removed at stage 3, what remains in the table is only the hidden submodule and the violating names left inside _, and removing them from the table makes it green
    (names moved to the entry point are still read as the table's submodule row).
    """
    submodule = f"{FIXTURE_ROOT}.types"
    table = _fixture_table({SUBMODULE: [submodule], submodule: ["LIMIT", "PingResult", "helper"]})
    before = _fixture_tree(
        tmp_path / "before", {"__init__.py": EFFECTS_AND_HANDLERS, "types.py": TYPES}
    )
    check_package(_read_fixture(before), table)

    root = EFFECTS_AND_HANDLERS + ROOT_SHOWS_TYPES[root_form]
    stage_1 = _fixture_tree(
        tmp_path / "stage-1", {"__init__.py": root, "_types.py": TYPES, "types.py": SHIM[shim_form]}
    )
    check_package(_read_fixture(stage_1), table)

    stage_3 = _read_fixture(
        _fixture_tree(tmp_path / "stage-3", {"__init__.py": root, "_types.py": TYPES})
    )
    with pytest.raises(AssertionError, match=r"2 name\(s\) are in the known table but not in the inventory") as failure:
        check_package(stage_3, table)
    assert f"{submodule}(public submodule)" in str(failure.value)
    assert f"{submodule}.helper" in str(failure.value)
    check_package(stage_3, _fixture_table({submodule: ["LIMIT", "PingResult"]}))


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_different_definition_with_a_known_name_at_the_root_turns_red(tmp_path: Path) -> None:
    """Adding a different definition of the same name to the entry while the submodule shows the name is red (not read as a moved name)."""
    submodule = f"{FIXTURE_ROOT}.types"
    table = _fixture_table({SUBMODULE: [submodule], submodule: ["LIMIT", "PingResult", "helper"]})
    repo = _fixture_tree(
        tmp_path, {"__init__.py": EFFECTS_AND_HANDLERS + ANOTHER_PING_RESULT, "types.py": TYPES}
    )
    with pytest.raises(
        AssertionError, match=r"in neither the known table nor the allowed table[\s\S]*doeff_fixture_api\.PingResult"
    ):
        check_package(_read_fixture(repo), table)


#: A forwarding module left at the original path after moving a child package to _types (the tool's form — with the marker on its first line).
MARKED_SHIM = f"""# moved-submodule-shim: {FIXTURE_ROOT}.types -> {FIXTURE_ROOT}._types
import sys
from importlib import import_module

sys.modules[__name__] = import_module("{FIXTURE_ROOT}._types")
"""
#: A module under the child package (it shows one violating function).
BELOW = '''
def sub_helper(x: int) -> int:
    """An outlier in a module below the child package."""
    return x - 1
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_marked_shim_keeps_the_modules_below_a_moved_package_at_their_old_names(
    tmp_path: Path,
) -> None:
    """While a child package is moved to _ and a forwarding module with the marker on its first line remains, the modules below are counted by their old names and stay green.

    With a forwarding module without the marker, the modules below disappear from the inventory (the old-named submodule and name rows are in the table but not in the inventory).
    """
    child, below = f"{FIXTURE_ROOT}.types", f"{FIXTURE_ROOT}.types.sub"
    table = _fixture_table(
        {SUBMODULE: [child, below], child: ["LIMIT", "PingResult", "helper"], below: ["sub_helper"]}
    )
    before = _fixture_tree(
        tmp_path / "before",
        {"__init__.py": EFFECTS_AND_HANDLERS, "types/__init__.py": TYPES, "types/sub.py": BELOW},
    )
    check_package(_read_fixture(before), table)

    moved = {
        "__init__.py": EFFECTS_AND_HANDLERS,
        "_types/__init__.py": TYPES,
        "_types/sub.py": BELOW,
    }
    stage_1 = _fixture_tree(tmp_path / "stage-1", {**moved, "types.py": MARKED_SHIM})
    check_package(_read_fixture(stage_1), table)

    unmarked = MARKED_SHIM.split("\n", 1)[1]
    without_mark = _fixture_tree(tmp_path / "unmarked", {**moved, "types.py": unmarked})
    with pytest.raises(AssertionError, match=r"2 name\(s\) are in the known table but not in the inventory") as failure:
        check_package(_read_fixture(without_mark), table)
    assert f"{below}(public submodule)" in str(failure.value)
    assert f"{below}.sub_helper" in str(failure.value)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_marked_shim_of_a_namespace_dir_keeps_the_modules_below_at_their_old_names(
    tmp_path: Path,
) -> None:
    """A child dir without __init__ is moved to _, and while a forwarding module with the marker remains, the modules below are counted by their old names and stay green.

    A namespace dir before the move is not a module (it has no submodule row), and the inventory passes through the dir and counts the modules below.
    The forwarding module stands in for that dir, so it does not count itself as a submodule and passes through the moved-to dir in the same way
    (when the doeff-cluster section sim was moved to _sim, 603 lines below disappeared without following the destination, and the forwarding module became a
    submodule not in the table).
    """
    below = f"{FIXTURE_ROOT}.types.sub"
    table = _fixture_table({SUBMODULE: [below], below: ["sub_helper"]})
    before = _fixture_tree(
        tmp_path / "before", {"__init__.py": EFFECTS_AND_HANDLERS, "types/sub.py": BELOW}
    )
    check_package(_read_fixture(before), table)

    stage_1 = _fixture_tree(
        tmp_path / "stage-1",
        {"__init__.py": EFFECTS_AND_HANDLERS, "_types/sub.py": BELOW, "types.py": MARKED_SHIM},
    )
    check_package(_read_fixture(stage_1), table)


#: A child module that fails when imported (the inventory reads names from its text — like doeff-ml-nexus's Hy module that cannot be imported).
UNIMPORTABLE = '''
raise RuntimeError("this module cannot be imported")


def helper(x: int) -> int:
    """An outlier, read from the text of a module that cannot be imported."""
    return x + 1
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_marked_shim_of_a_module_that_cannot_be_imported_keeps_the_names_of_the_moved_text(
    tmp_path: Path,
) -> None:
    """An unimportable child is moved to _, and while a forwarding module with the marker remains, names are counted by their old names from the destination's text and stay green.

    The forwarding module's text does not define names, so unless the destination is read the names disappear from the inventory and it turns red with names that are in the table but not in the inventory
    (this happened with doeff-ml-nexus's Hy module that cannot be imported).
    """
    child = f"{FIXTURE_ROOT}.types"
    table = _fixture_table({SUBMODULE: [child], child: ["helper"]})
    before = _fixture_tree(
        tmp_path / "before", {"__init__.py": EFFECTS_AND_HANDLERS, "types.py": UNIMPORTABLE}
    )
    check_package(_read_fixture(before), table)

    stage_1 = _fixture_tree(
        tmp_path / "stage-1",
        {"__init__.py": EFFECTS_AND_HANDLERS, "_types.py": UNIMPORTABLE, "types.py": MARKED_SHIM},
    )
    check_package(_read_fixture(stage_1), table)


# --- Only doeff imports its parts (ADR-DOE-API-001 R10, R11) ---

#: Where to look for files outside doeff (from the repo root): under packages/ (excluding the parts' own dirs), examples, and the Hy of docs/adr. The doeff root
#: package (doeff/) and doeff's checks (tests/) are inside doeff.
OUTSIDE_DOEFF = ("packages", "examples", "docs/adr")
#: Kinds of files to read, and dirs not to look in (build products and dependencies — dirs whose names start with . are not looked in either).
_SOURCE_SUFFIXES = (".py", ".pyi", ".hy")
_SKIPPED_DIRS = frozenset({"__pycache__", "node_modules", "target"})
#: Functions that take and read a module name string (importlib.import_module, pytest.importorskip, __import__, importlib.util.find_spec).
_IMPORTING_CALLS = frozenset({"import_module", "importorskip", "__import__", "find_spec"})
#: Hy lexical tokens: strings, comments, parentheses, and other words (parentheses inside strings and comments are not read as forms).
_HY_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|;[^\n]*|[()\[\]{}]|[^\s()\[\]{}";]+')
_OPENING = frozenset({"(", "[", "{"})
_CLOSING = frozenset({")", "]", "}"})


@dataclass(frozen=True)
class PartImport:
    """One place where a file outside doeff imports a part inside doeff (path from the repo root, line, and the imported module)."""

    path: str
    line: int
    module: str


def _is_part(module: str, roots: frozenset[str]) -> bool:
    """Whether the root of a module name (including the Hy - spelling) is one of the parts' import roots."""
    return module.replace("-", "_").split(".")[0] in roots


def _source_files(base: Path, skipped: frozenset[Path]) -> Iterator[Path]:
    """Python and Hy files under base (excluding skipped dirs, dirs that start with ., and dirs of build products)."""
    for directory, dirs, files in os.walk(base):
        here = Path(directory)
        dirs[:] = sorted(
            name
            for name in dirs
            if not name.startswith(".") and name not in _SKIPPED_DIRS and here / name not in skipped
        )
        yield from (here / name for name in sorted(files) if name.endswith(_SOURCE_SUFFIXES))


def _python_imports(text: str) -> Iterator[tuple[int, str]]:
    """Modules that a Python file imports (line and name): import, from (non-relative), and calls to functions that take and read a name string.

    A file that cannot be read because of a syntax error is read literally for its import and from statements at the start of a line.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        for number, line in enumerate(text.splitlines(), start=1):
            found = re.match(r"\s*(?:from|import)\s+([\w.]+)", line)
            if found:
                yield number, found.group(1)
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            yield node.lineno, node.module
        elif isinstance(node, ast.Call) and node.args:
            called = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", None)
            )
            first = node.args[0]
            if (
                called in _IMPORTING_CALLS
                and isinstance(first, ast.Constant)
                and isinstance(first.value, str)
            ):
                yield node.lineno, first.value


def _form_modules(tokens: list[tuple[int, str]], start: int) -> Iterator[tuple[int, str]]:
    """The module words of a Hy import or require form (position and name): of the words one level directly under the form, those excluding :as, the word after it, and what is inside parentheses.

    A form lists, for each module, "module [name ...]" or "module :as alias", so the list of names is inside parentheses and the alias is after :as.
    """
    depth, alias_next = 0, False
    for position, token in tokens[start:]:
        if token in _OPENING:
            depth += 1
        elif token in _CLOSING:
            if depth == 0:
                return
            depth -= 1
        elif depth == 0 and alias_next:
            alias_next = False
        elif depth == 0 and token == ":as":
            alias_next = True
        elif depth == 0 and not token.startswith(('"', ":")):
            yield position, token


def _hy_imports(text: str) -> Iterator[tuple[int, str]]:
    """Modules that a Hy file imports (line and name): the module words of (import ...) and (require ...) forms, and calls to functions that take and read a name string."""
    tokens = [(found.start(), found.group()) for found in _HY_TOKEN.finditer(text)]
    tokens = [(position, token) for position, token in tokens if not token.startswith(";")]
    for index, (_position, token) in enumerate(tokens[:-2]):
        if token != "(":
            continue
        head = tokens[index + 1][1]
        if head in ("import", "require"):
            found = _form_modules(tokens, index + 2)
        elif head.replace("-", "_").rsplit(".", 1)[-1] in _IMPORTING_CALLS:
            position, argument = tokens[index + 2]
            found = iter([(position, argument[1:-1])] if argument.startswith('"') else [])
        else:
            continue
        yield from ((text.count("\n", 0, position) + 1, module) for position, module in found)


def part_imports(repo: Path) -> list[PartImport]:
    """Every place where a file outside doeff (under OUTSIDE_DOEFF — excluding the parts' own dirs) imports an import root of a part inside doeff."""
    roots = frozenset(part.import_root for part in tool.DOEFF_PARTS)
    own = frozenset(repo / "packages" / part.dir for part in tool.DOEFF_PARTS)
    found: list[PartImport] = []
    for base in OUTSIDE_DOEFF:
        for path in _source_files(repo / base, own):
            text = path.read_text(encoding="utf-8", errors="replace")
            if not any(root in text.replace("-", "_") for root in roots):
                continue
            imports = _hy_imports(text) if path.suffix == ".hy" else _python_imports(text)
            relative = path.relative_to(repo).as_posix()
            found.extend(
                PartImport(relative, line, module)
                for line, module in imports
                if _is_part(module, roots)
            )
    return sorted(found, key=lambda row: (row.path, row.line, row.module))


def _part_imports_text(found: list[PartImport]) -> str:
    return (
        "A file outside doeff imports a part inside doeff — the parts are inside doeff and not counted by the inventory (ADR-DOE-API-001 R11). "
        "Read VM types from the doeff root (from doeff import EffectBase, K, Pass, Resume, UnhandledEffect), and read what a doeff subpackage needs "
        "from inside the VM from doeff._vm:\n"
        + "\n".join(f"  {row.path}:{row.line} {row.module}" for row in found)
    )


def test_only_doeff_imports_its_parts() -> None:
    """A file outside doeff (a package under packages/ that is not a part, examples, docs/adr) does not import a part inside doeff (doeff_vm)."""
    found = part_imports(ROOT)
    assert not found, _part_imports_text(found)


#: Forms that read doeff_vm outside doeff (a line with the marker fires at its end is red) and forms that do not turn red (the doeff root, doeff._vm, similar names, comments and strings).
PART_IMPORTS_PY = """
import importlib

import pytest

import doeff_vm  # fires
import doeff_vm.doeff_vm as ext  # fires
from doeff_vm import K  # fires
from doeff_vm._effect_types import declare_passes  # fires
importlib.import_module("doeff_vm.doeff_vm")  # fires
pytest.importorskip("doeff_vm")  # fires

import doeff_vm_like_name
from doeff import K as RootK
from doeff._vm import native

# from doeff_vm import K
SAID = "doeff_vm"
"""
PART_IMPORTS_HY = """
(import doeff-vm [K])  ; fires
(import doeff_vm.doeff_vm :as ext)  ; fires
(import pytest doeff_vm [Pass])  ; fires
(require doeff-vm [m])  ; fires
(pytest.importorskip "doeff_vm")  ; fires

(import doeff [K Pass])
(import doeff._vm [native] doeff-vm-like-name :as like)
;; (import doeff_vm [K])
(setv SAID "(import doeff_vm)")
"""
#: Forms that read the same place from the doeff root and doeff._vm (green).
ROOT_IMPORTS_PY = """
from doeff import K, Pass
from doeff._vm import declare_passes, native
"""
ROOT_IMPORTS_HY = """
(import doeff [K Pass])
(import doeff._vm [native])
"""
#: Files outside doeff (where it turns red) and files inside doeff (the parts themselves, the doeff root package, and doeff's checks — where it is not counted).
OUTSIDE_FILES = {
    f"packages/{FIXTURE_DIR}/src/{FIXTURE_ROOT}/__init__.py": PART_IMPORTS_PY,
    f"packages/{FIXTURE_DIR}/src/{FIXTURE_ROOT}/hy_side.hy": PART_IMPORTS_HY,
    "examples/vm_sample.py": PART_IMPORTS_PY,
    "docs/adr/defadr_vm_sample.hy": PART_IMPORTS_HY,
}
INSIDE_FILES = {
    "packages/doeff-vm/doeff_vm/__init__.py": PART_IMPORTS_PY,
    "doeff/__init__.py": PART_IMPORTS_PY,
    "tests/test_vm_sample.py": PART_IMPORTS_PY,
}


def _write_files(repo: Path, files: Mapping[str, str]) -> None:
    for relative, source in files.items():
        target = repo / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source, encoding="utf-8")


def test_a_doeff_part_imported_outside_doeff_turns_red(tmp_path: Path) -> None:
    """A file outside doeff that imports a part inside doeff (doeff_vm) is red — it names the lines of a plain import, from, a lower module, a Hy import and require
    (including a form that lists modules in one form), and a name string passed to importlib.import_module and pytest.importorskip. Forms that read from the doeff root and
    doeff._vm, similar names, and comments and strings are not counted, and imports of the parts themselves, the doeff root package, and doeff's checks (tests/) are not counted either.
    Fixing the outside file to read from the doeff root makes it green (ADR-DOE-API-001 R11)."""
    _write_files(tmp_path, {**OUTSIDE_FILES, **INSIDE_FILES})
    found = part_imports(tmp_path)
    marked = {
        (relative, number)
        for relative, source in OUTSIDE_FILES.items()
        for number, line in enumerate(source.splitlines(), start=1)
        if line.rstrip().endswith("fires")
    }
    assert {(row.path, row.line) for row in found} == marked
    assert len(found) == len(marked) == 22
    assert {row.module.replace("-", "_") for row in found} == {
        "doeff_vm",
        "doeff_vm.doeff_vm",
        "doeff_vm._effect_types",
    }
    assert "  examples/vm_sample.py:6 doeff_vm" in _part_imports_text(found).splitlines()

    _write_files(
        tmp_path,
        {
            relative: ROOT_IMPORTS_HY if relative.endswith(".hy") else ROOT_IMPORTS_PY
            for relative in OUTSIDE_FILES
        },
    )
    assert part_imports(tmp_path) == []


#: A forwarding module that has, under the marker, a fixed list of names for when it was hidden (the form the former move tool writes).
PINNED_SHIM = MARKED_SHIM.replace(
    "\n",
    f"\n# moved-submodule-shim-names {FIXTURE_ROOT}.types:\n#   LIMIT PingResult\n#   helper\n",
    1,
)
#: An inner helper a writer added to the destination _ module after hiding.
ADDED_INSIDE = '''

def added_helper(x: int) -> int:
    """A helper added inside the hidden module after it was hidden."""
    return x * 2
'''
#: A name a writer added to the entry (a name intended to be shown).
ADDED_AT_ROOT = '''

def root_helper(x: int) -> int:
    """A helper the writer adds to the root on purpose."""
    return x - 2
'''


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_name_added_inside_a_pinned_shim_does_not_grow_the_public_api(tmp_path: Path) -> None:
    """If a forwarding module has a fixed list of names, adding a helper to the destination _ module adds no inventory rows and stays green.

    With a forwarding module without the list, the old path returns the destination module itself, so the added helper joins the old path's
    public names and turns red (merely adding an inner helper grew the public API). A name added to the entry is
    a name the writer intends to show, so it is red even with the list (add it to the table or fix it).
    """
    child = f"{FIXTURE_ROOT}.types"
    table = _fixture_table({SUBMODULE: [child], child: ["LIMIT", "PingResult", "helper"]})
    pinned = _fixture_tree(
        tmp_path / "pinned",
        {
            "__init__.py": EFFECTS_AND_HANDLERS,
            "_types.py": TYPES + ADDED_INSIDE,
            "types.py": PINNED_SHIM,
        },
    )
    package = _read_fixture(pinned)
    check_package(package, table)
    assert {row["name"] for row in package["exports"] if row["module"] == child} == {
        "LIMIT",
        "PingResult",
        "helper",
    }

    unpinned = _fixture_tree(
        tmp_path / "unpinned",
        {
            "__init__.py": EFFECTS_AND_HANDLERS,
            "_types.py": TYPES + ADDED_INSIDE,
            "types.py": MARKED_SHIM,
        },
    )
    with pytest.raises(AssertionError, match=rf"in neither the known table nor the allowed table[\s\S]*{child}\.added_helper"):
        check_package(_read_fixture(unpinned), table)

    at_root = _fixture_tree(
        tmp_path / "at-root",
        {
            "__init__.py": EFFECTS_AND_HANDLERS + ADDED_AT_ROOT,
            "_types.py": TYPES + ADDED_INSIDE,
            "types.py": PINNED_SHIM,
        },
    )
    with pytest.raises(
        AssertionError, match=rf"in neither the known table nor the allowed table[\s\S]*{FIXTURE_ROOT}\.root_helper"
    ) as failure:
        check_package(_read_fixture(at_root), table)
    assert "added_helper" not in str(failure.value)


#: A forwarding module pointing at a namespace dir (_types without __init__), with a fixed list of the names of the modules below written in it.
PINNED_NAMESPACE_SHIM = MARKED_SHIM.replace(
    "\n", f"\n# moved-submodule-shim-names {FIXTURE_ROOT}.types.sub:\n#   sub_helper\n", 1
)


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_name_added_below_a_pinned_namespace_dir_shim_does_not_grow_the_public_api(
    tmp_path: Path,
) -> None:
    """It also reads the fixed list of names of a forwarding module that points at a namespace dir, and stays green when a helper is added under the destination _.

    That forwarding module is not a module, so if the inventory read lists only from a list of modules it was not read, and the added helper joined the old path's
    public names and turned red again (the doeff-cluster sections _sim and _record_store). When there is no list,
    the old paths below are counted as paths without a list (picked up by forwarding modules for which the former move tool (pin) writes a list).
    """
    below = f"{FIXTURE_ROOT}.types.sub"
    table = _fixture_table({SUBMODULE: [below], below: ["sub_helper"]})
    files = {"__init__.py": EFFECTS_AND_HANDLERS, "_types/sub.py": BELOW + ADDED_INSIDE}
    pinned = _fixture_tree(tmp_path / "pinned", {**files, "types.py": PINNED_NAMESPACE_SHIM})
    package = _read_fixture(pinned)
    check_package(package, table)
    assert {row["name"] for row in package["exports"] if row["module"] == below} == {"sub_helper"}
    layout = tool.read_layout(pinned / "packages" / FIXTURE_DIR)
    shim = pinned / "packages" / FIXTURE_DIR / "src" / FIXTURE_ROOT / "types.py"
    assert tool.shim_modules(layout) == [(f"{FIXTURE_ROOT}.types", shim)]
    assert tool.unpinned_shim_modules(layout) == []

    unpinned = _fixture_tree(tmp_path / "unpinned", {**files, "types.py": MARKED_SHIM})
    with pytest.raises(AssertionError, match=rf"in neither the known table nor the allowed table[\s\S]*{below}\.added_helper"):
        check_package(_read_fixture(unpinned), table)
    assert tool.unpinned_shim_modules(tool.read_layout(unpinned / "packages" / FIXTURE_DIR)) == [
        below
    ]


@pytest.mark.parametrize("package_dir", [path.name for path in tool.package_dirs(ROOT)])
def test_every_moved_submodule_shim_pins_the_names_of_its_old_paths(package_dir: str) -> None:
    """Every forwarding module has a fixed list of names for each old path of itself and the modules below (an old path without a list
    counts all names added to the destination _ module as public names — write the list with the former move tool (pin))."""
    layout = tool.read_layout(ROOT / "packages" / package_dir)
    assert not tool.unpinned_shim_modules(layout), (
        f"{package_dir}: there are old paths of forwarding modules without a fixed list of names — "
        f"write a fixed list of names in the forwarding module: "
        + ", ".join(tool.unpinned_shim_modules(layout))
    )


#: A package whose entry shows the function serve lazily (the shape of aiohttp_http_server in doeff_core_effects) and the function tool immediately (the shape of tmux_handler in doeff_agentic).
#: Both functions are in _ modules, and the children serve and tool at the old paths are forwarding modules.
CLASHING_ENTRY = f"""
import importlib as _importlib

from {FIXTURE_ROOT}._tool import tool as tool

_LAZY_EXPORTS = {{"serve": "{FIXTURE_ROOT}._serve"}}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(name)
    value = getattr(_importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({{*globals(), *_LAZY_EXPORTS}})
"""


def _moved_shim(child: str) -> str:
    """A forwarding module for a child ``child`` at an old path (it replaces sys.modules, like the former move tool's shell)."""
    return (
        f"# moved-submodule-shim: {FIXTURE_ROOT}.{child} -> {FIXTURE_ROOT}._{child}\n"
        f"# moved-submodule-shim-names {FIXTURE_ROOT}.{child}:\n"
        f"#   {child}\n"
        "import sys\n"
        "from importlib import import_module\n\n"
        f'sys.modules[__name__] = import_module("{FIXTURE_ROOT}._{child}")\n'
    )


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_a_submodule_named_like_an_entry_name_turns_red_and_removing_it_turns_green(
    tmp_path: Path,
) -> None:
    """Placing a forwarding module with the same name as the function serve (lazy) or tool (immediate) the entry shows turns both the literal check and the fresh-process check
    red (naming which old-path import turned the name into a module), and removing the two shells makes it green (ADR-DOE-API-001 R34).

    The same shape as the shell aiohttp_http_server.py of doeff_core_effects at doeff 42f897000, with which the records service fell over at every startup on 10-30
    with ``with_handlers: handler must be callable, got module``.
    """
    files = {
        "__init__.py": CLASHING_ENTRY,
        "_serve.py": 'def serve() -> str:\n    return "serve"\n',
        "_tool.py": 'def tool() -> str:\n    return "tool"\n',
        "serve.py": _moved_shim("serve"),
        "tool.py": _moved_shim("tool"),
    }
    package_dir = _fixture_tree(tmp_path, files) / "packages" / FIXTURE_DIR
    written = written_entry_name_clashes(package_dir)
    imported, failed = imported_entry_name_clashes(package_dir)
    assert [line.split(":")[0] for line in written] == [f"{FIXTURE_ROOT}.serve", f"{FIXTURE_ROOT}.tool"]
    assert imported == [
        f"{FIXTURE_ROOT}.serve: after importing {FIXTURE_ROOT}.serve, the entry's name became the module {FIXTURE_ROOT}._serve",
        f"{FIXTURE_ROOT}.tool: after importing {FIXTURE_ROOT}.tool, the entry's name became the module {FIXTURE_ROOT}._tool",
    ]
    assert failed == []
    with pytest.raises(AssertionError, match=r"ADR-DOE-API-001 R34"):
        check_entry_names(package_dir)

    for child in ("serve", "tool"):
        (package_dir / "src" / FIXTURE_ROOT / f"{child}.py").unlink()
    check_entry_names(package_dir)


def _moved_package_shim(child: str, names: Mapping[str, str]) -> str:
    """A forwarding module for a child package ``child`` at an old path: both the old path and the old names below it answer (through a meta path finder) with the destination
    module itself (the same shape as the former move tool's package shell). ``names`` goes from an old module's name to its fixed list."""
    old, new = f"{FIXTURE_ROOT}.{child}", f"{FIXTURE_ROOT}._{child}"
    pins = "".join(f"# moved-submodule-shim-names {module}:\n#   {pinned}\n" for module, pinned in names.items())
    return (
        f"# moved-submodule-shim: {old} -> {new}\n{pins}"
        "import importlib.abc\n"
        "import importlib.util\n"
        "import sys\n"
        "from importlib import import_module\n\n\n"
        "class _MovedSubmodules(importlib.abc.MetaPathFinder, importlib.abc.Loader):\n"
        "    def find_spec(self, fullname, path=None, target=None):\n"
        f'        moved = "{new}" + fullname[len("{old}"):]\n'
        f'        if not fullname.startswith("{old}.") or importlib.util.find_spec(moved) is None:\n'
        "            return None\n"
        "        return importlib.util.spec_from_loader(fullname, self)\n\n"
        "    def create_module(self, spec):\n"
        "        return None\n\n"
        "    def exec_module(self, module):\n"
        f'        sys.modules[module.__name__] = import_module("{new}" + module.__name__[len("{old}"):])\n\n\n'
        "sys.meta_path.insert(0, _MovedSubmodules())\n"
        f'sys.modules[__name__] = import_module("{new}")\n'
    )


@pytest.mark.timeout(_PROBE_SECONDS + 20)
def test_an_inner_package_entry_name_like_its_child_turns_red_and_moving_the_child_turns_green(
    tmp_path: Path,
) -> None:
    """When the entry of the package _inner under the root binds the function tool from a child of the same name, tool, and a shell answers the old path inner of the moved package,
    both the literal check and the fresh-process check are red (importing the old path inner.tool overwrites the attribute tool of _inner with a module), and
    moving the child to the _ name _tool makes it green (ADR-DOE-API-001 R34).

    The same shape as the child agent of doeff_agents._effects and the old path doeff_agents.effects.agent of the moved package.
    """
    files = {
        "__init__.py": "",
        "_inner/__init__.py": f"from {FIXTURE_ROOT}._inner.tool import tool as tool\n",
        "_inner/tool.py": 'def tool() -> str:\n    return "tool"\n',
        "inner.py": _moved_package_shim(
            "inner", {f"{FIXTURE_ROOT}.inner": "tool", f"{FIXTURE_ROOT}.inner.tool": "tool"}
        ),
    }
    package_dir = _fixture_tree(tmp_path, files) / "packages" / FIXTURE_DIR
    written = written_entry_name_clashes(package_dir)
    imported, failed = imported_entry_name_clashes(package_dir)
    assert [line.split(":")[0] for line in written] == [f"{FIXTURE_ROOT}._inner.tool"]
    assert imported == [
        f"{FIXTURE_ROOT}._inner.tool: after importing {FIXTURE_ROOT}.inner.tool, the entry's name became the module"
        f" {FIXTURE_ROOT}._inner.tool"
    ]
    assert failed == []
    with pytest.raises(AssertionError, match=r"ADR-DOE-API-001 R34"):
        check_entry_names(package_dir)

    inner = package_dir / "src" / FIXTURE_ROOT / "_inner"
    (inner / "tool.py").rename(inner / "_tool.py")
    (inner / "__init__.py").write_text(f"from {FIXTURE_ROOT}._inner._tool import tool as tool\n", encoding="utf-8")
    check_entry_names(package_dir)
