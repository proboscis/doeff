"""An analysis answer kept on disk, reused while every module file it depends on has the same content.

A closure test (does a job's Program leave any effect unanswered under its foundation?) reads
hundreds of definitions and costs 1–7 s even with the expanded trees cached — spread over the
whole reader, so no single part is worth cutting (agora-redesign #1586 / #1645). Its answer is a
function of what the analysis reads: the sources of the modules it follows, and the runtime values
(module attributes, handler marks, signatures, the foundation itself) that importing those modules
made. So the answer is stored with the module name and the content digest of the modules it depends
on (``_depended_modules``):

- the modules whose source the analysis read (``program_effects.reading_sources``) and the module of
  the analysis itself (``compute`` — the caller's code that runs the analyzer);
- and, from each of those, the modules its values were built from: its parent packages, the modules
  that define the values its namespace holds (a function's or class's module and the file of its
  code, an instance's class's module, the module a name is bound to, the macros a Hy module
  required), the modules its own code names (imports inside function bodies — the analysis follows
  them — and ``pkg.sub`` used through a bound ``pkg``), and the functions its functions wrap or close
  over — and so on from every module reached.

Before, the key was every module the process had loaded (#1645). A test session loads hundreds of
test files a closure analysis never reads, and agora-controllers' daily run spreads 951 files over
three sessions, so a change in any of them in four hours (and ``conftest`` / ``__mp_main__``, loaded
alike from other files) made every closure test analyze in full — 20-60 s each (ki-30d28550680c).
A package's loaded submodules hang on it as attributes whoever imported them, so a submodule is
reached from its package only when a module reached names it.

An answer is reused when, for every module it names, the file this process loads that module from
has the same content: the loaded module's file, or — for a module this process has not loaded — the
file the import system would find, looked up without importing anything. A module this process
loaded from a file with other content misses (agora-redesign #1864 — checkouts of one repo share the
machine's cache and name their modules alike).

The file's path is not part of the key (agora-redesign #3777). Before, each file was named by its
path and stat and an identity had one answer, so a new worktree always ran the analysis in full,
and two worktrees running the same test overwrote each other's answer (the screen job's closure
test took 5–7 s whenever another worktree had run it last). Now a worktree whose files have the
same content reads the answer another one stored, and answers of different contents are kept side
by side in the identity's directory, named by the content they were computed under.

A file is read only when it does not stat as it did where the answer was stored: each module file
is stored with its path and stat, and one that still stats the same (path, mtime, ctime, size,
inode) is taken as unchanged. The files that have to be read (all of them when an answer is
stored) are read side by side. An answer reused after reading files is stored again with this
process's stats, so the next process in the same checkout reads none. An identity keeps the
``_KEPT`` answers used most recently; storing another drops the rest.

What the key does not cover (agora-redesign #1645, ki-30d28550680c): a value decided at import time
from a file that is not a module (or from the environment); a module that rewrites other modules'
values when imported but is not reached from them (a test's conftest patching a job's module) —
before, such a module counted by its content only when the storing process had loaded it, never by
whether the reading process loaded it; and plain data (a number, a string) a module built at import
time with a module it does not keep a reference to. agora-controllers does neither at import time.

``DOEFF_EFFECT_ANALYZER_RESULT_CACHE=off`` turns this cache off (nothing read or written); the
expanded-tree cache's ``DOEFF_EFFECT_ANALYZER_CACHE=off`` turns both off. A miss (the analysis run
in full, and the digests stored with its answer) is observed like an uncached expansion, so a
test-time budget subtracts it.
"""

import contextlib
import dis
import hashlib
import itertools
import os
import pickle
import sys
import tempfile
import types
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Generic, TypeVar, cast

from doeff_effect_analyzer import env_places
from doeff_effect_analyzer.program_effects import (
    _EXPANSION_OBSERVERS,
    _hy_cache_dir,
    reading_sources,
)

T = TypeVar("T")

# v1: the answer with the stat of every module file loaded when it was computed.
# v2: each file also names its module, so an answer from another checkout is not reused (#1864).
# v3: each file is named by its content (its path and stat only spare reading it), and an identity
#     is a directory of answers named by the contents they were computed under (#3777).
# v4: the files are those of the modules the answer depends on, not every loaded module (ki-30d28550680c).
# Entries of an older shape have another key and are never read (nothing deletes them).
_FORMAT = "v4"

# The answers one identity keeps — one per content its analysis was computed under (checkouts at
# different commits running the same test).
_KEPT = 8


