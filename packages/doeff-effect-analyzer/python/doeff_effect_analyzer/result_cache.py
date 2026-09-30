"""An analysis answer kept on disk, reused while every module file it was computed under is unchanged.

A closure test (does a job's Program leave any effect unanswered under its foundation?) reads
hundreds of definitions and costs 1–3 s even with the expanded trees cached — spread over the
whole reader, so no single part is worth cutting (agora-redesign #1586 / #1645). Its answer is a
function of what the analysis reads: the sources of the modules it follows, and the runtime values
(module attributes, handler marks, signatures, the foundation itself) that importing those modules
made. Both are covered by the files of the modules the process had loaded when the answer was
computed, so the answer is stored with the (path, mtime, size, inode) of every one of them and
reused only while all of them still stat the same — changing one byte of any of them misses.

What the key does not cover (agora-redesign #1645): a value decided at import time from a file that
is not a module (or from the environment), and a module loaded only in the reading process that
rewrites other modules' values when imported. agora-controllers reads neither at import time.

``DOEFF_EFFECT_ANALYZER_RESULT_CACHE=off`` turns this cache off (nothing read or written); the
expanded-tree cache's ``DOEFF_EFFECT_ANALYZER_CACHE=off`` turns both off. A miss (the analysis run
in full) is observed like an uncached expansion, so a test-time budget subtracts it.
"""

import contextlib
import hashlib
import os
import pickle
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Generic, TypeVar, cast

from doeff_effect_analyzer.program_effects import _EXPANSION_OBSERVERS, _hy_cache_dir

T = TypeVar("T")

# v1: the answer with the stat of every module file loaded when it was computed.
_FORMAT = "v1"


@dataclass(frozen=True)
class _Material:
    """One module file as it was when the answer was computed."""

    path: str
    mtime_ns: int
    size: int
    inode: int


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
    """``compute()``, or its answer stored under ``identity`` while the module files it was
    computed under are unchanged. ``identity`` names the analysis and its arguments (None = the
    arguments cannot be named: compute without the cache). The answer must pickle."""
    path = _entry_path(identity)
    if path is None:
        return compute()
    stored = _read_entry(path)
    if stored is not None and all(_unchanged(material) for material in stored.material):
        # The file for this identity was written by this function with this compute (the identity
        # names the analysis), so its value is a T; pickle does not carry the type parameter.
        return cast(T, stored.value)
    with contextlib.ExitStack() as observed:
        for observer in tuple(_EXPANSION_OBSERVERS):
            observed.enter_context(observer())
        value = compute()
        _write_entry(path, _Entry(material=_loaded_module_files(), value=value))
    return value


def _entry_path(identity: tuple[str, ...] | None) -> Path | None:
    """Where the answer for ``identity`` is kept (None = not cached)."""
    if identity is None or os.environ.get("DOEFF_EFFECT_ANALYZER_RESULT_CACHE") == "off":
        return None
    trees = _hy_cache_dir()
    if trees is None:
        return None
    key = "\n".join([_FORMAT, sys.version, *identity])
    return trees / "results" / f"{hashlib.sha256(key.encode('utf-8')).hexdigest()}.pickle"


def _loaded_module_files() -> tuple[_Material, ...]:
    """Every module file this process has loaded, as it is now."""
    seen: dict[str, _Material] = {}
    for module in tuple(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if not isinstance(filename, str) or filename in seen:
            continue
        material = _stat(filename)
        if material is not None:
            seen[filename] = material
    return tuple(sorted(seen.values(), key=lambda m: m.path))


def _stat(filename: str) -> _Material | None:
    """The file as the key compares it (None when it is gone — an entry naming it no longer holds)."""
    try:
        status = os.stat(filename)
    except OSError:
        return None
    return _Material(filename, status.st_mtime_ns, status.st_size, status.st_ino)


def _unchanged(material: _Material) -> bool:
    """Whether a module file the answer was computed under is still the same file."""
    return _stat(material.path) == material


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
