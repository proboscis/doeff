"""List the public API each subpackage under ``packages/`` shows its users, and type every name.

The one place that counts a subpackage's public API (the rule under discussion: a subpackage shows
its users effects and handlers only). Tests that check the rule read ``inventory`` from here.

What is counted, per package dir:

- the names the import root's ``__init__`` exports (the roots are the wheel's packages in
  ``pyproject.toml``: hatch's ``packages``, maturin's ``python-source`` + ``module-name``,
  setuptools' discovered packages);
- the names every submodule whose path has no part starting with ``_`` exports (a module a user
  can import);
- every such submodule itself, as one row (shown by ``(submodule)``, named by its import name, kind
  ``other``): the public names come from the import root only, so a public submodule is outside
  the rule (ADR-DOE-API-001 R5 — hide it by giving its name a leading ``_``);
- ``[project.scripts]``, ``[project.entry-points]`` and the Rust executables of a maturin ``bin``.

doeff's own parts under ``packages/`` (``DOEFF_PARTS`` — the VM ``doeff-vm``, imported only by doeff) are not
package dirs of the inventory, as the repo root package ``doeff`` is not one (ADR-DOE-API-001 R11).

The names a moved-submodule shim's old path shows (the shim's own name, and the old names of the
modules below the package it moved to) are the names its pins list: the comment lines
``# moved-submodule-shim-names <old module>: <name> ...`` below the mark, written when the submodule
was hidden. Importing the old path gives the moved module itself, so without the pins a helper added
inside the ``_`` module would also count as a public name of the old path. A shim without pins for a
module shows every name, as below.

The names a module exports: its ``__all__`` when it has one, otherwise every name not starting
with ``_`` that the package itself defines (names imported from another package or the standard
library, and module objects, are not counted). A module that makes names on demand (a module-level
``__getattr__``) exports the names its ``__dir__`` and its ``from ... import`` statements (the
``if TYPE_CHECKING:`` block) list too. A Hy module's macros not starting with ``_``
(``_hy_export_macros`` when the module exports them) are counted too.

Kinds:

- ``effect``: a subclass of ``EffectBase``, an ``EffectBase`` value, or a function that makes an
  effect (its return type is an ``EffectBase`` subclass, or every ``return`` calls one);
- ``handler``: a value or class with the handler marker (``_doeff_is_handler_fn``), a function
  that returns a handler (a factory: its return type is ``ProgramHandler``, a ``Program -> Program``
  callable or a list of handlers, or every ``return`` builds a handler), or a raw handler
  function taking ``(effect, k)``. A factory is called with the least arguments it takes — none,
  or a placeholder for each parameter without a default — with HOME, the XDG dirs and the cwd
  pointed at a scratch dir, and must return a value with the marker, or a list or tuple of them
  (ADR-DOE-API-001 R3); a factory whose call raises keeps the verdict its return type gives;
- ``other``: decided to be neither — a program function (``@do``, ``defk``) or a function whose
  return type is a program, a function read as a factory whose call returns a value without the
  marker, any other function, class, exception, type, constant, macro, console script or entry
  point. A function that returns a program is a program helper (form ``program の helper``) when
  nothing its body reads — and the bodies of its own package's functions it reads, followed
  through — installs a handler (``WithHandler``, a handler value, a handler builder or factory) or
  answers an effect (``Resume``, ``Pass``, ``Transfer`` ...) (ADR-DOE-API-001 R18): a helper stays
  ``other``, and the allowed table (``tests/data/public_api_allowed.json``) allows it by name in
  the group ``許した物 — program の helper``;
- ``unclassified``: what the machine cannot decide — a function with no return type, or one that
  says nothing (``Any``, ``object``, a bare ``Callable``, a list of them), whose ``return``
  statements do not decide it either; a class named like an effect or a handler without being
  one (and a function returning one); a name of a module that cannot be imported.

Each package is imported in its own child process, so one package's import side effects and
failures stay out of the others.

    uv run python tools/public_api_inventory.py --json inventory.json --markdown inventory.md
    uv run python tools/public_api_inventory.py --package doeff-time --json -
"""

from __future__ import annotations

import argparse
import ast
import collections
import contextlib
import enum
import functools
import importlib
import importlib.util
import inspect
import itertools
import json
import os
import re
import subprocess
import sys
import tempfile
import textwrap
import types
import typing
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, cast

import tomllib

ROOT = Path(__file__).resolve().parents[1]

Kind = Literal["effect", "handler", "other", "unclassified"]
KINDS: tuple[Kind, ...] = ("effect", "handler", "other", "unclassified")
KIND_LABELS: dict[Kind, str] = {
    "effect": "effect",
    "handler": "handler",
    "other": "それ以外",
    "unclassified": "未分類",
}
ModuleReadWay = Literal["__all__", "own", "pinned", "text"]
#: What shows a public submodule's row (as ``(console script)`` shows a console script's).
SUBMODULE = "(submodule)"
#: The first line of a module left at a submodule's old path when the submodule moved to ``_<name>``.
SHIM_MARK = re.compile(r"#\s*moved-submodule-shim:\s*([\w.]+)\s*->\s*([\w.]+)")
#: A comment line below a shim's mark that pins the names one old path shows; the lines that follow
#: it starting ``#`` and two or more spaces go on with the same module's names.
SHIM_NAMES_MARK = re.compile(r"#\s*moved-submodule-shim-names\s+([\w.]+):(.*)")
SHIM_PIN_LINE = re.compile(r"#\s*moved-submodule-shim-names\s+[\w.]+:.*|#\s{2,}\S.*")

_MODULE_SUFFIXES = (".py", ".hy", ".pyi")
_EXTENSION_SUFFIXES = (".so", ".pyd")
_INIT_NAMES = ("__init__.py", "__init__.hy", "__init__.pyi")
_PROGRAM_TYPE_NAMES = frozenset({"Program", "Expand", "DoExpr", "DoCtrl", "ProgramBase"})
_PROGRAM_VALUE_TYPE_NAMES = _PROGRAM_TYPE_NAMES | {"Call", "Pure", "Perform"}
_HANDLER_TYPE_NAMES = frozenset({"ProgramHandler", "_InstalledHandler", "Handler"})
#: The forms of a function read as a handler factory (ADR-DOE-API-001 R3): called to check what it returns.
FACTORY_FORMS = frozenset({"handler を返す関数", "handler の組を返す関数"})
#: The form of a function read as a factory whose call returns a value without the handler marker.
NOT_A_FACTORY = "handler を返さない関数"
#: The form of a function that returns a program which only emits effects (ADR-DOE-API-001 R18).
PROGRAM_HELPER = "program の helper"
#: The forms of a function that returns a program and whose body goes past a helper's.
HELPER_INSTALLS = "handler を被せる program を返す関数"
HELPER_ANSWERS = "effect に答える program を返す関数"
#: The form of a public Hy macro's row (a macro module shows only these — ADR-DOE-API-001 R14・R15・R31).
MACRO_FORM = "Hy の macro"
#: The forms of a command's row: a console script and a Rust executable maturin's bin puts (R7).
CONSOLE_SCRIPT = "console script"
RUST_EXECUTABLE = "Rust の実行 file"
# What a helper's body must not read, by name in doeff.program (and by name when read as an attribute).
_INSTALL_NAMES = ("WithHandler", "WithHandlerType", "with_handlers")
_ANSWER_NAMES = ("Resume", "ResumeThrow", "Transfer", "TransferThrow", "Pass")
# What a factory call may write under: pointed at a scratch dir while a factory is called.
_HOME_VARIABLES = ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME")
# The generic collections a function may return a handler stack in.
_COLLECTION_ORIGINS = (list, tuple, frozenset, collections.abc.Sequence, collections.abc.Iterable)
_CONSTANT_TYPES: tuple[type, ...] = (
    str,
    bytes,
    int,
    float,
    complex,
    bool,
    type(None),
    tuple,
    list,
    dict,
    set,
    frozenset,
    Path,
    re.Pattern,
    enum.Enum,
)
_HY_DEFINING_FORM = re.compile(
    r"\s*\((?:setv|defn|defk|deff|defp|defclass|defmacro|defhandler|defeffect|\S*def\S*)\s+(\S+)"
)
_HY_IMPORT_FORM = re.compile(r"\(import\s+([\w.]+)\s*\[([^\]]*)\]")
_HY_TOP_DEFINITION = re.compile(
    r"^\((?:defn|defk|deff|defp|defclass|defmacro|defhandler|setv)\s+([^\s()\[\]]+)", re.MULTILINE
)
# How many wrapper layers (``__wrapped__``, doeff's marks) and re-export hops are followed.
_FOLLOW_DEPTH = 8
_DEFAULT_TIMEOUT_SECONDS = 1800
_MISSING = object()
# The alias a ``type X = ...`` statement makes; the statement and its type come with Python 3.12, so 3.11 has no alias to read.
_TYPE_ALIAS_TYPES: tuple[type, ...] = (typing.TypeAliasType,) if sys.version_info >= (3, 12) else ()


class ExportRow(TypedDict):
    """One name a module (or the package's metadata) shows, with its kind and where it is defined."""

    module: str
    name: str
    kind: Kind
    form: str
    defined_at: str
    reason: str


class MergedRow(ExportRow):
    """An ``ExportRow`` with every module that shows the same name and definition."""

    modules: list[str]


class ModuleRead(TypedDict):
    """How one public module was read: imported (its names by ``__all__`` or ``own``) or as text."""

    name: str
    read: ModuleReadWay
    error: str | None


class PackageInventory(TypedDict):
    """The public API of one package dir (the JSON the child process hands the parent)."""

    dir: str
    distribution: str | None
    import_roots: list[str]
    notes: list[str]
    modules: list[ModuleRead]
    exports: list[ExportRow]
    error: str | None


@dataclass(frozen=True)
class Verdict:
    """The verdict on one name: its kind, its form (what it is) and the reason (what decided it)."""

    kind: Kind
    form: str
    reason: str


@dataclass(frozen=True)
class Place:
    """Where a name is defined: the module, and its source file and line when they are known."""

    module: str
    file: str | None = None
    line: int | None = None


# --- the layout of a package dir (read from files only; the parent process uses this) ---


