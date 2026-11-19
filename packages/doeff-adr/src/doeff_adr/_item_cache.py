"""Cache of item records for Hy test files -- keyed by the hash of the source content and the record of the macros the expansion used.

A macro that creates items puts records into the module through its expansion (``doeff_hy._pytest_items``). This module saves the records of files imported once during collection
and lets later collections look them up without importing. It does not touch the Hy importer.

- Location: ``<dir>/<sha256 of source>.json``. The dir is decided in one place, :func:`items_cache_dir_for` (ini -> environment variable
    ``DOEFF_ADR_ITEMS_CACHE`` -> ``~/.cache/doeff-adr/pytest-items``, in that order; the plugin reads the ini and passes it). The content
    hash is the key, so files with the same content look up the same record across worktrees.
- Validity: look up by the sha256 of the source (the key); an entry is valid only when the record of macros the expansion used at save time matches the current environment (checked the same way as .pyc --
    it covers only the bodies of functions executed during expansion), the runtime entry point that puts records (the sha256 of the file of the :data:`RECORDING_RUNTIME` module) is still
    the same, and the record format version is the same. An item record is a string constant produced by the expansion, and at import time the runtime entry point just puts it into the module,
    so if the expansion and the runtime entry point are the same, the record is the same. Records look up macros again by module name, so they are not tied to absolute paths.
- How to take and check the expansion record uses the entry points of doeff-hy's ``doeff_hy_bytecode_guard._loader_hooks`` (``loaded_record`` and
    ``record_is_current_here`` -- the same single way of checking as the mechanism that finds stale bytecode, #1292). Do not write a second way of checking here.
    It used to check by the sha256 of the macro provider file, and any line change in doeff_hy's macros.hy made every Hy test file be
    collected by import again, so the doeff daily run exceeded its time limit (the 10-12 04:00 and 08:00 runs -- child of #3769).
"""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile
import types
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal

from doeff_hy._pytest_items import Record, decode_records, encode_records
from doeff_hy_bytecode_guard._loader_hooks import (
    file_sha256,
    loaded_record,
    record_from_json,
    record_is_current_here,
    record_to_json,
)

from doeff_adr._env_places import ITEMS_CACHE_ENV, environment_value
from doeff_adr._source_dependencies import (
    DependencyChecks,
    ProviderFound,
    ProviderMissing,
    ProviderState,
    SourceDependency,
    SourceSnapshot,
    loaded_origins,
)

TYPE_CHECKING = False  # the type checker reads branches on this name as true (a public name used only for the annotation of the record type)
if TYPE_CHECKING:
    from doeff_hy_bytecode_guard._records import MacroRecord

# Raise this when the record shape or the way of deciding the key changes (caches of older versions are not read). 2 = find the macro provider by the way doeff_hy_bytecode_guard traces.
# 3 = add module fixture records.
# 4 = save the actual values after import, explicit ids, and the state of in-project sources (#1459).
# Fixture aliases are also saved by their pytest registered names (#1551). Do not use names from old records.
# 6 = instead of the sha256 of the macro provider file, save the record of macros the expansion used and the file of the runtime entry point that puts records (child of #3769).
CACHE_FORMAT = 6

#: The module of the runtime entry point that puts records -- where ``record-at-import``, which the expansion calls at import time, lives. The expansion record does not cover this code
#: (it is not run during expansion), so it is covered separately by the file sha256.
RECORDING_RUNTIME = "doeff_hy._pytest_items"

#: Stand-in for the sha256 of a file that could not be read (never equals a hex sha256 -- the record is always judged stale).
_UNREADABLE = "unreadable"


def items_cache_dir_for(configured: str, rootpath: Path) -> Path:
    """The only place that decides the record directory. ``configured`` is the value of the pytest ini ``doeff_adr_items_cache``.

    Order: the ini value if present (the project setting is the narrowest) -> otherwise the environment variable ITEMS_CACHE_ENV (the runner passes it per process) ->
    if neither, under the user's cache dir (``~/.cache/doeff-adr/pytest-items``). The environment variable step exists because the daily whole-system verification task
    swaps HOME for an empty dir on every run (with the default location the records were empty every time, and the node-a daily run imported 309 files for collection).
    Relative paths of the ini and the environment variable are resolved from rootdir.
    """
    named = configured.strip() or (environment_value(ITEMS_CACHE_ENV) or "").strip()
    if not named:
        return Path.home() / ".cache" / "doeff-adr" / "pytest-items"
    path = Path(named).expanduser()
    return path if path.is_absolute() else rootpath / path


@dataclass(frozen=True)
class RuntimeDependency:
    """One saved module of the runtime entry point that puts records (the module name and the file sha256 at save time). The file path
    changes per worktree, so it is not made a key -- at read time the current file is looked up from the module name."""

    module: str
    digest: str


@dataclass(frozen=True)
class ExpansionUnknown:
    """There is no record of the macros the expansion of the imported test file used (the reason as a sentence) -- the record of that file is not saved."""

    reason: str