@dataclass(frozen=True)
class _Stat:
    """A file as ``os.stat`` saw it — what decides whether it has to be read again."""

    path: str
    mtime_ns: int
    ctime_ns: int
    size: int
    inode: int


@dataclass(frozen=True)
class _Material:
    """One module file as it was when the answer was computed: the module loaded from it, the digest
    of its content (what the answer depends on), and how it stat'ed where it was stored."""

    module: str
    sha256: str
    stat: _Stat


@dataclass(frozen=True)
class _Located:
    """A loaded module and the file it was loaded from, before the file's content is read."""

    module: str
    stat: _Stat


@dataclass(frozen=True)
class _Entry(Generic[T]):
    """A stored answer and the module files it was computed under."""

    material: tuple[_Material, ...]
    value: T


def importable_name(obj: object) -> str | None:
    """``module:qualname`` when that name leads back to ``obj`` itself, else None.

    Used to name the arguments of an analysis in the key: an object that cannot be found again by
    its name (a partial, a lambda, a def inside a function) has no name that pins what it is, so
    an analysis of it is not cached."""
    module_name = getattr(obj, "__module__", None)
    qualname = getattr(obj, "__qualname__", None)
    if not isinstance(module_name, str) or not isinstance(qualname, str) or "<" in qualname:
        return None
    found: object = sys.modules.get(module_name)
    for part in qualname.split("."):
        found = getattr(found, part, None)
    return f"{module_name}:{qualname}" if found is obj else None


def cached_result(identity: tuple[str, ...] | None, compute: Callable[[], T]) -> T:
    """``compute()``, or an answer stored under ``identity`` whose module files have the same
    content here. ``identity`` names the analysis and its arguments (None = the arguments cannot be
    named: compute without the cache). The answer must pickle, and hold only what ``compute`` read:
    a ``HandlerEffects`` in it would read its clauses not yet asked for when pickled — outside
    ``reading_sources``, so their modules would not be in the key (``program_effects.Clause``)."""
    place = _place(identity)
    if place is None:
        return compute()
    for path in _entries_most_recent_first(place):
        stored = _read_entry(path)
        if stored is None:
            continue
        current = _current_material(stored.material)
        if current is None:
            continue
        _mark_used(path, _Entry(material=current, value=stored.value), stored)
        # The files of this identity were written by this function with this compute (the
        # identity names the analysis), so the value is a T; pickle does not carry the type parameter.
        return cast(T, stored.value)
    with contextlib.ExitStack() as observed:
        for observer in tuple(_EXPANSION_OBSERVERS):
            observed.enter_context(observer())
        answer = reading_sources(compute)
        entry = _Entry(material=_material(_depended_modules(answer.read, compute)), value=answer.value)
        _write_entry(place / _entry_name(entry.material), entry)
        _drop_least_used(place)
    return answer.value


def _place(identity: tuple[str, ...] | None) -> Path | None:
    """The directory of the answers for ``identity`` (None = not cached)."""
    if identity is None or env_places.result_cache_setting() == "off":
        return None
    trees = _hy_cache_dir()
    if trees is None:
        return None
    key = "\n".join([_FORMAT, sys.version, *identity])
    return trees / "results" / hashlib.sha256(key.encode("utf-8")).hexdigest()


def _entry_name(material: tuple[_Material, ...]) -> str:
    """The file of an answer: the digest of the module names and contents it was computed under
    (not of their paths — another checkout with the same contents writes the same file)."""
    text = "\n".join(f"{used.module}={used.sha256}" for used in material)
    return f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}.pickle"


def _entries_most_recent_first(place: Path) -> tuple[Path, ...]:
    """The answers stored for an identity, the one used last first (none when the directory is absent)."""
    try:
        entries = tuple(place.glob("*.pickle"))
    except OSError:
        return ()
    return tuple(sorted(entries, key=_last_used, reverse=True))


def _last_used(path: Path) -> int:
    """When an answer was stored or last reused (-1 when it is gone — another process dropped it)."""
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _current_material(material: tuple[_Material, ...]) -> tuple[_Material, ...] | None:
    """The stored module files as this process finds them, when every one has the same content
    (None when one differs or cannot be found). A file that stats as it was stored is not read;
    the others are read side by side."""
    found = tuple(_stat_here(stored.module) for stored in material)
    statuses = tuple(status for status in found if status is not None)
    if len(statuses) != len(material):
        return None
    _read_ahead(tuple(status for stored, status in zip(material, statuses, strict=True) if status != stored.stat))
    current = tuple(_with_same_content(stored, status) for stored, status in zip(material, statuses, strict=True))
    kept = tuple(used for used in current if used is not None)
    return kept if len(kept) == len(material) else None


