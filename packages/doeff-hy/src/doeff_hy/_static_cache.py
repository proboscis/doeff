"""Expansion cache for doeff-hy-check.

Even checking just three changed files expands every .hy under the roots they import (105 in the core of the agora screen), which
took 36 seconds. Dependency expansion yields the same result as long as what it depended on is unchanged, so we
save the expanded Python, the position mapping and the findings, and the next run expands only what changed.

What an expansion depended on (built the same way as the bytecode record on the test-run side):
- The content of the source being expanded, its module name, and its path relative to the root (these affect diagnostic paths and import resolution).
- The record of macros used (``doeff_hy_bytecode_guard._loader_hooks.macro_recording``): the Hy version, the macros the expansion actually used (provider
  module name, macro name, and a digest of the closure of the macro function's code, including helpers the macro calls, constants it reads and imports in its body), the names looked up
  in the table that were not found and the providers that put macros into the table, and the sha256 of files for modules covered per file (such as targets of imports inside a macro body).
  Post-processing of the type-check expansion that cannot be traced from macros is included via the closure digest of ``POST_PROCESSING`` (``project``) in doeff_hy._static_check
  (``also``). Changes to unused macros, annotations or line numbers alone do not trigger a rebuild.
Previously the key held only a fingerprint of the whole doeff_hy package plus the .hy files reached through requires collected by reading the source with the Hy reader.
A single doeff_hy commit unrelated to expansion rebuilt every expansion (a one-file measurement went from 8 to 72 seconds), while changing a helper
.py called by a macro still hit a stale expansion.

The macros used are only known after expansion, so storage has two levels (same shape as the doeff-effect-analyzer expansion store, agora-redesign
#3598): under a location determined by the source content, module name, relative path and this file's version (<cache dir>/<first 2 chars>/<location>/),
put one entry per record (<record fingerprint>.json), and read only an entry whose record matches the current environment (``record_is_current_here``: look up the used
macros by module name in the current environment and compare digests).
Work trees with different doeff versions keep their entries side by side without overwriting each other.

The storage format is one JSON file per entry. A corrupt file is skipped as unreadable and expanded again.

To reduce opens in a warm run, we also keep a copy of the whole closure bundled into one file per set of checked files (the bundle; see the section below). The bundle is a copy; the entries are the source of truth.
"""

import base64
import hashlib
import json
import os
import sqlite3
import sys
import time
import zlib
from array import array
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, overload

from doeff_hy_bytecode_guard._loader_hooks import (
    record_fingerprint,
    record_from_json,
    record_is_current_here,
    record_to_json,
)

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard._records import MacroRecord

# Version of the storage format (raise it when the format changes; it is part of the location name, so entries of old versions are not read).
# 2 = verify against the record of files whose expansion succeeded.
# 3 = verify against a per-macro record of the macros the expansion used (record row = table name, provider module name, macro name, digest).
# 4 = hold position mappings as one string of a packed array compressed down, rather than a list of lists of ints (PackedSpans).
CACHE_VERSION = 4


@dataclass(frozen=True)
class Span:
    """A range of expanded Python (0-based line and column, end exclusive) with the corresponding Hy position (1-based); the same type as static_check.Span."""

    start: tuple[int, int]
    end: tuple[int, int]
    hy_line: int
    hy_column: int


#: Name of the storage form (same type as Span; the repr type name enters the carry digest, so the type name stays Span).
CachedSpan = Span


#: Number of ints held by one position mapping (start line, start column, end line, end column, Hy line, Hy column).
_SPAN_CELLS = 6