@dataclass(frozen=True)
class DoeffPart:
    """One of doeff's own parts under ``packages/``: a package only doeff (the repo root package) imports.

    A part is not a subpackage a user imports — a user reads its names from ``doeff`` — so the inventory does
    not count it, as it does not count ``doeff`` itself (ADR-DOE-API-001 R10・R11 — agora card ki-9c326052f21c).
    ``tests/test_public_api_known_violations.py`` checks that no file outside doeff imports its root.
    """

    dir: str
    import_root: str


DOEFF_PARTS: tuple[DoeffPart, ...] = (DoeffPart("doeff-vm", "doeff_vm"),)


@dataclass(frozen=True)
class ImportRoot:
    """A top-level import name a package installs, and the dir holding its source."""

    name: str
    path: Path


@dataclass(frozen=True)
class _Installs:
    """What a build backend's config installs: import roots, Rust executables, notes for the reader."""

    roots: tuple[ImportRoot, ...] = ()
    rust_bins: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class PackageLayout:
    """What a package dir installs, read from its ``pyproject.toml`` (and ``Cargo.toml``)."""

    dir: Path
    distribution: str | None
    roots: tuple[ImportRoot, ...]
    maturin: bool
    scripts: dict[str, str]
    entry_points: dict[str, dict[str, str]]
    rust_bins: tuple[str, ...]
    notes: tuple[str, ...]


def package_dirs(repo: Path = ROOT) -> list[Path]:
    """Every package the inventory covers: each dir under ``packages/`` (not a dot or underscore dir, nor one of
    doeff's own parts — ``DOEFF_PARTS``)."""
    return sorted(
        path
        for path in (repo / "packages").iterdir()
        if path.is_dir()
        and not path.name.startswith((".", "_"))
        and path.name not in {part.dir for part in DOEFF_PARTS}
    )


def _table(data: dict[str, object], *keys: str) -> dict[str, object]:
    """The nested TOML table at ``keys``, so absent config reads as an empty table."""
    current = data
    for key in keys:
        found = current.get(key)
        if not isinstance(found, dict):
            return {}
        current = cast(dict[str, object], found)  # a TOML table: string keys
    return current


def _read_toml(path: Path) -> dict[str, object]:
    """A TOML file as a table (``tomllib`` answers ``dict[str, Any]``)."""
    return dict(tomllib.loads(path.read_text(encoding="utf-8")))


def _cargo_bins(package_dir: Path) -> tuple[str, ...]:
    """The executables a Cargo crate builds: its ``[[bin]]`` names, or the crate name for ``src/main.rs``."""
    cargo = package_dir / "Cargo.toml"
    if not cargo.is_file():
        return ()
    data = _read_toml(cargo)
    bins = data.get("bin")
    if isinstance(bins, list) and bins:
        return tuple(
            str(cast(dict[str, object], entry)["name"]) for entry in bins
        )  # [[bin]] tables
    if (package_dir / "src" / "main.rs").is_file():
        return (str(_table(data, "package")["name"]),)
    return ()


def _setuptools_roots(package_dir: Path, tool: dict[str, object]) -> tuple[ImportRoot, ...]:
    """The packages setuptools discovers: ``packages.find.where``, or ``src`` (src layout) or the dir."""
    where = _table(tool, "setuptools", "packages", "find").get("where")
    if isinstance(where, list):
        bases = [package_dir / str(entry) for entry in where]
    elif (package_dir / "src").is_dir():
        bases = [package_dir / "src"]
    else:
        bases = [package_dir]
    return tuple(
        ImportRoot(child.name, child)
        for base in bases
        for child in sorted(base.iterdir())
        if child.is_dir()
        and child.name.isidentifier()
        and child.name != "tests"
        and any((child / init).is_file() for init in _INIT_NAMES)
    )


def _installs(
    package_dir: Path, distribution: str | None, backend: str, tool: dict[str, object]
) -> _Installs:
    """What the package's build config installs (maturin, hatch, setuptools, or no build at all)."""
    maturin = _table(tool, "maturin")
    hatch_packages = _table(tool, "hatch", "build", "targets", "wheel").get("packages")
    if maturin and maturin.get("bindings") == "bin":
        return _Installs(
            rust_bins=_cargo_bins(package_dir),
            notes=("Rust の実行 file を置く package(Python から import できる module は無い)",),
        )
    if maturin:
        default_module = (distribution or package_dir.name).replace("-", "_")
        top = str(maturin.get("module-name", default_module)).split(".")[0]
        return _Installs(
            roots=(ImportRoot(top, package_dir / str(maturin.get("python-source", ".")) / top),)
        )
    if isinstance(hatch_packages, list):
        paths = [package_dir / str(entry) for entry in hatch_packages]
        return _Installs(
            roots=tuple(ImportRoot(path.name, path) for path in paths if path.name.isidentifier()),
            notes=tuple(
                f"{path.relative_to(package_dir)}: import の名にならない dir(型の stub など)— 数えない"
                for path in paths
                if not path.name.isidentifier()
            ),
        )
    if backend.startswith("setuptools"):
        return _Installs(roots=_setuptools_roots(package_dir, tool))
    if not backend:
        return _Installs(
            notes=(
                "build の設定が無い(uv の package = false)— Python から import できる module は無い",
            )
        )
    return _Installs(notes=(f"build backend {backend} の import の根を読めない",))


def read_layout(package_dir: Path) -> PackageLayout:
    """The import roots, scripts, entry points and executables one package dir installs."""
    pyproject = package_dir / "pyproject.toml"
    if not pyproject.is_file():
        return PackageLayout(
            package_dir,
            None,
            (),
            False,
            {},
            {},
            _cargo_bins(package_dir),
            ("pyproject.toml が無い",),
        )
    data = _read_toml(pyproject)
    project = _table(data, "project")
    tool = _table(data, "tool")
    distribution = str(project["name"]) if "name" in project else None
    backend = str(_table(data, "build-system").get("build-backend", ""))
    installs = _installs(package_dir, distribution, backend, tool)
    return PackageLayout(
        dir=package_dir,
        distribution=distribution,
        roots=installs.roots,
        maturin=bool(_table(tool, "maturin")),
        scripts={name: str(target) for name, target in _table(project, "scripts").items()},
        entry_points={
            group: {
                name: str(target) for name, target in _table(project, "entry-points", group).items()
            }
            for group in _table(project, "entry-points")
        },
        rust_bins=installs.rust_bins,
        notes=installs.notes,
    )


def _module_name(prefix: str, relative: Path) -> str:
    """The import name of a module file or package dir below the package named ``prefix``."""
    *parents, last = relative.parts
    if last.endswith(_EXTENSION_SUFFIXES):
        last = last.split(".")[0]
    elif last.endswith(_MODULE_SUFFIXES):
        last = last.rsplit(".", 1)[0]
    return ".".join([prefix, *parents, last])


def _init_of(directory: Path) -> Path | None:
    """The ``__init__`` file that makes a dir a package (``None`` for a namespace dir)."""
    return next((directory / name for name in _INIT_NAMES if (directory / name).is_file()), None)


def _root_modules(root: ImportRoot) -> Iterator[tuple[str, Path]]:
    """The modules of one import root: the root package, then its public submodules and subpackages."""
    if not root.path.is_dir():
        return
    yield root.name, _init_of(root.path) or root.path
    yield from _modules_below(root.path, root.name, 0)


def _modules_below(base: Path, prefix: str, depth: int) -> Iterator[tuple[str, Path]]:
    """The public modules below the package dir ``base`` (named ``prefix``).

    A moved-submodule shim (``SHIM_MARK``) also stands for the modules below the package it moved
    to, by their old names: they stay importable at the old path until the shim is removed. A shim
    for a namespace dir (no ``__init__`` — doeff-cluster's areas) is walked through like the dir it
    stands for, and is not a module itself.
    """
    for path in sorted(base.rglob("*")):
        relative = path.relative_to(base)
        if any(part.startswith(("_", ".")) for part in relative.parts):
            continue
        init = _init_of(path) if path.is_dir() else None
        if init is not None:
            yield _module_name(prefix, relative), init
        elif path.is_file() and path.name.endswith(_MODULE_SUFFIXES + _EXTENSION_SUFFIXES):
            name = _module_name(prefix, relative)
            moved_to = _shim_package(name, path)
            if moved_to is None or _init_of(moved_to) is not None:
                yield name, path
            if moved_to is not None and depth < _FOLLOW_DEPTH:
                yield from _modules_below(moved_to, name, depth + 1)


def _shim_target(name: str, path: Path) -> Path | None:
    """The package dir or module file a moved-submodule shim stands for (``None`` for any other module).

    A shim's first line is ``# moved-submodule-shim: <old> -> <new>``, its old name is its own and
    the new one is a sibling (the shims the rewriting tool of card ki-299be2ea4a13 writes).
    """
    if path.suffix != ".py":
        return None
    with path.open(encoding="utf-8", errors="replace") as source:
        found = SHIM_MARK.fullmatch(source.readline().strip())
    if found is None or found.group(1) != name:
        return None
    parent, _, last = found.group(2).rpartition(".")
    if parent != name.rpartition(".")[0]:
        return None
    moved_to = path.parent / last
    if moved_to.is_dir():  # a package, or a namespace dir walked through
        return moved_to
    return next(
        (
            path.parent / f"{last}{suffix}"
            for suffix in (".py", ".hy")
            if (path.parent / f"{last}{suffix}").is_file()
        ),
        None,
    )


def _shim_package(name: str, path: Path) -> Path | None:
    """The package dir a moved-submodule shim stands for (``None`` for any other module)."""
    moved_to = _shim_target(name, path)
    return moved_to if moved_to is not None and moved_to.is_dir() else None


def _shim_source(name: str, path: Path) -> Path:
    """The file whose text stands for the module ``name`` at ``path``: the moved module's for a shim."""
    moved_to = _shim_target(name, path)
    if moved_to is None:
        return path
    return (_init_of(moved_to) or path) if moved_to.is_dir() else moved_to