def _stat_here(module: str) -> _Stat | None:
    """The file this process loads ``module`` from, as it stats now (None when there is none)."""
    found = _file_of(module)
    return None if found is None else _stat_of(found)


def _with_same_content(stored: _Material, status: _Stat) -> _Material | None:
    """A stored module file as this process finds it (``status``), when it has the same content
    (None when it differs). A file that stats as it was stored is taken as unchanged."""
    if status == stored.stat:
        return stored
    if _digest(status) != stored.sha256:
        return None
    return _Material(module=stored.module, sha256=stored.sha256, stat=status)


def _mark_used(path: Path, current: "_Entry[object]", stored: "_Entry[object]") -> None:
    """Keep a reused answer: store it again when files were read to reuse it (so this checkout reads
    none next time), else only mark it as used now (an identity drops the answers used least recently)."""
    if current.material != stored.material:
        _write_entry(path, current)
        return
    with contextlib.suppress(OSError):
        os.utime(path)


def _file_of(module: str) -> str | None:
    """The file this process loads ``module`` from: the loaded module's, else the one the import
    system would find (None when there is none)."""
    loaded = sys.modules.get(module)
    if loaded is not None:
        filename = getattr(loaded, "__file__", None)
        return filename if isinstance(filename, str) else None
    spec = _spec_without_import(module)
    origin = None if spec is None or not spec.has_location else spec.origin
    return origin if isinstance(origin, str) else None


def _spec_without_import(name: str) -> ModuleSpec | None:
    """The spec the import system finds for ``name``, asking the finders directly so that no
    package is imported on the way (``importlib.util.find_spec`` imports the parents): the lookup
    must not change what is loaded — an answer is keyed on that."""
    parent = name.rpartition(".")[0]
    locations = _search_locations(parent) if parent else None
    if parent and locations is None:
        return None
    for finder in tuple(sys.meta_path):
        find_spec = getattr(finder, "find_spec", None)
        if find_spec is None:
            continue
        try:
            spec = find_spec(name, locations)
        except (ImportError, OSError, ValueError, KeyError):
            # KeyError: the import system's namespace path (a package without ``__init__``) reads its
            # parent from ``sys.modules`` and raises when the parent is not loaded here — not found
            # here, so the stored answer is not reused and the analysis runs again.
            return None
        if isinstance(spec, ModuleSpec):
            return spec
    return None


def _search_locations(package: str) -> list[str] | None:
    """Where the submodules of ``package`` are looked for (None when it is not a package here)."""
    loaded = sys.modules.get(package)
    if loaded is not None:
        locations = getattr(loaded, "__path__", None)
        return None if locations is None else list(locations)
    spec = _spec_without_import(package)
    if spec is None or spec.submodule_search_locations is None:
        return None
    return list(spec.submodule_search_locations)


def _material(names: frozenset[str]) -> tuple[_Material, ...]:
    """The files of the loaded modules ``names``, by module name, with their content's digest — what
    an answer is stored with."""
    located = tuple(
        _Located(module=name, stat=status)
        for name in names
        if (module := sys.modules.get(name)) is not None and (status := _loaded_stat(module)) is not None
    )
    _read_ahead(tuple(place.stat for place in located))
    material = (
        _Material(module=place.module, sha256=digest, stat=place.stat)
        for place in located
        if (digest := _digest(place.stat)) is not None
    )
    return tuple(sorted(material, key=lambda used: used.module))


@dataclass(frozen=True)
class _Loaded:
    """The modules of this process, looked up while the modules of an answer are gathered: by name
    (``sys.modules`` as it was), by file (a function's code names the file it was compiled from,
    whatever its ``__module__`` says) and the loaded submodules of each package, by their last name."""

    by_name: Mapping[str, object]
    by_file: Mapping[str, str]
    children: Mapping[str, Mapping[str, str]]

    def submodules(self, package: str, names: frozenset[str]) -> frozenset[str]:
        """The loaded submodules of ``package`` whose last name is one of ``names``."""
        found = self.children.get(package)
        return frozenset() if found is None else frozenset(found[last] for last in names & found.keys())