class PackedSpans(Sequence[Span]):
    """Holds the sequence of position mappings as one int array per file. The expansion parent holds the mappings of all checked files (5,500 per day, 8.74 million entries)
    through pyright. If each were a list or Span object it would cost 200 to 400 bytes apiece, and free-threaded CPython does not return the memory of
    discarded small objects to the OS, so the pile once built stayed as RSS during pyright (2026-11-01: the parent reached 10GB).
    With an array each costs 24 bytes, and a Span is made one at a time on read and discarded (not remembered). The storage form is one string: the array bytes compressed with zlib and
    base64-encoded (a JSON list of lists would create one list per element just by reading). It reads as the same contents, same repr and same equality as a tuple
    (the repr enters the carry digest, static_carry.own_digest)."""

    __slots__ = ("_cells", "_mut_text")

    def __init__(self, cells: array, text: str | None = None) -> None:
        self._cells = cells
        self._mut_text = text

    @classmethod
    def of(cls, spans: Iterable[Span]) -> "PackedSpans":
        """To pack a sequence of Spans (in the child process right after expansion, so the pickle sent to the parent is also one array)."""
        if isinstance(spans, PackedSpans):
            return spans
        cells = array("i")
        for span in spans:
            cells.extend((*span.start, *span.end, span.hy_line, span.hy_column))
        return cls(cells)

    @classmethod
    def from_text(cls, text: str) -> "PackedSpans":
        """To restore from the storage form (payload). An unreadable string raises ValueError (the caller skips it as an unreadable entry; deferring until use would have
        a corrupt entry bring down the whole check after pyright). The string read is reused for rewriting (the bundle is rewritten every time)."""
        try:
            raw = zlib.decompress(base64.b64decode(text, validate=True))
        except zlib.error as error:
            raise ValueError(f"cannot decode position mappings: {error}") from error
        cells = array("i")
        if len(raw) % (cells.itemsize * _SPAN_CELLS):
            raise ValueError(f"byte count {len(raw)} of position mappings is not a multiple of one mapping's size")
        cells.frombytes(raw)
        if sys.byteorder != "little":
            cells.byteswap()
        return cls(cells, text)

    def payload(self) -> str:
        """To produce the storage form (base64 of compressed bytes; little-endian 4-byte ints)."""
        if self._mut_text is None:
            cells = self._cells
            if sys.byteorder != "little":
                cells = array("i", cells)
                cells.byteswap()
            self._mut_text = base64.b64encode(zlib.compress(cells.tobytes(), 1)).decode("ascii")
        return self._mut_text

    def _span(self, number: int) -> Span:
        """To make the number-th mapping into a Span only at read time (not remembered; remembering would defeat the point of holding an array)."""
        c = self._cells
        at = number * _SPAN_CELLS
        return Span((c[at], c[at + 1]), (c[at + 2], c[at + 3]), c[at + 4], c[at + 5])

    def __len__(self) -> int:
        return len(self._cells) // _SPAN_CELLS

    @overload
    def __getitem__(self, index: int) -> Span: ...

    @overload
    def __getitem__(self, index: slice) -> tuple[Span, ...]: ...

    def __getitem__(self, index: int | slice) -> Span | tuple[Span, ...]:
        """To read the number-th mapping (a slice yields a tuple); read the same way as a tuple."""
        if isinstance(index, slice):
            return tuple(self._span(number) for number in range(*index.indices(len(self))))
        size = len(self)
        number = index + size if index < 0 else index
        if not 0 <= number < size:
            raise IndexError("position mapping index out of range")
        return self._span(number)

    def __iter__(self) -> Iterator[Span]:
        return (self._span(number) for number in range(len(self)))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, PackedSpans):
            return self._cells == other._cells
        return isinstance(other, tuple) and tuple(self) == other

    def __hash__(self) -> int:
        return hash(tuple(self))

    def __repr__(self) -> str:
        return repr(tuple(self))

    def __reduce__(self) -> tuple[object, ...]:
        return (PackedSpans, (self._cells, None))


@dataclass(frozen=True)
class CachedFinding:
    """A saved finding (same fields as static_check.Diagnostic)."""

    path: str
    line: int
    column: int
    severity: str
    rule: str
    message: str


@dataclass(frozen=True)
class CachedProjection:
    """The part of one .hy expansion result that is saved and read back, plus the record of macros the expansion used."""

    text: str
    spans: Sequence[Span]
    findings: tuple[CachedFinding, ...]
    used: "MacroRecord"
    # Names of modules the expanded Python imports (decides where to traverse; so we need not re-read the expansion text with ast on every lookup.
    # Old entries do not have it = None; the looking-up side reads it once and writes it back).
    imports: tuple[str, ...] | None = None


@dataclass(frozen=True)
class CacheMiss:
    """Reason a saved expansion cannot be used (missing, or no entry with a record matching the current environment). The caller expands again and saves."""

    reason: str