def shim_pins(name: str, path: Path) -> dict[str, tuple[str, ...]]:
    """The names a moved-submodule shim pins, per old module (empty for any other module).

    The pins are the comment lines right below the mark (``SHIM_NAMES_MARK``); a line naming a
    module the shim does not stand for (neither its own name nor one below it) is not read.
    """
    if _shim_target(name, path) is None:
        return {}
    with path.open(encoding="utf-8", errors="replace") as source:
        below_mark = source.read().split("\n")[1:]
    block = list(
        itertools.takewhile(lambda line: SHIM_PIN_LINE.fullmatch(line) is not None, below_mark)
    )
    heads = [index for index, line in enumerate(block) if SHIM_NAMES_MARK.fullmatch(line)]
    return {
        found.group(1): tuple(
            word
            for text in (found.group(2), *(line[1:] for line in block[head + 1 : end]))
            for word in text.split()
        )
        for head, end in itertools.pairwise([*heads, len(block)])
        if (found := SHIM_NAMES_MARK.fullmatch(block[head])) is not None
        and (found.group(1) == name or found.group(1).startswith(f"{name}."))
    }


def pinned_names(layout: PackageLayout) -> dict[str, tuple[str, ...]]:
    """The names every shim of the package pins, per old module (``shim_pins`` of each public module)."""
    return {
        module: names
        for name, path in public_modules(layout)
        for module, names in shim_pins(name, path).items()
    }


def shim_modules(layout: PackageLayout) -> list[tuple[str, Path]]:
    """The package's moved-submodule shims: the old name and the shim's file."""
    return [
        (name, path)
        for name, path in public_modules(layout)
        if _shim_target(name, path) is not None
    ]


def unpinned_shim_modules(layout: PackageLayout) -> list[str]:
    """The old modules a shim stands for that its pins do not list (each shows every name it has)."""
    shims = [name for name, _path in shim_modules(layout)]
    pinned = pinned_names(layout)
    return [
        name
        for name, _path in public_modules(layout)
        if name not in pinned and any(name == shim or name.startswith(f"{shim}.") for shim in shims)
    ]


def public_modules(layout: PackageLayout) -> list[tuple[str, Path]]:
    """Every module a user can import from the package: the roots and their public submodules.

    A submodule is public when no part of its path below the root starts with ``_`` (``__init__``
    stands for its package). A dir is a module when it holds an ``__init__``; a dir without one is
    walked through (its modules are importable through the namespace package). A module with both
    a source file and a ``.pyi`` stub is listed once, by its source.
    """
    candidates = [module for root in layout.roots for module in _root_modules(root)]
    # Stubs sort before sources of the same name, so the source is the one the dict keeps.
    ordered = sorted(candidates, key=lambda module: (module[0], module[1].suffix != ".pyi"))
    return sorted(dict(ordered).items())


# --- typing the names of imported modules (the child process uses this) ---


def _static(obj: object, name: str) -> object:
    """An attribute read without running descriptors or ``__getattr__`` (``_MISSING`` when absent)."""
    return inspect.getattr_static(obj, name, _MISSING)


def _is_handler_marked(obj: object) -> bool:
    """Whether a value carries doeff's handler marker (what every installer has)."""
    return _static(obj, "_doeff_is_handler_fn") is True


def _unwrapped_once(obj: object) -> object:
    """The value one doeff or functools wrapper holds (``_MISSING`` when ``obj`` wraps nothing)."""
    if isinstance(obj, functools.partial):
        return obj.func
    if isinstance(obj, types.MethodType):
        return obj.__func__
    if isinstance(obj, type):
        return _MISSING
    inner = (
        _static(obj, attribute)
        for attribute in ("__doeff_handler_data__", "__doeff_generator_function__", "__wrapped__")
    )
    return next((value for value in inner if value is not _MISSING), _MISSING)


def _definition(obj: object) -> object:
    """The function or class a value comes from, through doeff's and functools' wrappers."""
    current = obj
    for _ in range(_FOLLOW_DEPTH):
        inner = _unwrapped_once(current)
        if inner is _MISSING or inner is current:
            break
        current = inner
    return current


def _is_program_function(obj: object) -> bool:
    """Whether calling a value builds a program (a ``@do`` / ``defk`` function)."""
    current = obj
    for _ in range(_FOLLOW_DEPTH):
        if _static(current, "__doeff_generator_function__") is not _MISSING:
            return True
        inner = _static(current, "__wrapped__")
        if inner is _MISSING:
            return type(current).__name__ in {"DoFunction", "KleisliProgram"}
        current = inner
    return False


def _doeff_hy_handler_types() -> tuple[object, ...]:
    """doeff-hy's ``Handler`` type (a ``defhandler`` handler's type), when doeff-hy is installed."""
    if importlib.util.find_spec("doeff_hy") is None:
        return ()
    static_types = importlib.import_module("doeff_hy._static_types")
    return (static_types.__dict__["Handler"],) if "Handler" in static_types.__dict__ else ()