def _loaded_now() -> _Loaded:
    """This process's modules, indexed once for one gathering."""
    by_name = dict(sys.modules)
    by_file = {
        filename: name
        for name, module in by_name.items()
        if isinstance(filename := _module_namespace(module).get("__file__"), str)
    }
    submodules = sorted(name for name in by_name if "." in name)
    children = {
        package: {name.rpartition(".")[2]: name for name in names}
        for package, names in itertools.groupby(submodules, key=lambda name: name.rpartition(".")[0])
    }
    return _Loaded(by_name=by_name, by_file=by_file, children=children)


def _depended_modules(read: frozenset[str], compute: object) -> frozenset[str]:
    """The modules an answer depends on, so that it is keyed on them only: the modules whose source
    the analysis read, the module of the analysis itself, and every module reached from them by
    ``_dependencies`` (the module docstring). ``__main__`` is the program that started the process
    (another runner for the same analysis), not a module of it."""
    loaded = _loaded_now()
    frontier = (read | _value_modules(compute, "", frozenset(), loaded)) & loaded.by_name.keys()
    reached: frozenset[str] = frozenset()
    while frontier:
        reached = reached | frontier
        frontier = frozenset(
            found for name in frontier for found in _dependencies(name, loaded)
        ) - reached
    return reached - {"__main__"}


def _dependencies(name: str, loaded: _Loaded) -> frozenset[str]:
    """The loaded modules the values of the module ``name`` were built from: its parent packages,
    the modules of the values its namespace holds (and of the macros a Hy module required), and the
    modules its own code imports in function bodies or names as a dotted string."""
    parents = frozenset(name.rsplit(".", depth)[0] for depth in range(1, name.count(".") + 1))
    namespace = _module_namespace(loaded.by_name[name])
    package = namespace.get("__package__")
    own = _own_code(name, package if isinstance(package, str) else "", namespace)
    held = frozenset(
        found for value in _namespace_values(namespace) for found in _value_modules(value, name, own.names, loaded)
    )
    taken = frozenset(found for base in own.imported for found in loaded.submodules(base, own.names))
    return (parents | held | own.imported | taken) & loaded.by_name.keys()


def _module_namespace(module: object) -> Mapping[str, object]:
    """A loaded module's namespace, read without attribute access (an object standing in
    ``sys.modules`` may compute its attributes); empty for an object that is not a module."""
    return module.__dict__ if issubclass(type(module), types.ModuleType) else {}


def _namespace_values(namespace: Mapping[str, object]) -> tuple[object, ...]:
    """The values a module's namespace holds, with the macros a Hy module required (Hy keeps them in
    a dict of its own, not as names)."""
    macros = tuple(
        macro
        for table in ("_hy_macros", "_hy_reader_macros")
        if isinstance(found := namespace.get(table), dict)
        for macro in found.values()
    )
    return (*tuple(namespace.values()), *macros)


def _value_modules(value: object, owner: str, own_names: frozenset[str], loaded: _Loaded) -> frozenset[str]:
    """The modules a value of the module ``owner``'s namespace was built from (a module bound to a
    name: that module, and its submodules ``owner``'s code names; a function: its module, the file of
    its code and what it wraps or closes over; a class: its module; anything else: its class's
    module). A submodule of ``owner`` itself counts only when ``owner``'s code names it — a package
    holds every loaded submodule as an attribute, whoever imported it."""
    # The kind is read from type(value), not isinstance (a proxy's __class__ may run code); that does
    # not narrow value for the checker, so each cast below states the subclass check above it.
    kind = type(value)
    if issubclass(kind, types.ModuleType):
        return _bound_modules(value, owner, own_names, loaded)
    if issubclass(kind, types.FunctionType):
        return _function_modules(cast(types.FunctionType, value), loaded)
    if issubclass(kind, type):
        defined: object = cast(type, value).__dict__.get("__module__")
    elif issubclass(kind, types.BuiltinFunctionType):
        defined = cast(types.BuiltinFunctionType, value).__module__
    else:
        defined = kind.__module__
    return frozenset({defined}) if isinstance(defined, str) else frozenset()


def _bound_modules(module: object, owner: str, own_names: frozenset[str], loaded: _Loaded) -> frozenset[str]:
    """The modules a module bound to a name of the module ``owner`` stands for: itself and its
    submodules ``owner``'s code names — or, for a submodule of ``owner`` (an attribute the import
    system set on the package, whoever imported it), itself only when ``owner``'s code names it."""
    bound = _module_namespace(module).get("__name__")
    if not isinstance(bound, str):
        return frozenset()
    if owner and bound.startswith(f"{owner}."):
        return frozenset({bound}) if bound.rpartition(".")[2] in own_names else frozenset()
    return frozenset({bound}) | loaded.submodules(bound, own_names)