class MalformedCacheEntryError(ValueError):
    """The cache file has a different shape (written by a doeff-adr of another version, half-written, etc.)."""


MalformedCacheEntry = MalformedCacheEntryError


FixtureScope = Literal["function", "class", "module", "package", "session"]


def fixture_scope(value: object) -> FixtureScope:
    """Turn the fixture scope word into a closed type (outside the vocabulary is MalformedCacheEntry -- do not silently make it function)."""
    match value:
        case "function":
            return "function"
        case "class":
            return "class"
        case "module":
            return "module"
        case "package":
            return "package"
        case "session":
            return "session"
        case _:
            raise MalformedCacheEntry(f"fixture scope is outside the vocabulary: {value!r}")


@dataclass(frozen=True)
class FixtureRecord:
    """One module fixture -- a record for placing, in a stand-in module, a stand-in fixture with the same name, scope, and arguments.

    Made at save time from the pytest fixture mark of the real module (the definition source of the fixture stays the real module -- the stand-in fixture
    only calls the real function at setup). Fixtures with params / autouse are not recorded
    (they change the collection result, so that file is collected by import as before).
    """

    attribute: str
    name: str
    scope: FixtureScope
    argnames: tuple[str, ...]
    generator: bool


@dataclass(frozen=True)
class CacheEntry:
    """The content of one cache file (the shape decoded at the JSON boundary)."""

    format: int
    records: tuple[str, ...]
    expansion: "MacroRecord"
    runtime: tuple[RuntimeDependency, ...]
    fixtures: tuple[FixtureRecord, ...]
    sources: tuple[SourceDependency, ...]


@dataclass(frozen=True)
class CacheHit:
    """A record looked up from a valid cache."""

    records: tuple[Record, ...]
    fixtures: tuple[FixtureRecord, ...]


@dataclass(frozen=True)
class CacheMiss:
    """No usable record in the cache -- the reason is reported at the end of collection."""

    reason: str


CacheLookup = CacheHit | CacheMiss


def _entry_path(source: Path, cache_dir: Path) -> Path:
    """Decide the path of the cache file from the hash of the source content."""
    return cache_dir / f"{hashlib.sha256(source.read_bytes()).hexdigest()}.json"


def _module_file(name: str) -> Path | None:
    """Look up the current file from the module name (if imported, that file; otherwise search -- the parent package of the provider is already imported)."""
    module = sys.modules.get(name)
    origin = module.__file__ if module is not None else None
    if origin is None:
        spec = importlib.util.find_spec(name)
        origin = spec.origin if spec is not None else None
    if origin is None or not origin.endswith((".py", ".hy")):
        return None
    return Path(origin)


def _provider_state(name: str) -> ProviderState:
    source = _module_file(name)
    return ProviderMissing() if source is None else ProviderFound(file_sha256(str(source)))


def _parse_entry(text: str) -> CacheEntry:
    """Decode the JSON of a cache file into types (the only JSON boundary). A different shape is MalformedCacheEntry."""
    try:
        raw = json.loads(text)
        format_ = int(raw["format"])
        if format_ != CACHE_FORMAT:
            raise MalformedCacheEntry(f"cache format version differs ({format_} != {CACHE_FORMAT})")
        expansion = record_from_json(raw["expansion"])
        if expansion is None:
            raise MalformedCacheEntry("expansion record shape differs")
        return CacheEntry(
            format=format_,
            records=tuple(str(t) for t in raw["records"]),
            expansion=expansion,
            runtime=tuple(
                RuntimeDependency(str(d["module"]), str(d["digest"])) for d in raw["runtime"]
            ),
            fixtures=tuple(
                FixtureRecord(
                    attribute=str(f["attribute"]),
                    name=str(f["name"]),
                    scope=fixture_scope(f["scope"]),
                    argnames=tuple(str(a) for a in f["argnames"]),
                    generator=bool(f["generator"]),
                )
                for f in raw["fixtures"]
            ),
            sources=tuple(SourceDependency(**dependency) for dependency in raw["sources"]),
        )
    except MalformedCacheEntry:
        raise
    except (ValueError, KeyError, TypeError) as exc:
        raise MalformedCacheEntry(str(exc)) from exc