class _Classifier:
    """Types the values a package exports (holds doeff's types, imported once per child)."""

    def __init__(self) -> None:
        from doeff import EffectBase

        self.effect_base: type = EffectBase
        program_module = importlib.import_module("doeff.program")
        self.handler_builders: tuple[object, ...] = tuple(
            program_module.__dict__[name]
            for name in ("handler", "stacked_handlers", "_named_handler", "_rust_handler_installer")
            if name in program_module.__dict__
        )
        self.handler_types: tuple[object, ...] = (
            *(
                program_module.__dict__[name]
                for name in ("ProgramHandler", "_InstalledHandler")
                if name in program_module.__dict__
            ),
            *_doeff_hy_handler_types(),
        )
        # What makes a program function's body go past a helper's: a value of doeff.program, and the form it gives.
        self.helper_breaches: tuple[tuple[object, str], ...] = tuple(
            (program_module.__dict__[name], form)
            for names, form in ((_INSTALL_NAMES, HELPER_INSTALLS), (_ANSWER_NAMES, HELPER_ANSWERS))
            for name in names
            if name in program_module.__dict__
        )
        # The calls made so far, by the factory's id (a definition shown by several modules is called once).
        self._factory_calls: dict[int, _FactoryCall] = {}

    def is_effect_class(self, obj: object) -> bool:
        """Whether a value is an effect type (a subclass of ``EffectBase``)."""
        return isinstance(obj, type) and issubclass(obj, self.effect_base)

    def classify(self, obj: object, name: str) -> Verdict:
        """The inventory's verdict on one exported value: its kind, form and reason."""
        if isinstance(obj, type):
            return self._classify_class(obj, name)
        if callable(obj) and _is_function_like(obj) and not _is_handler_marked(obj):
            return self._classify_function(obj)
        value_type = type(obj)
        type_name = value_type.__name__
        from_doeff = value_type.__module__.startswith(("doeff_vm", "doeff."))
        rules: list[tuple[bool, Verdict]] = [
            (
                isinstance(obj, self.effect_base),
                Verdict("effect", "effect の値", f"{type_name} の値(EffectBase の子)"),
            ),
            (
                _is_handler_marked(obj),
                Verdict("handler", "handler", "handler の印(_doeff_is_handler_fn)を持つ"),
            ),
            (isinstance(obj, types.ModuleType), Verdict("other", "module", "module")),
            (
                from_doeff and type_name.endswith("Handler"),
                Verdict("handler", "VM の handler の値", f"{type_name} の値"),
            ),
            (_is_type_like(obj), Verdict("other", "型", f"型({type_name})")),
            (isinstance(obj, _CONSTANT_TYPES), Verdict("other", "定数", f"定数({type_name})")),
            (
                from_doeff and type_name in _PROGRAM_VALUE_TYPE_NAMES,
                Verdict("other", "program の値", f"{type_name} の値"),
            ),
            (callable(obj), Verdict("other", "呼べる値", f"{type_name} の値(呼べる)")),
        ]
        fallback = Verdict("other", "値", f"{type_name} の値")
        return next((verdict for matched, verdict in rules if matched), fallback)

    def _classify_class(self, cls: type, name: str) -> Verdict:
        """The verdict on a class: an effect type, a handler class, or by what it is named."""
        names = (name, cls.__name__)
        rules: list[tuple[bool, Verdict]] = [
            (
                issubclass(cls, self.effect_base),
                Verdict("effect", "effect の class", "EffectBase の子の class"),
            ),
            (
                _is_handler_marked(cls),
                Verdict(
                    "handler", "handler の class", "handler の印(_doeff_is_handler_fn)を持つ class"
                ),
            ),
            (
                issubclass(cls, BaseException),
                Verdict("other", "例外の class", "BaseException の子"),
            ),
            (
                any(each.endswith("Effect") for each in names),
                Verdict("unclassified", "class", "名が Effect で終わるが EffectBase の子でない"),
            ),
            (
                any(each.endswith("Handler") for each in names),
                Verdict("unclassified", "class", "名が Handler で終わるが handler の印が無い"),
            ),
            (issubclass(cls, enum.Enum), Verdict("other", "Enum", "Enum の子")),
            (_static(cls, "_is_protocol") is True, Verdict("other", "Protocol", "typing.Protocol")),
        ]
        return next(
            (verdict for matched, verdict in rules if matched), Verdict("other", "class", "class")
        )

    def _classify_function(self, obj: object) -> Verdict:
        """The verdict on a function: its parameters, then a program function, its return type, returns.

        A return type that says nothing about the value (``Any``, ``object``, a bare ``Callable``,
        a list of them) leaves the verdict to the ``return`` statements, and to unclassified when
        they do not decide it either.
        """
        verdict = self._read_function(obj)
        return self._called_factory(obj, verdict) if verdict.form in FACTORY_FORMS else verdict

    def _read_function(self, obj: object) -> Verdict:
        """The verdict read from a function's parameters, marks, return type and ``return`` statements."""
        function = _definition(obj)
        if _takes_effect_and_k(function):
            return Verdict(
                "handler", "素の handler の関数", "引数が (effect, k)(handler の印は無い)"
            )
        if _is_program_function(obj):
            return self._program_verdict(
                function,
                Verdict("other", "program 関数", "@do / defk の関数(呼ぶと program を作る)"),
            )
        annotation = _return_annotation(function)
        decided = self._annotation_kind(annotation) if annotation is not _MISSING else None
        if decided is not None and decided.form == "program を返す関数":
            return self._program_verdict(function, decided)
        if decided is not None:
            return decided
        if annotation is _MISSING or self._is_vague(annotation):
            unknown = (
                "戻りの型が無く、return の形からも決まらない"
                if annotation is _MISSING
                else f"戻りの型 {_annotation_text(annotation)} では決まらず、return の形からも決まらない"
            )
            return self._returns_kind(function) or Verdict("unclassified", "関数", unknown)
        return Verdict("other", "関数", f"戻りの型 {_annotation_text(annotation)}")

    def _called_factory(self, obj: object, read: Verdict) -> Verdict:
        """The verdict on a function read as a factory, checked by calling it (ADR-DOE-API-001 R3).

        Called with the least arguments it takes: none when every parameter has a default, else a
        placeholder for each parameter without one (a factory that only keeps its arguments for the
        handler builds the handler; one that reads them when called fails). A call that fails keeps the
        verdict the return type or the returns gave (the failure may come from the placeholder or the
        machine — a missing executable — not from the factory's shape). One definition shown by
        several modules is called once.
        """
        needed = _required_parameters(obj)
        if needed is None or not callable(obj):
            return Verdict(read.kind, read.form, f"{read.reason}・引数を読めず、呼んでいない")
        how = (
            f"要る引数 {'・'.join(parameter.name for parameter in needed)} に置き物を渡して"
            if needed
            else "引数なしで"
        )
        key = id(obj)
        if key not in self._factory_calls:
            self._factory_calls[key] = _FactoryCall(obj, _call_factory(obj, needed))
        outcome = self._factory_calls[key].outcome
        if isinstance(outcome, _CallFailure):
            return Verdict(read.kind, read.form, f"{read.reason}・{how}呼ぶと {outcome.error}")
        if _returns_handlers(outcome, many=read.form == "handler の組を返す関数"):
            return Verdict(read.kind, read.form, f"{read.reason}・{how}呼ぶと handler を返す")
        return Verdict(
            "other",
            NOT_A_FACTORY,
            f"{read.reason} だが、{how}呼ぶと {type(outcome).__name__} を返す"
            "(handler の印 _doeff_is_handler_fn が無い)",
        )

    def _program_verdict(self, function: object, read: Verdict) -> Verdict:
        """The verdict on a function that returns a program: a program helper or not (ADR-DOE-API-001 R18).

        A helper's program only emits effects: nothing its body reads — and the bodies of the
        functions of its own package it reads, followed through — installs a handler or answers an
        effect. A body that cannot be read (not a Python function) keeps the verdict ``read`` gave.
        """
        if not isinstance(function, types.FunctionType):
            return Verdict(
                read.kind, read.form, f"{read.reason}・中を読めず、helper と確かめていない"
            )
        breaches = (
            _Breach(form, current.__qualname__, name)
            for current in _package_bodies(function)
            for name in sorted(_code_names(current.__code__))
            if (form := self._breach_form(name, current.__globals__.get(name, _MISSING)))
            is not None
        )
        breach = next(breaches, None)
        if breach is None:
            return Verdict(
                "other", PROGRAM_HELPER, f"{read.reason}・中で handler を被せず、effect に答えない"
            )
        return Verdict(
            "other", breach.form, f"{read.reason}・{breach.where} が {breach.name} を読む"
        )

    def _breach_form(self, name: str, value: object) -> str | None:
        """The form one name a body reads gives past a helper's (``None`` when a helper may read it).

        A global is read by its value — doeff's ``WithHandler`` and handler control (``Resume``,
        ``Pass`` ...), a handler value, builder or factory; an attribute (``doeff.Resume``) by its name.
        """
        if value is _MISSING:
            if name in _INSTALL_NAMES:
                return HELPER_INSTALLS
            return HELPER_ANSWERS if name in _ANSWER_NAMES else None
        listed = (form for target, form in self.helper_breaches if value is target)
        found = next(listed, None)
        if found is not None:
            return found
        return HELPER_INSTALLS if self._called_kind(value) == "handler" else None

    def _is_vague(self, annotation: object) -> bool:
        """Whether a return type says nothing about effect or handler (``Any``, a ``Callable``, a list of them)."""
        if (
            annotation in (typing.Any, object, list, tuple)
            or annotation is collections.abc.Callable
        ):
            return True
        origin = typing.get_origin(annotation)
        if origin is collections.abc.Callable:
            return True
        if origin in _COLLECTION_ORIGINS:
            return any(
                self._is_vague(arg) for arg in typing.get_args(annotation) if arg is not Ellipsis
            )
        return False

    def _annotation_kind(self, annotation: object) -> Verdict | None:
        """The verdict a return type gives (``None`` when it says neither effect, handler nor program)."""
        if isinstance(annotation, str):
            return _named_annotation_kind(annotation)
        text = _annotation_text(annotation)
        origin = typing.get_origin(annotation)
        base = origin if origin is not None else annotation
        rules: list[tuple[bool, Verdict]] = [
            (
                self._is_effect_type(annotation),
                Verdict("effect", "effect を作る関数", f"戻りの型 {text}(EffectBase の子)"),
            ),
            (
                self._is_handler_type(annotation),
                Verdict("handler", "handler を返す関数", f"戻りの型 {text}"),
            ),
            (
                self._is_handler_collection(annotation),
                Verdict("handler", "handler の組を返す関数", f"戻りの型 {text}"),
            ),
            (
                isinstance(base, type) and base.__name__.endswith("Handler"),
                Verdict(
                    "unclassified",
                    "関数",
                    f"戻りの型 {text} の名は Handler で終わるが handler の印が無い",
                ),
            ),
            (
                _is_program_type(annotation),
                Verdict("other", "program を返す関数", f"戻りの型 {text}"),
            ),
        ]
        return next((verdict for matched, verdict in rules if matched), None)

    def _is_handler_type(self, annotation: object) -> bool:
        """Whether a return type is a handler: doeff's handler types, a marked class, or ``Program -> Program``."""
        if any(annotation is handler_type for handler_type in self.handler_types):
            return True
        if isinstance(annotation, type) and _is_handler_marked(annotation):
            return True
        if typing.get_origin(annotation) is not collections.abc.Callable:
            return False
        parameters, result = typing.get_args(annotation)
        return (
            isinstance(parameters, list)
            and len(parameters) == 1
            and _is_program_type(parameters[0])
            and _is_program_type(result)
        )

    def _is_handler_collection(self, annotation: object) -> bool:
        """Whether a return type is a list or tuple of handlers (a stack to install)."""
        if typing.get_origin(annotation) not in _COLLECTION_ORIGINS:
            return False
        members = [arg for arg in typing.get_args(annotation) if arg is not Ellipsis]
        return bool(members) and all(self._is_handler_type(arg) for arg in members)

    def _is_effect_type(self, annotation: object) -> bool:
        """Whether a return type is an effect type: the class, a generic of it, or a union of them."""
        if self.is_effect_class(annotation):
            return True
        origin = typing.get_origin(annotation)
        if origin in (typing.Union, types.UnionType):
            members = [arg for arg in typing.get_args(annotation) if arg is not type(None)]
            return bool(members) and all(self._is_effect_type(arg) for arg in members)
        return origin is not None and self.is_effect_class(origin)

    def _returns_kind(self, function: object) -> Verdict | None:
        """The verdict every ``return`` of a Python function agrees on (``None`` when they do not)."""
        node = _function_node(function)
        if node is None or not isinstance(function, types.FunctionType):
            return None
        returned = [statement.value for statement in _own_returns(node)]
        if all(value is None for value in returned):
            return Verdict("other", "関数", "値を返さない")
        nested = {
            child.name: child
            for child in ast.walk(node)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node
        }
        kinds = {
            self._returned_kind(value, function.__globals__, nested)
            for value in returned
            if value is not None
        }
        if kinds == {"effect"}:
            return Verdict("effect", "effect を作る関数", "return が effect の class を呼ぶ")
        if kinds == {"handler"}:
            return Verdict("handler", "handler を返す関数", "return が handler を作る")
        return None

    def _returned_kind(
        self,
        value: ast.expr,
        namespace: dict[str, object],
        nested: dict[str, ast.FunctionDef | ast.AsyncFunctionDef],
    ) -> Kind | None:
        """Whether one returned expression is an effect or a handler, read with the module's globals."""
        if isinstance(value, ast.Call):
            return self._called_kind(_resolve(value.func, namespace))
        if isinstance(value, ast.Name) and value.id in nested:
            definition = nested[value.id]
            marked = any(_decorator_name(item) == "handler" for item in definition.decorator_list)
            return "handler" if marked or _def_takes_effect_and_k(definition) else None
        target = _resolve(value, namespace)
        if _is_handler_marked(target):
            return "handler"
        return "effect" if isinstance(target, self.effect_base) else None

    def _called_kind(self, target: object) -> Kind | None:
        """Whether calling ``target`` makes an effect or a handler (an effect class, a handler builder)."""
        if self.is_effect_class(target):
            return "effect"
        if any(target is builder for builder in self.handler_builders) or _is_handler_marked(
            target
        ):
            return "handler"
        if target is _MISSING or not callable(target) or isinstance(target, type):
            return None
        annotation = _return_annotation(_definition(target))
        decided = self._annotation_kind(annotation) if annotation is not _MISSING else None
        return (
            decided.kind if decided is not None and decided.kind in ("effect", "handler") else None
        )


def _is_program_type(annotation: object) -> bool:
    """Whether a type is doeff's program type (``Program``, ``DoExpr`` ...), a generic of it or a union of them."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return all(_is_program_type(arg) for arg in typing.get_args(annotation))
    base = origin if origin is not None else annotation
    return isinstance(base, type) and base.__name__ in _PROGRAM_TYPE_NAMES


def _named_annotation_kind(annotation: str) -> Verdict:
    """The verdict of a return type left as text (one the module's globals cannot evaluate)."""
    head = annotation.split("[", 1)[0].strip().rsplit(".", 1)[-1]
    if head in _HANDLER_TYPE_NAMES:
        return Verdict("handler", "handler を返す関数", f"戻りの型 {annotation}")
    if head in _PROGRAM_TYPE_NAMES:
        return Verdict("other", "program を返す関数", f"戻りの型 {annotation}")
    return Verdict("unclassified", "関数", f"戻りの型 {annotation} を解けない")


def _is_function_like(obj: object) -> bool:
    """Whether a callable value is a function (so its return type and body can be read)."""
    if isinstance(obj, functools.partial):
        return True
    return isinstance(
        _definition(obj), (types.FunctionType, types.BuiltinFunctionType, types.MethodType)
    )


def _is_type_like(obj: object) -> bool:
    """Whether a value is a typing construct (``TypeVar``, alias, union, special form)."""
    typing_kinds = (
        typing.TypeVar,
        typing.ParamSpec,
        *_TYPE_ALIAS_TYPES,
        types.UnionType,
        types.GenericAlias,
    )
    return isinstance(obj, typing_kinds) or type(obj).__module__ == "typing"


def _return_annotation(function: object) -> object:
    """A function's return type, evaluated when it can be (its text when not; ``_MISSING`` when absent)."""
    if not callable(function):
        return _MISSING
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError, NameError):
        return _MISSING
    annotation = signature.return_annotation
    if annotation is inspect.Signature.empty:
        return _MISSING
    if isinstance(annotation, str) and isinstance(function, types.FunctionType):
        try:
            return eval(annotation, function.__globals__)  # the module's own annotation text
        except (NameError, AttributeError, SyntaxError, TypeError):
            return annotation
    return annotation