def _function_modules(function: types.FunctionType, loaded: _Loaded) -> frozenset[str]:
    """The modules of a function and of the functions it wraps (``__wrapped__`` — a defk keeps its
    body there) or closes over: each one's ``__module__`` and the module of the file its code was
    compiled from."""
    return frozenset(
        found
        for each in _functions_within(function)
        for found in (each.__module__, loaded.by_file.get(each.__code__.co_filename))
        if isinstance(found, str)
    )


def _functions_within(function: types.FunctionType) -> tuple[types.FunctionType, ...]:
    """A function and the functions it holds — in its ``__dict__`` (what a decorator wrapped) and in
    its closure cells — and theirs, each once."""
    reached: tuple[types.FunctionType, ...] = ()
    frontier: tuple[types.FunctionType, ...] = (function,)
    while frontier:
        reached = (*reached, *frontier)
        frontier = tuple(
            held
            for each in frontier
            for held in _held_functions(each)
            if not any(held is seen for seen in reached)
        )
        frontier = tuple(held for index, held in enumerate(frontier) if not any(held is earlier for earlier in frontier[:index]))
    return reached


def _held_functions(function: types.FunctionType) -> tuple[types.FunctionType, ...]:
    """The functions a function holds directly: in its ``__dict__`` and its closure cells (an empty
    cell holds nothing)."""
    cells = tuple(
        content
        for cell in function.__closure__ or ()
        for content in _cell_content(cell)
    )
    return tuple(
        cast(types.FunctionType, held)
        for held in (*tuple(function.__dict__.values()), *cells)
        if issubclass(type(held), types.FunctionType)
    )


def _cell_content(cell: types.CellType) -> tuple[object, ...]:
    """What a closure cell holds (nothing while its variable is unassigned)."""
    try:
        return (cell.cell_contents,)
    except ValueError:
        return ()


@dataclass(frozen=True)
class _OwnCode:
    """What the code of the functions and classes a module defines refers to: the names it uses
    (globals and attributes — a submodule reached through a package it holds is one of them) and the
    modules it imports in its bodies (and names as a dotted string — ``__import__("pkg.mod")``, the
    form doeff-hy's expansions use); ``from a import x`` also takes the submodule ``a.x`` when ``x``
    is one of the names. A bare name is not taken for a module: code names ``conftest`` or ``test``
    without importing a module of that name."""

    names: frozenset[str]
    imported: frozenset[str]


def _own_code(owner: str, package: str, namespace: Mapping[str, object]) -> _OwnCode:
    """The names and imports of the code the module ``owner`` (in ``package``) defines — to find the
    modules that code imports when called and the submodules it reaches through a package."""
    codes = tuple(
        code
        for value in namespace.values()
        for function in _defined_functions(value, owner)
        for within in _functions_within(function)
        for code in _codes_within(within.__code__)
    )
    return _OwnCode(
        names=frozenset(name for code in codes for name in code.co_names),
        imported=frozenset(found for code in codes for found in _code_imports(code, package)),
    )


def _defined_functions(value: object, owner: str) -> tuple[types.FunctionType, ...]:
    """The functions a namespace value defined in the module ``owner`` stands for: the function
    itself, or a class's methods (static, class and property ones unwrapped)."""
    kind = type(value)
    if issubclass(kind, types.FunctionType):
        function = cast(types.FunctionType, value)
        return (function,) if function.__module__ == owner else ()
    if issubclass(kind, type) and cast(type, value).__dict__.get("__module__") == owner:
        members = tuple(cast(type, value).__dict__.values())
        unwrapped = tuple(
            inner
            for member in members
            for inner in _member_functions(member)
        )
        return tuple(cast(types.FunctionType, inner) for inner in unwrapped if issubclass(type(inner), types.FunctionType))
    return ()


def _member_functions(member: object) -> tuple[object, ...]:
    """The functions a class member stands for (a method, or what a staticmethod, classmethod or
    property holds)."""
    kind = type(member)
    if issubclass(kind, (staticmethod, classmethod)):
        return (cast("staticmethod[..., object]", member).__func__,)
    if issubclass(kind, property):
        held = cast(property, member)
        return (held.fget, held.fset, held.fdel)
    return (member,)


def _codes_within(code: types.CodeType) -> tuple[types.CodeType, ...]:
    """A code object and the code nested in it (inner functions, comprehensions, class bodies)."""
    nested = tuple(const for const in code.co_consts if isinstance(const, types.CodeType))
    return (code, *(inner for each in nested for inner in _codes_within(each)))


