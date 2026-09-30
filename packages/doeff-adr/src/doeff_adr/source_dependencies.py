"""動的な収集記録が読んだproject内sourceの保守的な有効性検査(#1459)。"""

import hashlib
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class SourceDependency:
    path: str
    digest: str
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int


def snapshot(path: Path, relative: str, digest: str) -> SourceDependency:
    status: os.stat_result = path.stat()
    return SourceDependency(
        relative,
        digest,
        status.st_size,
        status.st_mtime_ns,
        status.st_ctime_ns,
        status.st_dev,
        status.st_ino,
    )


@dataclass(frozen=True)
class SourceSnapshot:
    sources: tuple[SourceDependency, ...]


def loaded_sources(root: Path) -> SourceSnapshot:
    """実値を作った時点で読込済みのlocal sourceを記録する。importを追加しない。"""
    paths: set[Path] = set()
    for module in tuple(sys.modules.values()):
        origin: object = vars(module).get("__file__") if module is not None else None
        if not isinstance(origin, str) or Path(origin).suffix not in {".py", ".hy"}:
            continue
        path: Path = Path(origin).resolve()
        if path.is_relative_to(root) and ".venv" not in path.relative_to(root).parts:
            paths.add(path)
    result: list[SourceDependency] = []
    for path in sorted(paths):
        digest: str = hashlib.sha256(path.read_bytes()).hexdigest()
        result.append(snapshot(path, path.relative_to(root).as_posix(), digest))
    return SourceSnapshot(tuple(result))


@dataclass
class DependencyChecks:
    """収集1回の観測。重複した依存fileはstatとhashの結果を共有する。"""

    _mut_files: dict[str, SourceDependency | None] = field(default_factory=dict)
    _mut_hashes: dict[str, str] = field(default_factory=dict)
    _mut_stat_hits: int = 0
    _mut_checks: int = 0
    _mut_rebuilds: int = 0
    _mut_seconds: float = 0.0

    def verify(self, root: Path, saved: tuple[SourceDependency, ...]) -> SourceSnapshot | str:
        started: float = time.perf_counter()
        try:
            return self._verify(root, saved)
        finally:
            self._mut_seconds += time.perf_counter() - started

    def _verify(self, root: Path, saved: tuple[SourceDependency, ...]) -> SourceSnapshot | str:
        refreshed: list[SourceDependency] = []
        for dependency in saved:
            path: Path = root / dependency.path
            key: str = str(path)
            self._mut_checks += 1
            if key not in self._mut_files:
                try:
                    self._mut_files[key] = snapshot(path, dependency.path, "")
                except OSError:
                    self._mut_files[key] = None
            current: SourceDependency | None = self._mut_files[key]
            if current is None:
                self._mut_rebuilds += 1
                return f"実値の依存sourceが無い: {dependency.path}"
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
            ):
                self._mut_stat_hits += 1
                refreshed.append(dependency)
                continue
            if key not in self._mut_hashes:
                self._mut_hashes[key] = hashlib.sha256(path.read_bytes()).hexdigest()
            digest: str = self._mut_hashes[key]
            if digest != dependency.digest:
                self._mut_rebuilds += 1
                return f"実値の依存sourceが変わった: {dependency.path}"
            refreshed.append(
                SourceDependency(
                    current.path,
                    digest,
                    current.size,
                    current.mtime_ns,
                    current.ctime_ns,
                    current.device,
                    current.inode,
                )
            )
        return SourceSnapshot(tuple(refreshed))

    def report(self) -> str:
        return (
            f"doeff-adr: 実値の依存照合 {len(self._mut_files)} file・照合 {self._mut_checks} 回・"
            f"stat一致 {self._mut_stat_hits} 回・hash {len(self._mut_hashes)} file・"
            f"{self._mut_seconds:.6f} 秒・依存変更で再作成 {self._mut_rebuilds} file"
        )
