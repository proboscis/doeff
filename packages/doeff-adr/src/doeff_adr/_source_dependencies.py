"""Conservative validity check of the in-project sources read by a dynamic collection record (#1459)."""

import hashlib
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Upper bound of the timestamp granularity (ns) -- if the last change of a file (mtime, ctime) is within this width of the time the record was taken, it can be
# rewritten within the same timestamp tick without stat changing (the same shape as git's racy-git). The coarse kernel ctime tick is a jiffy
# (4 ms on atlas 6.8), and coarse file systems use 1-2 seconds (ext3 and HFS+ 1 second, FAT 2 seconds), so we use 2 seconds.
RACY_WINDOW_NS: int = 2_000_000_000


@dataclass(frozen=True)
class SourceDependency:
    """Record of one source that was read. recorded_ns = the wall-clock time the record was taken (an old record without this field has 0 -- stat is not trusted, it is verified once by hash,
    and rewritten with the verification time)."""

    path: str
    digest: str
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int
    recorded_ns: int = 0


def snapshot(path: Path, relative: str, digest: str, recorded_ns: int | None = None) -> SourceDependency:
    """Record of the current stat of path. If recorded_ns is not passed, the wall-clock time after reading stat is used as the record time."""
    status: os.stat_result = path.stat()
    return SourceDependency(
        relative,
        digest,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
        status.st_dev,
        status.st_ino,
        time.time_ns() if recorded_ns is None else recorded_ns,
    )


def stat_is_trusted(dependency: SourceDependency) -> bool:
    """Pure: whether a record whose stat matches may be trusted without reading the hash -- only when the last change of the file is at least RACY_WINDOW_NS
    before the time the record was taken (a rewrite within the same timestamp tick does not show in stat, so records within that width are verified by hash -- git's racy-git)."""
    return max(dependency.mtime_ns, dependency.ctime_ns) + RACY_WINDOW_NS < dependency.recorded_ns


@dataclass(frozen=True)
class SourceSnapshot:
    sources: tuple[SourceDependency, ...]


def local_source(origin: str, root: Path) -> Path | None:
    """Impure (resolves a path): module ``__file__`` -> the resolved path of a .py / .hy inside root (None if outside root, under .venv, or not a source)."""
    if Path(origin).suffix not in {".py", ".hy"}:
        return None
    path: Path = Path(origin).resolve()
    if path.is_relative_to(root) and ".venv" not in path.relative_to(root).parts:
        return path
    return None


def loaded_origins() -> frozenset[str]:
    """Names of the files (``__file__``) of the currently loaded modules -- does not read or resolve paths (called inside the import window)."""
    return frozenset(
        origin
        for module in tuple(sys.modules.values())
        if module is not None and isinstance(origin := vars(module).get("__file__"), str)
    )


@dataclass(frozen=True)
class LoadedSource:
    """One loaded local source -- the resolved path used as the ordering key, and its record."""

    path: Path
    dependency: SourceDependency


@dataclass(frozen=True)
class ProviderFound:
    """sha256 of the current file of the macro provider module (None if unreadable -- it matches no record's digest)."""

    digest: str | None


@dataclass(frozen=True)
class ProviderMissing:
    """The file of the macro provider module cannot be found."""


ProviderState = ProviderFound | ProviderMissing