_IMPORT_NAME = dis.opmap["IMPORT_NAME"]
_EXTENDED_ARG = dis.EXTENDED_ARG


def _code_imports(code: types.CodeType, package: str) -> frozenset[str]:
    """The modules one code object imports and the dotted module names it holds as strings. An
    import names its module by its argument (an index into ``co_names``), read from the code's bytes
    without decoding every instruction; a relative one (its depth is not read) may name the module
    under ``package`` or under any package above it."""
    dotted = frozenset(const for const in code.co_consts if isinstance(const, str) and _is_dotted_name(const))
    names = frozenset(code.co_names[index] for index in _import_arguments(code.co_code))
    ancestors = tuple(package.rsplit(".", depth)[0] for depth in range(package.count(".") + 1)) if package else ()
    relative = frozenset(f"{ancestor}.{name}" for ancestor in ancestors for name in names)
    return dotted | names | relative


def _import_arguments(raw: bytes) -> tuple[int, ...]:
    """The arguments of the import instructions in a code object's bytes (two bytes an instruction —
    operation and argument — an ``EXTENDED_ARG`` before one carrying its higher bits)."""
    operations = raw[::2]
    found: tuple[int, ...] = ()
    at = operations.find(_IMPORT_NAME)
    while at != -1:
        found = (*found, _argument(raw, at))
        at = operations.find(_IMPORT_NAME, at + 1)
    return found


def _argument(raw: bytes, at: int) -> int:
    """The full argument of the instruction at index ``at`` (counted in instructions)."""
    higher = _argument(raw, at - 1) << 8 if at > 0 and raw[2 * (at - 1)] == _EXTENDED_ARG else 0
    return higher | raw[2 * at + 1]


def _is_dotted_name(text: str) -> bool:
    """Whether a string reads as a dotted module name (``pkg.mod``)."""
    return "." in text and all(part.isidentifier() for part in text.split("."))


def _loaded_stat(module: object) -> _Stat | None:
    """The file a loaded module was loaded from, as it stats now (None when it has none — a built-in
    module — or the file is gone)."""
    filename = getattr(module, "__file__", None)
    return _stat_of(filename) if isinstance(filename, str) else None


def _stat_of(filename: str) -> _Stat | None:
    """The file as the key compares it (None when it is gone)."""
    try:
        status = os.stat(filename)
    except OSError:
        return None
    return _Stat(filename, status.st_mtime_ns, status.st_ctime_ns, status.st_size, status.st_ino)


# The content digest of each file this process has read, by how it stat'ed (a cache: a file that
# stats the same is not read twice in one process — the answers of one identity name mostly the same files).
_DIGESTS: dict[_Stat, str] = {}

# How many files are read at once. A file not in the page cache waits on the disk: one at a time, the
# 1,127 module files of the screen job's closure test took 16.3 s on a busy machine (agora-redesign #3777).
_READERS = 32


def _read_ahead(statuses: tuple[_Stat, ...]) -> None:
    """Read the files whose digest this process does not know yet side by side, so that ``_digest``
    answers from its cache."""
    unread = tuple(status for status in statuses if status not in _DIGESTS)
    if not unread:
        return
    with ThreadPoolExecutor(max_workers=_READERS) as pool:
        tuple(pool.map(_digest, unread))


def _digest(status: _Stat) -> str | None:
    """The sha256 of the file's content (None when it cannot be read)."""
    known = _DIGESTS.get(status)
    if known is not None:
        return known
    try:
        with open(status.path, "rb") as handle:
            digest = hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError:
        return None
    _DIGESTS[status] = digest
    return digest


def _read_entry(path: Path) -> "_Entry[object] | None":
    """The stored answer, or None when absent or unreadable (the caller computes it again)."""
    try:
        with path.open("rb") as handle:
            entry = pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError, ImportError, IndexError):
        return None
    return entry if isinstance(entry, _Entry) else None


def _write_entry(path: Path, entry: "_Entry[T]") -> None:
    """Store the answer atomically (a reader never sees half a file); a failed write only costs
    the next process one analysis."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(handle, "wb") as out:
            pickle.dump(entry, out, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(temporary, path)
    except OSError:
        return


def _drop_least_used(place: Path) -> None:
    """Keep the ``_KEPT`` answers of an identity used most recently and delete the others."""
    for path in _entries_most_recent_first(place)[_KEPT:]:
        with contextlib.suppress(OSError):
            path.unlink()