def place(text: str, module: str, relative: str) -> str:
    """Location name of a source's expansion (determined by source content, module name, relative path and storage format version; see the docstring above)."""
    digest = hashlib.sha256()
    for part in (str(CACHE_VERSION), module, relative, text):
        digest.update(part.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _place_dir(cache_dir: Path, name: str) -> Path:
    return cache_dir / name[:2] / name


def load(cache_dir: Path, name: str) -> CachedProjection | CacheMiss:
    """Within a location, read an entry whose record of macros used matches the current environment. If none, a CacheMiss with a reason (the caller expands again)."""
    directory = _place_dir(cache_dir, name)
    if not directory.is_dir():
        return CacheMiss("missing")
    for entry in _newest_first(directory):
        match _read_entry(entry):
            case CachedProjection() as cached if record_is_current_here(cached.used):
                return cached
            case _:
                continue
    return CacheMiss("no entry with a record matching the current environment")


def _newest_first(directory: Path) -> list[Path]:
    """List the entries in a location newest first. The entry matching the current environment is usually the most recently written, so old-version entries need not be read
    (a location with only one is not stat-ed)."""
    try:
        with os.scandir(directory) as scanned:
            found = [entry for entry in scanned if entry.name.endswith(".json")]
    except OSError:
        return []
    if len(found) > 1:
        found.sort(key=lambda entry: (-_mtime_ns(entry), entry.name))
    return [Path(entry.path) for entry in found]


def _mtime_ns(entry: os.DirEntry[str]) -> int:
    try:
        return entry.stat().st_mtime_ns
    except OSError:
        return 0


def _read_entry(path: Path) -> CachedProjection | CacheMiss:
    """Read one entry (CacheMiss if unreadable or malformed). The storage format version is checked against CACHE_VERSION (checking against a literal version would
    leave a version that raised CACHE_VERSION unable to read the entries it wrote itself)."""
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return CacheMiss(f"unreadable: {error}")
    return _entry_from_json(loaded)


def _entry_from_json(loaded: object) -> CachedProjection | CacheMiss:
    """Build one entry from JSON (CacheMiss if malformed; the same shape for an entry file and a bundle row)."""
    match loaded:
        case {
            "version": int(version),
            "used": used,
            "text": str(text),
            "spans": str(spans),
            "findings": list(findings),
        } if version == CACHE_VERSION:
            record = record_from_json(used)
            if record is None:
                return CacheMiss("record shape is wrong")
            try:
                return CachedProjection(
                    text,
                    PackedSpans.from_text(spans),
                    tuple(CachedFinding(**f) for f in findings),
                    record,
                    _imports_of(loaded),
                )
            except (TypeError, IndexError, ValueError) as error:
                return CacheMiss(f"field shape is wrong: {error}")
        case _:
            return CacheMiss("version or shape is wrong")


def _imports_of(loaded: object) -> tuple[str, ...] | None:
    """The entry's import names (None for an old entry without them or a malformed one)."""
    match loaded:
        case {"imports": list(names)}:
            return tuple(str(name) for name in names)
        case _:
            return None


def _entry_payload(projection: CachedProjection) -> dict[str, object]:
    payload: dict[str, object] = {
        "version": CACHE_VERSION,
        "used": record_to_json(projection.used),
        "text": projection.text,
        "spans": PackedSpans.of(projection.spans).payload(),
        "findings": [vars(f) for f in projection.findings],
    }
    if projection.imports is not None:
        payload["imports"] = list(projection.imports)
    return payload


def store(cache_dir: Path, name: str, projection: CachedProjection) -> None:
    """Save an expansion to a record entry within a location. Checking continues even if writing fails (the cache is only for speed)."""
    path = _place_dir(cache_dir, name) / f"{record_fingerprint(projection.used)}.json"
    payload = _entry_payload(projection)
    # The temporary file has a per-writer name (pid and time): even when two workers sharing the store write the same entry at once, neither writes into the other's temporary file,
    # and one of the contents is renamed into place as is. If writing fails, no temporary file is left behind.
    _write_atomically(path, payload)


def _write_atomically(path: Path, payload: object) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)