def _annotation_text(annotation: object) -> str:
    """A return type as a reader writes it, for the row's reason."""
    if isinstance(annotation, str):
        return annotation
    if isinstance(annotation, type):
        return annotation.__qualname__
    return repr(annotation).replace("typing.", "")


def _takes_effect_and_k(function: object) -> bool:
    """Whether a function is a raw handler: two required positional parameters, the second ``k``."""
    if not isinstance(function, types.FunctionType):
        return False
    required = [
        parameter
        for parameter in inspect.signature(function).parameters.values()
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        and parameter.default is parameter.empty
    ]
    return len(required) == 2 and required[1].name == "k"


@dataclass(frozen=True)
class _Breach:
    """What takes a program function's body past a helper's: the form it gives, who reads it, the name read."""

    form: str
    where: str
    name: str


def _code_names(code: types.CodeType) -> frozenset[str]:
    """The global and attribute names a code object reads, with those of the code defined inside it."""
    inner = (
        _code_names(constant) for constant in code.co_consts if isinstance(constant, types.CodeType)
    )
    return frozenset(code.co_names).union(*inner)


def _read_functions(function: types.FunctionType, package: str) -> Iterator[types.FunctionType]:
    """The functions of ``package`` (the top-level import name) a function's body reads by a global name."""
    for name in sorted(_code_names(function.__code__)):
        value = function.__globals__.get(name, _MISSING)
        inner = _definition(value) if value is not _MISSING else _MISSING
        if (
            isinstance(inner, types.FunctionType)
            and (inner.__module__ or "").split(".")[0] == package
        ):
            yield inner


def _first_each(functions: Sequence[types.FunctionType]) -> tuple[types.FunctionType, ...]:
    """``functions`` without the repeats (by identity), in the order first seen."""
    return tuple(
        function
        for index, function in enumerate(functions)
        if all(function is not earlier for earlier in functions[:index])
    )


def _package_bodies(function: types.FunctionType) -> tuple[types.FunctionType, ...]:
    """A function and the functions of its own package its body reads, followed through (each once)."""
    package = (function.__module__ or "").split(".")[0]
    reached: tuple[types.FunctionType, ...] = (function,)
    frontier = reached
    while frontier:
        read = _first_each(
            [each for current in frontier for each in _read_functions(current, package)]
        )
        frontier = tuple(each for each in read if all(each is not seen for seen in reached))
        reached = (*reached, *frontier)
    return reached


@dataclass(frozen=True)
class _CallFailure:
    """A factory call that raised: the error, for the row's reason."""

    error: str


@dataclass(frozen=True)
class _FactoryCall:
    """One factory call: the factory (held so its id is not reused) and what the call gave."""

    factory: object
    outcome: object


class _Placeholder:
    """What a factory call passes for a parameter without a default: no value, only a stand-in."""

    def __repr__(self) -> str:
        return "<public_api_inventory の置き物の引数>"