@dataclass
class DependencyChecks:
    """Observations of one collection. Duplicate dependency files share the stat and hash results.

    Macro providers are likewise examined only once per module name -- many of the recorded files (420 in agora) point at the same ~9
    providers, and re-examining per file meant 3,700 stats and path constructions.

    The loaded local sources kept in a real-value record are also resolved, read and hashed only once per module file (``sources_of``) -- re-reading everything for each
    real-value file added a cost proportional to the number of loaded modules (growing as the session progresses) to the import window, and
    only the later files of a session exceeded the doeff-hy-pytest load limit. There is one root per collection.
    """

    _mut_files: dict[str, SourceDependency | None] = field(default_factory=dict)
    _mut_hashes: dict[str, str] = field(default_factory=dict)
    _mut_providers: dict[str, ProviderState] = field(default_factory=dict)
    _mut_loaded: dict[str, LoadedSource | None] = field(default_factory=dict)
    _mut_stat_hits: int = 0
    _mut_checks: int = 0
    _mut_rebuilds: int = 0
    _mut_seconds: float = 0.0
    clock: Callable[[], int] = time.time_ns

    def macro_provider(self, module: str, find: Callable[[str], ProviderState]) -> ProviderState:
        """State of the current file of ``module`` (the module at the runtime hook that accumulates records). Lookup (``find``) uses only the single implementation held by
        the record cache side."""
        known = self._mut_providers.get(module)
        if known is None:
            known = find(module)
            self._mut_providers[module] = known
        return known

    def sources_of(self, origins: frozenset[str], root: Path) -> SourceSnapshot:
        """Records of the local sources inside root among the files of modules already loaded when the real value was made (names gathered by ``loaded_origins``).
        Adds no imports."""
        found: dict[Path, SourceDependency] = {
            loaded.path: loaded.dependency
            for origin in origins
            if (loaded := self._loaded_source(origin, root)) is not None
        }
        return SourceSnapshot(tuple(found[path] for path in sorted(found)))

    def _loaded_source(self, origin: str, root: Path) -> LoadedSource | None:
        """Record of one module file (resolved and read only once per collection)."""
        if origin not in self._mut_loaded:
            path: Path | None = local_source(origin, root)
            self._mut_loaded[origin] = (
                None
                if path is None
                else LoadedSource(
                    path,
                    snapshot(path, path.relative_to(root).as_posix(), hashlib.sha256(path.read_bytes()).hexdigest()),
                )
            )
        return self._mut_loaded[origin]

    def verify(self, root: Path, saved: tuple[SourceDependency, ...]) -> SourceSnapshot | str:
        started: float = time.perf_counter()
        try:
            return self._verify(root, saved)
        finally:
            self._mut_seconds += time.perf_counter() - started

    def _verify(self, root: Path, saved: tuple[SourceDependency, ...]) -> SourceSnapshot | str:
        refreshed: list[SourceDependency] = []
        root_text: str = str(root)
        for dependency in saved:
            # The key is built as a string -- the recorded path is a canonical posix path relative to rootdir, so it becomes the same string as ``str(root / path)``.
            # Avoid building a Path and converting back to a string per check (nearly 10,000 times for 420 files).
            key: str = os.path.join(root_text, dependency.path)
            self._mut_checks += 1
            if key not in self._mut_files:
                try:
                    self._mut_files[key] = snapshot(Path(key), dependency.path, "")
                except OSError:
                    self._mut_files[key] = None
            current: SourceDependency | None = self._mut_files[key]
            if current is None:
                self._mut_rebuilds += 1
                return f"dependency source of the actual value is missing: {dependency.path}"
            if (
                current.size,
                current.mtime_ns,
                current.ctime_ns,
                current.device,
                current.inode,
            ) == (
                dependency.size,
                dependency.mtime_ns,
                dependency.ctime_ns,
                dependency.device,
                dependency.inode,
            ) and stat_is_trusted(dependency):
                self._mut_stat_hits += 1
                refreshed.append(dependency)
                continue
            if key not in self._mut_hashes:
                self._mut_hashes[key] = hashlib.sha256(Path(key).read_bytes()).hexdigest()
            digest: str = self._mut_hashes[key]
            if digest != dependency.digest:
                self._mut_rebuilds += 1
                return f"dependency source of the actual value changed: {dependency.path}"
            refreshed.append(
                SourceDependency(
                    current.path,
                    digest,
                    current.size,
                    current.mtime_ns,
                    current.ctime_ns,
                    current.device,
                    current.inode,
                    self.clock(),
                )
            )
        return SourceSnapshot(tuple(refreshed))

    def report(self) -> str:
        return (
            f"doeff-adr: actual-value dependency check: {len(self._mut_files)} files; checks: {self._mut_checks}; "
            f"stat matches: {self._mut_stat_hits}; hashed: {len(self._mut_hashes)} files; "
            f"{self._mut_seconds:.6f} s; rebuilt on dependency change: {self._mut_rebuilds} files"
        )