# --- Per-closure bundle -------------------------------------------------------------------------
#
# A per-.hy entry opens twice on every lookup (reading the source and reading the entry; over 700 times for a 367-file closure). The bundle puts the rows of the whole closure
# (location name, expansion, record of macros used) into one SQLite file per set of checked files, so a warm run opens
# the bundle once and updates only changed, added, or removed rows instead of rewriting the whole closure. The bundle is a copy only for speed and the entries remain the source of truth:
# a file missing from or not matching the bundle falls back to the usual lookup (decide the location from the source content and look up the entry), and only changed rows are written at the end of that run.
#
# Property of not using stale expansions (the same two checks as the entry lookup):
# - source: a row holds the source size and mtime from when the bundle was written. If the current stat is the same, the content is trusted as the same; if different, rebuild
#   the location name from the source content and compare with the row's name (a file whose only mtime got newer reuses the bundle's expansion if the name is the same).
#   To avoid trusting and missing a rewrite that changes content while keeping size and mtime, a file whose mtime is newer than RACY_NS before the bundle write time
#   becomes a row without a stat and is checked by content every time (same as git's index racy check).
# - macro: each row is checked against the current environment with ``record_is_current_here(used)`` (same as an entry).
# Version 2 = JSON Lines (first line = version, each following line = one file; rewrote the entire file on any change).
# Version 3 = SQLite database (default DELETE rollback journal: one file at rest, one open on warm read, row-level incremental updates on change).
BUNDLE_VERSION = 3
#: A file updated within this span of the bundle write time is not trusted by mtime and size (a rewrite within the same tick of a coarse clock).
RACY_NS = 2_000_000_000
#: Upper bound in seconds for waiting on a SQLite lock on a bundle before reporting the failure and continuing without the bundle.
BUNDLE_LOCK_TIMEOUT_SECONDS = 2.0


@dataclass(frozen=True)
class FileStat:
    """The source stat compared against a bundle row (size, mtime in ns)."""

    size: int
    mtime_ns: int


@dataclass(frozen=True)
class BundleRow:
    """One bundle row: module name of the file at a relative path, location name, stat when written (None if not trusted), and expansion."""

    module: str
    place: str
    stat: FileStat | None
    cached: CachedProjection


@dataclass(frozen=True)
class PlacedBundleRow:
    """One read bundle row and the relative path of the file it is for."""

    relative: str
    row: BundleRow


@dataclass(frozen=True)
class BundleLoad:
    """Result of reading a bundle: usable rows, whether a lock timed out, whether an error was reported, and relative paths of corrupt rows to repair."""

    rows: dict[str, BundleRow]
    locked: bool = False
    failed: bool = False
    corrupt_relatives: frozenset[str] = frozenset()