def _required_parameters(function: object) -> list[inspect.Parameter] | None:
    """The parameters a call must pass (no default, not ``*args`` / ``**kwargs``); ``None`` when unreadable."""
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError, NameError):
        return None
    variadic = (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    return [
        parameter
        for parameter in parameters
        if parameter.default is parameter.empty and parameter.kind not in variadic
    ]


@contextlib.contextmanager
def _scratch_home() -> Iterator[None]:
    """HOME, the XDG dirs and the cwd pointed at a scratch dir, for one factory call.

    A factory may make its state dir when called (doeff-agentic's index, doeff-conductor's
    workspaces); it makes it in the scratch dir, not in the home of whoever lists the API. A path
    a module computed when imported is not moved — the call keeps writing there.
    """
    saved = {name: os.environ.get(name) for name in _HOME_VARIABLES}
    cwd = os.getcwd()
    with tempfile.TemporaryDirectory(
        prefix="public-api-call-", ignore_cleanup_errors=True
    ) as scratch:
        os.environ["HOME"] = scratch
        for name in _HOME_VARIABLES[1:]:
            os.environ.pop(name, None)
        os.chdir(scratch)
        try:
            yield
        finally:
            os.chdir(cwd)
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value


def _call_factory(factory: Callable[..., object], needed: list[inspect.Parameter]) -> object:
    """What calling a factory with a placeholder for each needed parameter gives (``_CallFailure`` when it raises)."""
    positional = [
        _Placeholder() for parameter in needed if parameter.kind is parameter.POSITIONAL_ONLY
    ]
    keywords = {
        parameter.name: _Placeholder()
        for parameter in needed
        if parameter.kind is not parameter.POSITIONAL_ONLY
    }
    try:
        with _scratch_home(), contextlib.redirect_stdout(sys.stderr):
            return factory(*positional, **keywords)
    except (Exception, SystemExit) as error:  # the failure is written into the row, not dropped
        return _CallFailure(f"{type(error).__name__}: {error}".splitlines()[0][:300])


def _returns_handlers(value: object, *, many: bool) -> bool:
    """Whether a factory's value is a handler (``many``: a list or tuple of handlers, empty or not)."""
    if not many:
        return _is_handler_marked(value)
    if not isinstance(value, (list, tuple)):
        return False
    members: Sequence[object] = value
    return all(_is_handler_marked(member) for member in members)


def _def_takes_effect_and_k(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """``_takes_effect_and_k`` for a ``def`` read from source (a handler body defined inside a factory)."""
    positional = [*node.args.posonlyargs, *node.args.args]
    required = positional[: len(positional) - len(node.args.defaults)]
    return len(required) == 2 and required[1].arg == "k"


def _function_node(function: object) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """The ``def`` of a Python function, parsed from its source (``None`` for Hy and native code)."""
    if not isinstance(function, types.FunctionType) or not function.__code__.co_filename.endswith(
        ".py"
    ):
        return None
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    except (OSError, TypeError, SyntaxError):
        return None
    first = tree.body[0] if tree.body else None
    return first if isinstance(first, (ast.FunctionDef, ast.AsyncFunctionDef)) else None


def _own_returns(node: ast.AST) -> Iterator[ast.Return]:
    """The ``return`` statements of a function, not those of the functions and classes inside it."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.Return):
            yield child
        elif not isinstance(
            child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
        ):
            yield from _own_returns(child)


def _decorator_name(decorator: ast.expr) -> str | None:
    """The last name of a decorator (``@handler``, ``@doeff.handler``, ``@handler(...)``)."""
    if isinstance(decorator, ast.Call):
        return _decorator_name(decorator.func)
    if isinstance(decorator, ast.Name):
        return decorator.id
    if isinstance(decorator, ast.Attribute):
        return decorator.attr
    return None


def _resolve(expression: ast.expr, namespace: dict[str, object]) -> object:
    """The value a name or dotted attribute refers to in a module's globals (``_MISSING`` otherwise)."""
    if isinstance(expression, ast.Name):
        if expression.id in namespace:
            return namespace[expression.id]
        builtins = namespace.get("__builtins__")
        if isinstance(builtins, dict) and expression.id in builtins:
            return cast(dict[str, object], builtins)[expression.id]  # the module's builtins table
        return _MISSING
    if isinstance(expression, ast.Attribute):
        owner = _resolve(expression.value, namespace)
        return _MISSING if owner is _MISSING else _static(owner, expression.attr)
    return _MISSING


# --- where a name is defined ---


@functools.cache
def _source_text(file: str) -> str | None:
    """A source file's text, read once per child (``None`` when it cannot be read)."""
    try:
        return Path(file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


@functools.cache
def _source_tree(file: str) -> ast.Module | None:
    """A Python file's syntax tree, parsed once per child (``None`` when it does not parse)."""
    text = _source_text(file)
    try:
        return ast.parse(text) if text is not None else None
    except SyntaxError:
        return None


def _module_file(module_name: str) -> str | None:
    """The source file of an imported module (``None`` for one not imported or without a file)."""
    module = sys.modules.get(module_name)
    file = module.__dict__.get("__file__") if module is not None else None
    return file if isinstance(file, str) else None


@dataclass(frozen=True)
class _Sources:
    """Finds where a plain value is bound, and tells the package's own modules from the others."""

    roots: tuple[str, ...]

    def origin(self, module_name: str, name: str, depth: int = 0) -> Place:
        """Where a plain value is bound, following ``from x import name`` to the module that binds it.

        A name the module neither binds nor imports by name is followed through its
        ``from x import *`` statements (a module that only re-shows another's names, as the old path
        of a submodule renamed to ``_<name>`` does), so both paths read the same definition place.
        """
        file = _module_file(module_name)
        if file is None or depth > _FOLLOW_DEPTH:
            return Place(module_name, file)
        if file.endswith(".hy"):
            return self._hy_origin(module_name, file, name, depth)
        tree = _source_tree(file) if file.endswith(".py") else None
        statements = list(_top_statements(tree.body)) if tree is not None else []
        here = Place(module_name, file)
        found = (self._statement_origin(statement, here, name, depth) for statement in statements)
        starred = (self._star_origin(statement, here, name, depth) for statement in statements)
        return next((place for place in itertools.chain(found, starred) if place is not None), here)

    def _statement_origin(
        self, statement: ast.stmt, module: Place, name: str, depth: int
    ) -> Place | None:
        """``origin`` when one module-level statement binds ``name`` (``None`` when it does not)."""
        if _binds(statement, name):
            return Place(module.module, module.file, statement.lineno)
        if not isinstance(statement, ast.ImportFrom) or module.file is None:
            return None
        for alias in statement.names:
            if (alias.asname or alias.name) == name and alias.name != "*":
                source = _absolute_module(module.module, module.file, statement)
                return (
                    self.origin(source, alias.name, depth + 1)
                    if source in sys.modules
                    else Place(source)
                )
        return None

    def _star_origin(
        self, statement: ast.stmt, module: Place, name: str, depth: int
    ) -> Place | None:
        """``origin`` through a ``from x import *`` whose module has ``name`` (``None`` otherwise)."""
        if not isinstance(statement, ast.ImportFrom) or module.file is None:
            return None
        if [alias.name for alias in statement.names] != ["*"]:
            return None
        source = _absolute_module(module.module, module.file, statement)
        source_module = sys.modules.get(source)
        if source_module is None or name not in source_module.__dict__:
            return None
        return self.origin(source, name, depth + 1)

    def _hy_origin(self, module_name: str, file: str, name: str, depth: int) -> Place:
        """``origin`` for a Hy module, read from its text (its defining forms and ``import`` forms)."""
        text = _source_text(file)
        if text is None:
            return Place(module_name, file)
        hy_names = {name, name.replace("_", "-")}
        for number, line in enumerate(text.splitlines(), start=1):
            match = _HY_DEFINING_FORM.match(line)
            if match and match.group(1).rstrip(")") in hy_names:
                return Place(module_name, file, number)
        for match in _HY_IMPORT_FORM.finditer(text):
            if hy_names & set(match.group(2).split()):
                source = match.group(1)
                return (
                    self.origin(source, name, depth + 1) if source in sys.modules else Place(source)
                )
        return Place(module_name, file)

    def is_own_module(self, module_name: str) -> bool:
        """Whether a module belongs to the package being read (one of its import roots)."""
        return module_name.split(".")[0] in self.roots


def _top_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Module-level statements, including those under ``if`` / ``try`` / ``with``."""
    for statement in body:
        if isinstance(statement, ast.If):
            yield from _top_statements(statement.body)
            yield from _top_statements(statement.orelse)
        elif isinstance(statement, ast.Try):
            yield from _top_statements(statement.body)
            for handler in statement.handlers:
                yield from _top_statements(handler.body)
            yield from _top_statements(statement.orelse)
            yield from _top_statements(statement.finalbody)
        elif isinstance(statement, ast.With):
            yield from _top_statements(statement.body)
        else:
            yield statement


def _statement_names(statement: ast.stmt) -> tuple[str, ...]:
    """The names a module-level statement binds (def, class, assignment, type alias)."""
    if isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return (statement.name,)
    if isinstance(statement, ast.Assign):
        return tuple(name for target in statement.targets for name in _target_names(target))
    if isinstance(statement, (ast.AnnAssign, ast.AugAssign)):
        return _target_names(statement.target)
    # ``type X = ...`` (ast.TypeAlias) comes with Python 3.12; 3.11 has no such statement to read.
    if (
        sys.version_info >= (3, 12)
        and isinstance(statement, ast.TypeAlias)
        and isinstance(statement.name, ast.Name)
    ):
        return (statement.name.id,)
    return ()


def _target_names(target: ast.expr) -> tuple[str, ...]:
    """The names an assignment target binds (a name, or a tuple or list of them)."""
    if isinstance(target, ast.Name):
        return (target.id,)
    if isinstance(target, (ast.Tuple, ast.List)):
        return tuple(name for element in target.elts for name in _target_names(element))
    return ()


def _binds(statement: ast.stmt, name: str) -> bool:
    """Whether a module-level statement binds ``name``."""
    return name in _statement_names(statement)


def _absolute_module(module_name: str, file: str, statement: ast.ImportFrom) -> str:
    """The module a (possibly relative) ``from ... import`` reads from."""
    if statement.level == 0:
        return statement.module or ""
    package_parts = module_name.split(".")
    if not Path(file).name.startswith("__init__."):
        package_parts = package_parts[:-1]
    base = package_parts[: len(package_parts) - (statement.level - 1)]
    return ".".join([*base, statement.module] if statement.module else base)


def _place_text(place: Place) -> str:
    """A repo-relative ``path:line`` for the table, or the module name for code outside the repo."""
    if place.file is None:
        return place.module
    path = Path(place.file).resolve()
    if path.suffix in _EXTENSION_SUFFIXES:
        return f"{place.module}(native)"
    if path.is_relative_to(ROOT) and not path.is_relative_to(ROOT / ".venv"):
        relative = path.relative_to(ROOT).as_posix()
        return f"{relative}:{place.line}" if place.line is not None else relative
    return place.module


def _code_place(target: object) -> Place | None:
    """Where a function or class is defined (``None`` for a value that is neither)."""
    if isinstance(target, types.FunctionType):
        return Place(target.__module__, target.__code__.co_filename, target.__code__.co_firstlineno)
    if isinstance(target, type):
        line = _static(target, "__firstlineno__")
        return Place(
            target.__module__,
            _module_file(target.__module__),
            line if isinstance(line, int) else _class_line(target),
        )
    return None


def _class_line(cls: type) -> int | None:
    """Where a class's definition starts, read from its module's source.

    ``__firstlineno__`` comes with Python 3.13; before it, ``inspect`` finds the ``class`` statement by
    its qualified name in the source (``None`` when there is no source, as for a native class).
    """
    try:
        return inspect.getsourcelines(cls)[1]
    except (OSError, TypeError):
        return None


# --- one package, in the child process ---


def _install_hy() -> None:
    """Let ``.hy`` modules be imported (Hy's import hook)."""
    if importlib.util.find_spec("hy") is not None:
        importlib.import_module("hy")


def _put_roots_on_path(layout: PackageLayout) -> None:
    """Import a package from its source in the checkout, the code the inventory lists.

    A built copy installed in the venv (a non-editable install, as in CI's fresh venv) would put the
    venv's copy in the rows' ``defined_at`` and list the installed version instead of the checkout.
    A maturin package needs its built native module, so its source dir is never put first: it goes
    last, where an installed build wins and a pure-Python front end that loads its native module
    only on use (doeff-effect-analyzer — not installed in the dev env) is read from the checkout
    instead of its text (card ki-593f9f44c501 — read as text, its effects and handler were
    unclassified).
    """
    places = [str(root.path.parent) for root in layout.roots]
    if layout.maturin:
        sys.path.extend(place for place in places if place not in sys.path)
        return
    sys.path[:0] = places


@dataclass(frozen=True)
class _LazyFailure:
    """A name a module-level ``__getattr__`` could not make: the error, to report in the row."""

    error: str


def _module_value(module: types.ModuleType, name: str) -> object:
    """A module's value for a name, made on demand when the module has a ``__getattr__``.

    ``_MISSING`` when the module has neither the name nor a ``__getattr__``; a ``_LazyFailure`` when
    its ``__getattr__`` fails for the name.
    """
    if name in module.__dict__:
        return module.__dict__[name]
    if "__getattr__" not in module.__dict__:
        return _MISSING
    try:
        with contextlib.redirect_stdout(sys.stderr):
            return getattr(module, name)
    except Exception as error:  # the failure is written into the row, not dropped
        return _LazyFailure(f"{type(error).__name__}: {error}".splitlines()[0][:300])


def _lazy_names(module: types.ModuleType) -> list[str]:
    """The names a module with a module-level ``__getattr__`` makes on demand.

    Read from its ``__dir__`` and from the ``from ... import`` statements of its source (the
    ``if TYPE_CHECKING:`` block that shows type checkers the lazy names included).
    """
    if "__getattr__" not in module.__dict__:
        return []
    listed = dir(module)  # runs the module's own __dir__ when it has one
    file = _module_file(module.__name__)
    tree = _source_tree(file) if file is not None and file.endswith(".py") else None
    imported = [
        alias.asname or alias.name
        for statement in (_top_statements(tree.body) if tree is not None else ())
        if isinstance(statement, ast.ImportFrom)
        for alias in statement.names
        if alias.name != "*"
    ]
    return sorted({*listed, *imported} - set(module.__dict__))


def _exported_names(module: types.ModuleType, sources: _Sources) -> tuple[list[str], ModuleReadWay]:
    """The names a module exports, and how they were read (``__all__`` or the package's own names)."""
    declared = module.__dict__.get("__all__")
    if isinstance(declared, (list, tuple)):
        return [str(name) for name in cast(Sequence[object], declared)], "__all__"
    candidates = [*module.__dict__, *_lazy_names(module)]
    names = sorted(
        name
        for name in candidates
        if not name.startswith("_") and _is_own_name(module, name, sources)
    )
    return names, "own"


def _is_own_name(module: types.ModuleType, name: str, sources: _Sources) -> bool:
    """Whether a module name counts as the package's own export (a failed lazy name counts, to be reported)."""
    value = _module_value(module, name)
    if isinstance(value, _LazyFailure):
        return True
    if value is _MISSING or isinstance(value, types.ModuleType):
        return False
    return _defined_in_package(value, module.__name__, name, sources)


def _defined_in_package(value: object, module_name: str, name: str, sources: _Sources) -> bool:
    """Whether a module's name is the package's own (not imported from another package)."""
    target = _definition(value)
    if isinstance(target, (type, types.FunctionType)):
        return sources.is_own_module(target.__module__)
    if isinstance(value, (typing.TypeVar, *_TYPE_ALIAS_TYPES)):
        return sources.is_own_module(value.__module__)
    return sources.is_own_module(sources.origin(module_name, name).module)


def _location(value: object, module_name: str, name: str, sources: _Sources) -> str:
    """Where an exported value is defined, for the table's column."""
    place = _code_place(_definition(value))
    if place is None:
        return _place_text(sources.origin(module_name, name))
    if place.module == "builtins":
        # A native class that names no module (pyo3 without ``module=``): the module showing it.
        return f"{module_name}(native)"
    return _place_text(place)


def _macro_exports(
    module: types.ModuleType, sources: _Sources
) -> list[tuple[str, Callable[..., object]]]:
    """A Hy module's public macros: ``_hy_export_macros`` when it exports them, else its own macros.

    The names of Hy's tables come from ``doeff_hy_bytecode_guard._records``, their one home (card ki-ffe72a1fb7d7).
    """
    if importlib.util.find_spec("doeff_hy_bytecode_guard") is None:
        return []
    records = importlib.import_module("doeff_hy_bytecode_guard._records")
    macros = module.__dict__.get(records.MACRO_TABLE)
    if not isinstance(macros, dict):
        return []
    table = cast(
        dict[str, Callable[..., object]], macros
    )  # Hy's macro table: mangled name -> macro
    exported = module.__dict__.get(records.EXPORT_NAMES)
    if isinstance(exported, (list, tuple, set, frozenset)):
        names = [str(name) for name in cast(Sequence[object], exported)]
        return [(name, table[name]) for name in names if name in table]
    return [
        (name, macro)
        for name, macro in sorted(table.items())
        if not name.startswith("_")
        and isinstance(macro, types.FunctionType)
        and macro.__module__ == module.__name__
        and sources.is_own_module(macro.__module__)
    ]


def _display_name(name: str) -> str:
    """A Hy-mangled name as written in Hy (``hyx_XlessHthan_signX_`` -> ``<-``)."""
    if not name.startswith("hyx_") or importlib.util.find_spec("hy") is None:
        return name
    return str(importlib.import_module("hy").unmangle(name))


def _fallback_names(path: Path) -> list[str]:
    """The names a module that cannot be imported seems to define, read from its text."""
    text = _source_text(str(path))
    if text is None:
        return []
    if path.suffix == ".hy":
        return sorted(
            {name for name in _HY_TOP_DEFINITION.findall(text) if not name.startswith("_")}
        )
    tree = _source_tree(str(path)) if path.suffix in (".py", ".pyi") else None
    if tree is None:
        return []
    names = {
        name for statement in _top_statements(tree.body) for name in _statement_names(statement)
    }
    return sorted(name for name in names if not name.startswith("_"))


def _entry_location(target: str) -> str:
    """Where a ``module:attr`` console script or entry point target is defined."""
    module_name, _, attribute = target.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as error:  # the failure is written into the row, not dropped
        return f"{module_name}(import できない: {type(error).__name__})"
    if not attribute:
        return _place_text(Place(module_name, _module_file(module_name)))
    value = functools.reduce(_static, attribute.split("."), cast(object, module))
    return _place_text(_code_place(_definition(value)) or Place(module_name))


def _name_row(
    module: types.ModuleType, door: str, name: str, classifier: _Classifier, sources: _Sources
) -> ExportRow:
    """The row of one name a module exports: its verdict and where it is defined.

    ``door`` is the import name the user reads the module by, which shows the name. It is the
    module's own ``__name__`` unless the module puts another in ``sys.modules`` under its name.
    """
    value = _module_value(module, name)
    if value is _MISSING:
        verdict = Verdict(
            "unclassified", "名だけ", "__all__ か移し替えの module の列に在るが module に無い"
        )
        defined_at = module.__name__
    elif isinstance(value, _LazyFailure):
        verdict = Verdict("unclassified", "名だけ", f"__getattr__ が名を作れない: {value.error}")
        defined_at = module.__name__
    else:
        try:
            verdict = classifier.classify(value, name)
            defined_at = _location(value, module.__name__, name, sources)
        except Exception as error:  # the failure is written into the row, not dropped
            verdict = Verdict(
                "unclassified", "値", f"種類を決める途中で {type(error).__name__}: {error}"
            )
            defined_at = module.__name__
    return ExportRow(
        module=door,
        name=name,
        kind=verdict.kind,
        form=verdict.form,
        defined_at=defined_at,
        reason=verdict.reason,
    )


def _macro_row(
    module: types.ModuleType, door: str, name: str, macro: Callable[..., object]
) -> ExportRow:
    """The row of one public Hy macro (``door`` as in ``_name_row``)."""
    return ExportRow(
        module=door,
        name=_display_name(name),
        kind="other",
        form=MACRO_FORM,
        defined_at=_place_text(_code_place(macro) or Place(module.__name__)),
        reason="Hy の macro(書き方の道具)",
    )


@dataclass(frozen=True)
class _ModuleResult:
    """One public module as read: how it was read, and the rows of its names."""

    read: ModuleRead
    rows: tuple[ExportRow, ...]


def _read_module(
    module_name: str,
    path: Path,
    classifier: _Classifier,
    sources: _Sources,
    pinned: tuple[str, ...] | None = None,
) -> _ModuleResult:
    """Import one public module and type its names; a module that cannot be imported is read as text.

    A moved-submodule shim that cannot be imported (the module it moved to fails) is read from the
    moved module's text: the shim's own text defines nothing. ``pinned`` is the names a shim pins for
    the module (``shim_pins``): the module shows those names only, like an ``__all__``.
    """
    try:
        with contextlib.redirect_stdout(sys.stderr):
            module = importlib.import_module(module_name)
    except (
        Exception,
        SystemExit,
    ) as error:  # the failure is written into the inventory, not dropped
        message = f"{type(error).__name__}: {error}".splitlines()[0][:300]
        path = _shim_source(module_name, path)
        location = _place_text(Place(module_name, str(path)))
        return _ModuleResult(
            ModuleRead(name=module_name, read="text", error=message),
            tuple(
                ExportRow(
                    module=module_name,
                    name=name,
                    kind="unclassified",
                    form="import できない module の名",
                    defined_at=location,
                    reason=message,
                )
                for name in (_fallback_names(path) if pinned is None else pinned)
            ),
        )
    names, read = _exported_names(module, sources)
    macros = _macro_exports(module, sources)
    if pinned is not None:
        shown = set(pinned)
        macros = [(name, macro) for name, macro in macros if _display_name(name) in shown]
        names = [name for name in pinned if name not in {_display_name(n) for n, _m in macros}]
        read = "pinned"
    return _ModuleResult(
        ModuleRead(name=module_name, read=read, error=None),
        (
            *(_name_row(module, module_name, name, classifier, sources) for name in names),
            *(_macro_row(module, module_name, name, macro) for name, macro in macros),
        ),
    )


def _metadata_rows(layout: PackageLayout) -> list[ExportRow]:
    """The rows of what ``pyproject.toml`` installs besides modules: scripts, entry points, executables."""
    cargo = _place_text(Place(layout.dir.name, str(layout.dir / "Cargo.toml")))
    return [
        *(
            ExportRow(
                module="(console script)",
                name=script,
                kind="other",
                form=CONSOLE_SCRIPT,
                defined_at=_entry_location(target),
                reason=f"命令の入口 {target}",
            )
            for script, target in sorted(layout.scripts.items())
        ),
        *(
            ExportRow(
                module="(entry point)",
                name=f"{group}:{entry}",
                kind="other",
                form=f"entry point({group})",
                defined_at=_entry_location(target),
                reason=f"入口 {target}",
            )
            for group, entries in sorted(layout.entry_points.items())
            for entry, target in sorted(entries.items())
        ),
        *(
            ExportRow(
                module="(実行 file)",
                name=binary,
                kind="other",
                form=RUST_EXECUTABLE,
                defined_at=cargo,
                reason="maturin の bin が置く実行 file",
            )
            for binary in layout.rust_bins
        ),
    ]


def submodule_rows(layout: PackageLayout) -> list[ExportRow]:
    """One row per public submodule (a public module that is not an import root — R5's outliers).

    Read from the files only: a submodule that cannot be imported is still a module a user reaches.
    """
    roots = {root.name for root in layout.roots}
    return [
        ExportRow(
            module=SUBMODULE,
            name=name,
            kind="other",
            form="公開の submodule",
            defined_at=_place_text(Place(name, str(path))),
            reason="path のどの部分も _ で始まらない submodule(名は import の根から出し、submodule は _ で隠す)",
        )
        for name, path in public_modules(layout)
        if name not in roots
    ]


def probe_package(package_dir: Path) -> PackageInventory:
    """Import one package's public modules and type every exported name (run in a child process)."""
    layout = read_layout(package_dir)
    _put_roots_on_path(layout)
    _install_hy()
    classifier = _Classifier()
    sources = _Sources(tuple(root.name for root in layout.roots))
    pins = pinned_names(layout)
    results = [
        _read_module(name, path, classifier, sources, pins.get(name))
        for name, path in public_modules(layout)
    ]
    return PackageInventory(
        dir=package_dir.name,
        distribution=layout.distribution,
        import_roots=[root.name for root in layout.roots],
        notes=list(layout.notes),
        modules=[result.read for result in results],
        exports=[
            *(row for result in results for row in result.rows),
            *submodule_rows(layout),
            *_metadata_rows(layout),
        ],
        error=None,
    )


# --- the whole repo (parent process) ---


def _run_probe(package_dir: Path, timeout: float) -> PackageInventory:
    """Probe one package in a child process (its own imports, its own failures)."""
    with tempfile.TemporaryDirectory(prefix="public-api-") as scratch:
        out = Path(scratch) / "probe.json"
        command = [
            sys.executable,
            "-P",
            str(Path(__file__).resolve()),
            "--probe",
            str(package_dir),
            "--probe-out",
            str(out),
        ]
        try:
            finished = subprocess.run(
                command, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            return _failed(package_dir, f"{timeout:.0f} 秒で読み終わらなかった")
        if finished.returncode != 0 or not out.is_file():
            tail = " / ".join(finished.stderr.strip().splitlines()[-5:])
            return _failed(
                package_dir, f"読む子が終了 code {finished.returncode} で終わった: {tail}"
            )
        return cast(
            PackageInventory, json.loads(out.read_text(encoding="utf-8"))
        )  # written by probe_package


def _failed(package_dir: Path, message: str) -> PackageInventory:
    """The entry of a package whose child process failed: its layout, no names, and the failure."""
    layout = read_layout(package_dir)
    return PackageInventory(
        dir=package_dir.name,
        distribution=layout.distribution,
        import_roots=[root.name for root in layout.roots],
        notes=list(layout.notes),
        modules=[],
        exports=[],
        error=message,
    )


def inventory(
    repo: Path = ROOT,
    packages: Sequence[str] | None = None,
    jobs: int = 4,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> list[PackageInventory]:
    """The public API of every package dir (or of the named ones), one entry per dir, sorted by name."""
    dirs = package_dirs(repo)
    if packages is not None:
        unknown = set(packages) - {path.name for path in dirs}
        if unknown:
            raise SystemExit(
                f"目録の package に無い(packages/ の下に無いか doeff の中の部品): {', '.join(sorted(unknown))}"
            )
        dirs = [path for path in dirs if path.name in packages]
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        return list(pool.map(lambda path: _run_probe(path, timeout), dirs))


def _merge_key(row: ExportRow) -> tuple[str, str, str]:
    """What makes two rows the same name: its name, its definition and its kind."""
    return row["name"], row["defined_at"], row["kind"]


def merged_exports(package: PackageInventory) -> list[MergedRow]:
    """A package's names, one row per name and definition (in first-shown order), with every module showing it."""
    indexed = sorted(enumerate(package["exports"]), key=lambda item: (_merge_key(item[1]), item[0]))
    groups = [
        list(group)
        for _key, group in itertools.groupby(indexed, key=lambda item: _merge_key(item[1]))
    ]
    return [
        MergedRow(**group[0][1], modules=[row["module"] for _index, row in group])
        for group in sorted(groups, key=lambda group: group[0][0])
    ]


def counts(package: PackageInventory) -> dict[Kind, int]:
    """How many distinct names of each kind a package shows (a name re-exported twice counts once)."""
    found = collections.Counter(row["kind"] for row in merged_exports(package))
    return {kind: found[kind] for kind in KINDS}


# --- the markdown page (discussion-page skill, scene "page") ---


def _cell(text: str) -> str:
    """Text made safe for one markdown table cell."""
    return text.replace("|", "\\|").replace("\n", " ")


def _counts_text(found: dict[Kind, int]) -> str:
    """A package's counts in one phrase, for the page and the run's log."""
    return (
        f"公開名 {sum(found.values())} 個(effect {found['effect']}・handler {found['handler']}・"
        f"それ以外 {found['other']}・未分類 {found['unclassified']})"
    )


def _package_notes(package: PackageInventory) -> list[str]:
    """What the reader should know about a package before its table (layout notes, failures)."""
    failure = [f"読めなかった: {package['error']}"] if package["error"] is not None else []
    modules = [
        f"`{module['name']}` を import できない: {module['error']}"
        for module in package["modules"]
        if module["error"] is not None
    ]
    return [*package["notes"], *failure, *modules]


def _package_section(package: PackageInventory, found: dict[Kind, int]) -> list[str]:
    """One package's section of the page: a summary line, its notes and its table of names."""
    roots = ", ".join(f"`{root}`" for root in package["import_roots"]) or "なし"
    notes = _package_notes(package)
    rows = merged_exports(package)
    head = [
        "",
        f"## {package['dir']}",
        "",
        f"import の根 {roots}・{_counts_text(found)}。",
        *([""] if notes else []),
        *(f"- {_cell(note)}" for note in notes),
    ]
    if not rows:
        return [*head, "", "注: 公開名は無い。"]
    table = [
        f"| `{_cell(row['name'])}` | {KIND_LABELS[row['kind']]} | {_cell(row['form'])} | "
        f"`{_cell(_short_place(row['defined_at'], package['dir']))}` | "
        f"{_cell(_short_modules(row['modules'], package['import_roots']))} | "
        f"{_cell(row['reason']) if row['kind'] != 'other' else ''} |"
        for row in rows
    ]
    return [
        *head,
        "",
        "| 名 | 種類 | 形 | 定義の場所 | 出す所 | 訳 |",
        "|---|---|---|---|---|---|",
        *table,
    ]


def _short_place(place: str, package_dir: str) -> str:
    """A definition place without the package's own ``packages/<dir>/`` (and ``src/`` / ``python/``) prefix."""
    own = place.removeprefix(f"packages/{package_dir}/")
    return own.removeprefix("src/").removeprefix("python/") if own != place else place


def _short_modules(modules: list[str], roots: list[str]) -> str:
    """The modules showing a name, relative to the package's root (``根`` for the root), at most two named."""
    relative = [
        next(
            (
                "根" if module == root else module.removeprefix(f"{root}.")
                for root in roots
                if module == root or module.startswith(f"{root}.")
            ),
            module,
        )
        for module in modules
    ]
    rest = f" ほか {len(relative) - 2}" if len(relative) > 2 else ""
    return ", ".join(relative[:2]) + rest


_METHOD_NOTES = (
    "数える module: pyproject の wheel の packages(hatch)・python-source と module-name(maturin)・setuptools の"
    "見つける package を import の根とし、根と、path のどの部分も `_` で始まらない submodule の全部。",
    "module が出す名: `__all__` が在ればその全部。無ければ `_` で始まらない名のうち、その package の中で定義された物"
    "(他の package と標準ライブラリから import しただけの名と module は数えない)。module の `__getattr__` で"
    "遅れて出す名は、`__dir__` と import 文(`if TYPE_CHECKING:` の節を含む)が並べる名も数える。",
    "Hy の module は `_` で始まらない macro も数える。pyproject の console script・entry point と、maturin の bin の"
    "Rust の実行 file も数える。",
    "path のどの部分も `_` で始まらない submodule そのものも、1 行(出す所 `(submodule)`・名は module の import の名・"
    "形「公開の submodule」・それ以外)と数える — 公開の名は import の根からだけ出す(ADR-DOE-API-001 R5)。"
    "1 行目が `# moved-submodule-shim: <古い名> -> <新しい名>` の移し替えの module は、移した先の package の下の module も"
    "古い名で数える(移し替えの module を消すまで古い path で import できる)。古い path が出す名は、印の下の"
    "`# moved-submodule-shim-names <古い名>: <名> …` の行(隠した時に書いた名の固定の列)の名だけを数える — 古い path は"
    "移した先の `_` の module そのものを返すので、列が無いと `_` の module に足した helper も古い path の公開名に加わる。",
    "effect: EffectBase の子の class・EffectBase の値・effect を作る関数(戻りの型が EffectBase の子か、return が"
    "effect の class を呼ぶ)。",
    "handler: handler の印(`_doeff_is_handler_fn`)を持つ値と class・handler を返す関数(戻りの型が ProgramHandler・"
    "program を受けて program を返す呼べる物・handler の組か、return が handler を作る)・引数が (effect, k) の素の"
    "handler の関数。",
    "それ以外: program 関数(@do・defk)と戻りの型が program の関数・ほかの関数・class・例外・型・定数・macro・"
    "console script・entry point。program を返す関数は、中(と、中で読む同じ package の関数の中)で handler を被せず"
    "(WithHandler・handler の値・作り手・工場を読まない)effect に答えない(Resume・Pass・Transfer などを読まない)時に"
    "形「program の helper」— 許した物の表は名ごとに組「許した物 — program の helper」で許す(ADR-DOE-API-001 R18)。",
    "未分類: 戻りの型が無いか中身を言わない型(Any・object・Callable・その list)で、return の形からも決まらない関数・"
    "名が Effect か Handler で終わるのに印の無い class(とそれを返す関数)・import できない module の名。",
    "同じ名を根と submodule の両方が出す時は 1 行にまとめ、列「出す所」に出す module を import の根からの相対で"
    "2 つまで並べる(根は「根」・3 つ目からは数)。定義の場所の `packages/<package>/`(と続く `src/`・`python/`)は"
    "略す。列「訳」は種類を決めた訳で、「それ以外」の行は列「形」が訳を兼ねるので空ける。全部の module と訳は"
    "`--json` の目録に在る。",
    "道具: `tools/public_api_inventory.py`(`uv run python tools/public_api_inventory.py --json <file> "
    "--markdown <file>`)。",
)


def render_markdown(packages: list[PackageInventory], commit: str) -> str:
    """The inventory as a page for people: the totals table, then one table per package."""
    per_package = [(package, counts(package)) for package in packages]
    total: dict[Kind, int] = {
        kind: sum(found[kind] for _package, found in per_package) for kind in KINDS
    }
    header = [
        "# doeff の subpackage の公開 API の目録",
        "",
        (
            f"packages/ の下の {len(packages)} 個の subpackage が利用者に見せる公開名は {sum(total.values())} 個で、"
            f"effect {total['effect']} 個・handler {total['handler']} 個・それ以外 {total['other']} 個・"
            f"未分類 {total['unclassified']} 個です。"
        ),
        "",
        "## 全体",
        "",
        "| package | 公開名 | effect | handler | それ以外 | 未分類 | 公開 module | import できない module |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    totals = [
        f"| {package['dir']} | {sum(found.values())} | {found['effect']} | {found['handler']} | "
        f"{found['other']} | {found['unclassified']} | {len(package['modules'])} | "
        f"{sum(1 for module in package['modules'] if module['error'] is not None)} |"
        for package, found in per_package
    ]
    sections = [line for package, found in per_package for line in _package_section(package, found)]
    appendix = ["", "## 付録: 数え方", "", *(f"- {note}" for note in _METHOD_NOTES)]
    note = f"注: doeff の commit {commit} の木を数えた。種類の決め方は付録にある。"
    return "\n".join([*header, *totals, "", note, *sections, *appendix]) + "\n"


def _git_head(repo: Path) -> str:
    """The commit the inventory was taken at, for the page's note."""
    finished = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    return finished.stdout.strip() or "(不明)"


def main(argv: Sequence[str] | None = None) -> int:
    """Write the inventory as JSON and markdown, or probe one package (the child process's entry)."""
    parser = argparse.ArgumentParser(description="subpackage の公開 API の目録を作る")
    parser.add_argument(
        "--package", action="append", help="packages/ の下の dir の名(重ねてよい・既定は全部)"
    )
    parser.add_argument("--json", help="目録の JSON を書く file(- で標準出力)")
    parser.add_argument("--markdown", help="目録の表の markdown を書く file")
    parser.add_argument("--jobs", type=int, default=4, help="同時に読む package の数")
    parser.add_argument(
        "--timeout", type=float, default=_DEFAULT_TIMEOUT_SECONDS, help="1 package を読む秒の上限"
    )
    parser.add_argument("--probe", help=argparse.SUPPRESS)
    parser.add_argument("--probe-out", help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    if arguments.probe is not None:
        result = probe_package(Path(arguments.probe).resolve())
        Path(arguments.probe_out).write_text(
            json.dumps(result, ensure_ascii=False), encoding="utf-8"
        )
        return 0
    packages = inventory(ROOT, arguments.package, arguments.jobs, arguments.timeout)
    commit = _git_head(ROOT)
    document = (
        json.dumps({"commit": commit, "packages": packages}, ensure_ascii=False, indent=1) + "\n"
    )
    if arguments.json == "-":
        sys.stdout.write(document)
    elif arguments.json is not None:
        Path(arguments.json).write_text(document, encoding="utf-8")
    if arguments.markdown is not None:
        Path(arguments.markdown).write_text(render_markdown(packages, commit), encoding="utf-8")
    log = "\n".join(
        f"{package['dir']}: {_counts_text(counts(package))}"
        + (f" — 読めなかった: {package['error']}" if package["error"] else "")
        for package in packages
    )
    print(log, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
