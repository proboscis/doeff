"""An analysis answer kept on disk, reused while every module file it was computed under has the same content.

A closure test (does a job's Program leave any effect unanswered under its foundation?) reads
hundreds of definitions and costs 1–7 s even with the expanded trees cached — spread over the
whole reader, so no single part is worth cutting (agora-redesign #1586 / #1645). Its answer is a
function of what the analysis reads: the sources of the modules it follows, and the runtime values
(module attributes, handler marks, signatures, the foundation itself) that importing those modules
made. Both are covered by the files of the modules the process had loaded when the answer was
computed, so the answer is stored with the module name and the content digest of every one of them.

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

What the key does not cover (agora-redesign #1645): a value decided at import time from a file that
is not a module (or from the environment), and a module loaded only in the reading process that
rewrites other modules' values when imported. agora-controllers reads neither at import time.

``DOEFF_EFFECT_ANALYZER_RESULT_CACHE=off`` turns this cache off (nothing read or written); the
expanded-tree cache's ``DOEFF_EFFECT_ANALYZER_CACHE=off`` turns both off. A miss (the analysis run
in full, and the digests stored with its answer) is observed like an uncached expansion, so a
test-time budget subtracts it.
"""

import contextlib
import hashlib
import os
import pickle
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path
from typing import Generic, TypeVar, cast

from doeff_effect_analyzer import env_places
from doeff_effect_analyzer.program_effects import _EXPANSION_OBSERVERS, _hy_cache_dir

T = TypeVar("T")

# v1: the answer with the stat of every module file loaded when it was computed.
# v2: each file also names its module, so an answer from another checkout is not reused (#1864).
# v3: each file is named by its content (its path and stat only spare reading it), and an identity
#     is a directory of answers named by the contents they were computed under (#3777).
# Entries of an older shape have another key and are never read (nothing deletes them).
_FORMAT = "v3"

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
    named: compute without the cache). The answer must pickle."""
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
        value = compute()
        entry = _Entry(material=_loaded_material(), value=value)
        _write_entry(place / _entry_name(entry.material), entry)
        _drop_least_used(place)
    return value


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


def _loaded_material() -> tuple[_Material, ...]:
    """Every module file this process has loaded, by module name, with its content's digest.
    ``__main__`` is the program that started the process (another runner for the same analysis), not a module of it."""
    located = tuple(
        _Located(module=name, stat=status)
        for name, module in tuple(sys.modules.items())
        if name != "__main__" and (status := _loaded_stat(module)) is not None
    )
    _read_ahead(tuple(place.stat for place in located))
    material = (
        _Material(module=place.module, sha256=digest, stat=place.stat)
        for place in located
        if (digest := _digest(place.stat)) is not None
    )
    return tuple(sorted(material, key=lambda used: used.module))


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