def bundle_key(root: Path, targets: Sequence[str]) -> str:
    """Name of the bundle (determined by the root, the set of relative paths of checked files, and the storage format version)."""
    digest = hashlib.sha256()
    for part in (str(BUNDLE_VERSION), str(CACHE_VERSION), str(root), *sorted(set(targets))):
        digest.update(part.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _bundle_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / "bundles" / f"{key}.sqlite3"


def _report_bundle_error(path: Path, action: str, detail: object) -> None:
    """Write one diagnostic line to stderr naming the bundle and failure so falling back from the bundle is never silent."""
    print(f"doeff-hy-check: bundle {path} {action} failed ({detail}); continuing without bundle", file=sys.stderr)


def _is_lock_error(error: BaseException) -> bool:
    if not isinstance(error, sqlite3.OperationalError):
        return False
    message = str(error).lower()
    return "locked" in message or "busy" in message


def _is_corrupt_db_error(error: BaseException) -> bool:
    if isinstance(error, sqlite3.OperationalError):
        return False
    return isinstance(error, sqlite3.DatabaseError)


def _connect_bundle(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=BUNDLE_LOCK_TIMEOUT_SECONDS, isolation_level=None)


def _bundle_row_from_db(
    relative: object,
    module: object,
    place_name: object,
    stat_size: object,
    stat_mtime_ns: object,
    entry_text: object,
) -> tuple[PlacedBundleRow | None, str]:
    """Build one BundleRow from a SQLite row (returns (None, reason) if malformed)."""
    if not (isinstance(relative, str) and isinstance(module, str) and isinstance(place_name, str) and isinstance(entry_text, str)):
        return None, "column type mismatch"
    try:
        loaded: object = json.loads(entry_text)
    except json.JSONDecodeError as error:
        return None, f"invalid entry JSON: {error}"
    cached = _entry_from_json(loaded)
    if isinstance(cached, CacheMiss):
        return None, cached.reason
    if stat_size is None and stat_mtime_ns is None:
        stat: FileStat | None = None
    elif (
        isinstance(stat_size, int)
        and not isinstance(stat_size, bool)
        and isinstance(stat_mtime_ns, int)
        and not isinstance(stat_mtime_ns, bool)
    ):
        stat = FileStat(stat_size, stat_mtime_ns)
    else:
        return None, "invalid stat columns"
    return PlacedBundleRow(relative, BundleRow(module, place_name, stat, cached)), ""


def load_bundle_state(cache_dir: Path, key: str) -> BundleLoad:
    """Read the SQLite bundle row by row. Missing bundle is a normal cold miss; corrupt, locked, or unreadable bundles report one line to stderr and fall back."""
    path = _bundle_path(cache_dir, key)
    try:
        if not path.exists():
            return BundleLoad({})
    except OSError as error:
        _report_bundle_error(path, "read", error)
        return BundleLoad({}, failed=True)

    connection: sqlite3.Connection | None = None
    try:
        connection = _connect_bundle(path)
        meta = dict(connection.execute("SELECT key, value FROM meta"))
        if meta.get("bundle_version") != str(BUNDLE_VERSION) or meta.get("cache_version") != str(CACHE_VERSION):
            _report_bundle_error(path, "read", f"unexpected metadata {meta!r}")
            return BundleLoad({}, failed=True)
        found: dict[str, BundleRow] = {}
        corrupt_rows = 0
        corrupt_relatives: set[str] = set()
        first_reason = ""
        for relative, module, place_name, stat_size, stat_mtime_ns, entry_text in connection.execute(
            "SELECT relative, module, place, stat_size, stat_mtime_ns, entry FROM rows"
        ):
            parsed, reason = _bundle_row_from_db(relative, module, place_name, stat_size, stat_mtime_ns, entry_text)
            if parsed is None:
                corrupt_rows += 1
                if isinstance(relative, str):
                    corrupt_relatives.add(relative)
                if not first_reason:
                    first_reason = reason
                continue
            found[parsed.relative] = parsed.row
        if corrupt_rows:
            _report_bundle_error(path, "read", f"skipped {corrupt_rows} corrupt row(s): {first_reason}")
        return BundleLoad(found, failed=corrupt_rows > 0, corrupt_relatives=frozenset(corrupt_relatives))
    except (sqlite3.Error, OSError) as error:
        _report_bundle_error(path, "read", error)
        return BundleLoad({}, locked=_is_lock_error(error), failed=True)
    finally:
        if connection is not None:
            connection.close()


def load_bundle(cache_dir: Path, key: str) -> dict[str, BundleRow]:
    """Read the bundle (empty if missing, unreadable, locked, or malformed; reports bundle failures to stderr)."""
    return load_bundle_state(cache_dir, key).rows


def bundle_signature(rows: dict[str, BundleRow]) -> dict[str, tuple[str, str, str, FileStat | None]]:
    """The value compared to decide whether to rewrite a bundle row (module name, location name, fingerprint of the record of macros the expansion used, trusted stat; the stat of a row
    with a newer mtime is not written, so None). Without the record fingerprint, a row re-expanded after a macro change would not be reflected in the bundle and would fall back to the entry every time."""
    horizon = time.time_ns() - RACY_NS
    return {
        relative: (
            row.module,
            row.place,
            record_fingerprint(row.cached.used),
            row.stat if row.stat is not None and row.stat.mtime_ns < horizon else None,
        )
        for relative, row in rows.items()
    }


def _recreate_bundle_schema(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TABLE IF EXISTS rows")
    connection.execute("DROP TABLE IF EXISTS meta")
    connection.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute(
        "CREATE TABLE rows ("
        "relative TEXT PRIMARY KEY, "
        "module TEXT NOT NULL, "
        "place TEXT NOT NULL, "
        "record_fp TEXT NOT NULL, "
        "stat_size INTEGER, "
        "stat_mtime_ns INTEGER, "
        "entry TEXT NOT NULL"
        ")"
    )
    connection.executemany(
        "INSERT INTO meta (key, value) VALUES (?, ?)",
        [("bundle_version", str(BUNDLE_VERSION)), ("cache_version", str(CACHE_VERSION))],
    )


def _write_bundle_db(
    path: Path,
    rows: dict[str, BundleRow],
    signature: dict[str, tuple[str, str, str, FileStat | None]],
    force_upsert: frozenset[str] = frozenset(),
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = _connect_bundle(path)
    try:
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("BEGIN IMMEDIATE")
        try:
            tables = {
                name
                for (name,) in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('meta', 'rows')"
                )
            }
            schema_current = False
            existing: dict[str, tuple[str, str, str, FileStat | None]] = {}
            if tables == {"meta", "rows"}:
                meta = dict(connection.execute("SELECT key, value FROM meta"))
                if meta.get("bundle_version") == str(BUNDLE_VERSION) and meta.get("cache_version") == str(CACHE_VERSION):
                    schema_current = True
                    for rel, mod, pl, fp, sz, mt in connection.execute(
                        "SELECT relative, module, place, record_fp, stat_size, stat_mtime_ns FROM rows"
                    ):
                        stat = FileStat(sz, mt) if sz is not None and mt is not None else None
                        existing[rel] = (mod, pl, fp, stat)
            if not schema_current:
                _recreate_bundle_schema(connection)
            to_delete = [rel for rel in existing if rel not in signature]
            to_upsert = [rel for rel, sig in signature.items() if existing.get(rel) != sig or rel in force_upsert]
            if schema_current and not to_delete and not to_upsert:
                connection.execute("ROLLBACK")
                return
            if to_delete:
                connection.executemany("DELETE FROM rows WHERE relative = ?", [(rel,) for rel in to_delete])
            for rel in to_upsert:
                mod, pl, fp, stat = signature[rel]
                entry_json = json.dumps(_entry_payload(rows[rel].cached), ensure_ascii=False)
                connection.execute(
                    "INSERT OR REPLACE INTO rows (relative, module, place, record_fp, stat_size, stat_mtime_ns, entry) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        rel,
                        mod,
                        pl,
                        fp,
                        stat.size if stat is not None else None,
                        stat.mtime_ns if stat is not None else None,
                        entry_json,
                    ),
                )
            connection.execute("COMMIT")
        except BaseException:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
    finally:
        connection.close()


def store_bundle(
    cache_dir: Path,
    key: str,
    rows: dict[str, BundleRow],
    *,
    report_failure: bool = True,
    force_upsert: frozenset[str] = frozenset(),
) -> None:
    """Incrementally update the SQLite bundle (writes only changed, added, or removed rows; checking continues even if writing fails)."""
    path = _bundle_path(cache_dir, key)
    signature = bundle_signature(rows)
    try:
        _write_bundle_db(path, rows, signature, force_upsert)
    except sqlite3.DatabaseError as error:
        if _is_corrupt_db_error(error):
            try:
                path.unlink(missing_ok=True)
                _write_bundle_db(path, rows, signature, force_upsert)
                return
            except (sqlite3.Error, OSError) as retry_error:
                if report_failure:
                    _report_bundle_error(path, "write", retry_error)
                return
        if report_failure:
            _report_bundle_error(path, "write", error)
    except (sqlite3.Error, OSError) as error:
        if report_failure:
            _report_bundle_error(path, "write", error)


def stat_of(path: Path) -> FileStat | None:
    """The stat of a file compared against a bundle row (size, mtime in ns; None if unreadable)."""
    try:
        status = path.stat()
    except OSError:
        return None
    return FileStat(status.st_size, status.st_mtime_ns)


def row_is_current(row: BundleRow, module: str, stat: FileStat | None) -> bool:
    """Whether a row may be trusted without reading the source (module name and stat are the same as when written, and the macros the expansion used match the current environment)."""
    return (
        row.stat is not None
        and row.stat == stat
        and row.module == module
        and record_is_current_here(row.cached.used)
    )


def default_cache_dir() -> Path:
    """Default location: the dir named by $DOEFF_HY_CHECK_CACHE (for callers pointing it at a dir that persists across runs; agora-redesign
    #3863); otherwise under the XDG cache (does not dirty the consumer repo's tree)."""
    from doeff_hy import _env_places as env_places

    configured = env_places.check_cache_setting()
    return Path(configured) if configured else env_places.cache_home() / "doeff-hy-check"