def _dump_entry(entry: CacheEntry, source: Path) -> str:
    """CacheEntry to JSON (the writing-side boundary). ``source`` is a note for humans to read."""
    return json.dumps(
        {
            "format": entry.format,
            "source": str(source),
            "records": list(entry.records),
            "expansion": record_to_json(entry.expansion),
            "runtime": [{"module": d.module, "digest": d.digest} for d in entry.runtime],
            "sources": [asdict(dependency) for dependency in entry.sources],
            "fixtures": [
                {
                    "attribute": f.attribute,
                    "name": f.name,
                    "scope": f.scope,
                    "argnames": list(f.argnames),
                    "generator": f.generator,
                }
                for f in entry.fixtures
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def read_cached(source: Path, cache_dir: Path, root: Path, checks: DependencyChecks) -> CacheLookup:
    """Look up the record of the source from the cache. If missing or stale (the runtime entry point that puts records or the macros the expansion used changed, or the format version differs),
    CacheMiss with the reason."""
    entry_path = _entry_path(source, cache_dir)
    try:
        text = entry_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CacheMiss("no record (a file with this content has never been imported during collection)")
    try:
        entry = _parse_entry(text)
    except MalformedCacheEntry as exc:
        return CacheMiss(f"cache shape differs: {exc}")
    for dependency in entry.runtime:
        match checks.macro_provider(dependency.module, _provider_state):
            case ProviderMissing():
                return CacheMiss(f"runtime entry point that puts records {dependency.module} not found")
            case ProviderFound(digest):
                if digest != dependency.digest:
                    return CacheMiss(f"runtime entry point that puts records {dependency.module} changed")
    if not record_is_current_here(entry.expansion):
        return CacheMiss("the macros used by the expansion changed")
    return _read_runtime_entry(entry_path, entry, source, root, checks)


def _read_runtime_entry(
    entry_path: Path, entry: CacheEntry, source: Path, root: Path, checks: DependencyChecks
) -> CacheLookup:
    """For a record whose macro version matches, check the dependencies of the actual values and update stat."""
    refreshed: SourceSnapshot | str = checks.verify(root, entry.sources)
    if isinstance(refreshed, str):
        return CacheMiss(refreshed)
    if refreshed.sources != entry.sources:
        _write_entry(entry_path, replace(entry, sources=refreshed.sources), source)
    return CacheHit(tuple(decode_records(entry.records)), entry.fixtures)


@dataclass(frozen=True)
class PendingEntry:
    """One record checked right after import -- it is written at the end of collection. Writing, and reading and hashing of already-loaded sources, are done outside the import interval measured by the load limit of doeff-hy-pytest
    (a cost of the collection tooling, not the weight of the test file).
    expansion = the record of macros the expansion used at import (:func:`expansion_of`).
    origins = the files of modules that were already loaded at import time on which the actual-value record depends (empty for static records)."""

    source: Path
    module: types.ModuleType
    fixtures: tuple[FixtureRecord, ...]
    records: tuple[Record, ...]
    expansion: "MacroRecord"
    origins: frozenset[str]


def expansion_of(module: types.ModuleType) -> "MacroRecord | ExpansionUnknown":
    """The record of macros the expansion of the imported test file used (what is attached to the code returned by the loader entry point -- the reason if absent). Look it up right after import
    (if the same file is read again later, the memo is replaced by the record of that code)."""
    file = vars(module).get("__file__")
    record = loaded_record(file) if isinstance(file, str) else None
    if record is None:
        return ExpansionUnknown("no expansion record (did not go through the loader entry point, or the code has no record)")
    return record


def pending_entry(
    source: Path,
    module: types.ModuleType,
    fixtures: tuple[FixtureRecord, ...],
    records: tuple[Record, ...],
    expansion: "MacroRecord",
    dynamic: bool,
) -> PendingEntry:
    """Turn a checked record into a form held until written. For an actual-value record, only collect the names of files of modules already loaded at this point (do not read them)."""
    return PendingEntry(
        source, module, fixtures, records, expansion, loaded_origins() if dynamic else frozenset()
    )


def write_cached(pending: PendingEntry, cache_dir: Path, root: Path, checks: DependencyChecks) -> None:
    """Save a checked record. For an actual-value record, also record the local sources that were already loaded at import time (reading each same source only once through the observation checks of one collection
    run)."""
    entry_path = _entry_path(pending.source, cache_dir)
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    sources: tuple[SourceDependency, ...] = checks.sources_of(pending.origins, root).sources
    entry = CacheEntry(
        CACHE_FORMAT,
        tuple(encode_records(pending.records)),
        pending.expansion,
        (_runtime_dependency(RECORDING_RUNTIME, checks),),
        pending.fixtures,
        sources,
    )
    _write_entry(entry_path, entry, pending.source)


def _runtime_dependency(module: str, checks: DependencyChecks) -> RuntimeDependency:
    """The current file sha256 of the module of the runtime entry point that puts records (a file not found or unreadable is written
    as a mark that matches no sha256 -- if not written, a change to that file would be missed)."""
    match checks.macro_provider(module, _provider_state):
        case ProviderFound(digest) if digest is not None:
            return RuntimeDependency(module, digest)
        case _:
            return RuntimeDependency(module, _UNREADABLE)


def _write_entry(entry_path: Path, entry: CacheEntry, source: Path) -> None:
    """Write atomically the first save and the stat update of dependency files whose content was identical."""
    handle, temporary = tempfile.mkstemp(dir=entry_path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(_dump_entry(entry, source))
    os.replace(temporary, entry_path)


def forget_cached(source: Path, cache_dir: Path) -> None:
    """Delete the record of the source from the cache (when setup finds a mismatch with the real thing)."""
    _entry_path(source, cache_dir).unlink(missing_ok=True)
