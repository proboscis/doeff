"""動的な収集記録が読んだproject内sourceの保守的な有効性検査(#1459)。"""

import hashlib
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path


# 時刻の粒度の上限(ns)— file の最後の変更(mtime・ctime)が、記録を取った時刻からこの幅の内なら、同じ時刻の区切りの中で
# 書き換えられても stat が変わらない(git の racy-git と同じ形・card ki-5b70c4b62814)。粗い kernel の ctime の区切りは jiffy
# (atlas の 6.8 で 4 ms)、粗い file system は 1〜2 秒(ext3・HFS+ は 1 秒・FAT は 2 秒)なので 2 秒にする。
RACY_WINDOW_NS: int = 2_000_000_000


@dataclass(frozen=True)
class SourceDependency:
    """読んだ source 1 つの記録。recorded_ns = 記録を取った壁の時刻(この欄の無い古い記録は 0 — stat を信じず 1 度 hash で確かめ、
    確かめた時刻で書き直される)。"""

    path: str
    digest: str
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int
    recorded_ns: int = 0


def snapshot(path: Path, relative: str, digest: str, recorded_ns: int | None = None) -> SourceDependency:
    """path の今の stat の記録。recorded_ns を渡さなければ、stat を読んだ後の壁の時刻を記録の時刻にする。"""
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
    """純粋: 記録の stat が一致した時に hash を読まずに信じてよいか — file の最後の変更が、記録を取った時刻より RACY_WINDOW_NS 以上
    前の時だけ(同じ時刻の区切りの中の書き換えは stat に出ないので、その幅の内の記録は hash で確かめる — git の racy-git)。"""
    return max(dependency.mtime_ns, dependency.ctime_ns) + RACY_WINDOW_NS < dependency.recorded_ns


@dataclass(frozen=True)
class SourceSnapshot:
    sources: tuple[SourceDependency, ...]


def local_source(origin: str, root: Path) -> Path | None:
    """純粋でない(path を解く): module の ``__file__`` → root の中の .py / .hy の解いた path(root の外・.venv の下・source でない物は None)。"""
    if Path(origin).suffix not in {".py", ".hy"}:
        return None
    path: Path = Path(origin).resolve()
    if path.is_relative_to(root) and ".venv" not in path.relative_to(root).parts:
        return path
    return None


@dataclass(frozen=True)
class LoadedSource:
    """読込済みの local source 1 つ — 並べる鍵の解いた path と、その記録。"""

    path: Path
    dependency: SourceDependency


@dataclass(frozen=True)
class ProviderFound:
    """macro の提供元の module の今の file の sha256(読めなければ None — どの記録の digest とも合わない)。"""

    digest: str | None


@dataclass(frozen=True)
class ProviderMissing:
    """macro の提供元の module の file が見つからない。"""


ProviderState = ProviderFound | ProviderMissing


@dataclass
class DependencyChecks:
    """収集1回の観測。重複した依存fileはstatとhashの結果を共有する。

    macro の提供元も同じく、module 名ごとに 1 度だけ調べる — 記録の file の多く(agora では 420 file)が同じ 9 個ほどの
    提供元を指し、file ごとに調べ直すと 3,700 回の stat と path の生成になっていた(agora-redesign #1551)。

    実値の記録に残す読込済みの local source も、module の file ごとに 1 度だけ解いて読み hash を取る(``loaded_sources``)— 実値の
    file ごとに全部を読み直すと、読み込んだ module の数(session が進むほど増える)に比例した費用が import の区間に乗り、
    doeff-hy-pytest の読み込みの上限を session の後ろの file だけが越えた(card ki-b2383fcc8b25)。root は収集 1 回で 1 つ。
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
        """macro の提供元 ``module`` の今の状態。調べ方(``find``)は記録のキャッシュの側が持つ 1 つだけを使う。"""
        known = self._mut_providers.get(module)
        if known is None:
            known = find(module)
            self._mut_providers[module] = known
        return known

    def loaded_sources(self, root: Path) -> SourceSnapshot:
        """実値を作った時点で読込済みのlocal sourceを記録する。importを追加しない。"""
        origins: set[str] = {
            origin
            for module in tuple(sys.modules.values())
            if module is not None and isinstance(origin := vars(module).get("__file__"), str)
        }
        found: dict[Path, SourceDependency] = {
            loaded.path: loaded.dependency
            for origin in origins
            if (loaded := self._loaded_source(origin, root)) is not None
        }
        return SourceSnapshot(tuple(found[path] for path in sorted(found)))

    def _loaded_source(self, origin: str, root: Path) -> LoadedSource | None:
        """module の file 1 つの記録(収集 1 回で 1 度だけ解いて読む)。"""
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
            # 鍵は文字列で作る — 記録の path は rootdir からの正規の posix の相対 path なので ``str(root / path)`` と
            # 同じ文字列になる。Path を作って文字列へ戻すのを照合ごとにしない(420 file で 1 万回近い — agora-redesign #1551)。
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
            ) and stat_is_trusted(dependency):
                self._mut_stat_hits += 1
                refreshed.append(dependency)
                continue
            if key not in self._mut_hashes:
                self._mut_hashes[key] = hashlib.sha256(Path(key).read_bytes()).hexdigest()
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
                    self.clock(),
                )
            )
        return SourceSnapshot(tuple(refreshed))

    def report(self) -> str:
        return (
            f"doeff-adr: 実値の依存照合 {len(self._mut_files)} file・照合 {self._mut_checks} 回・"
            f"stat一致 {self._mut_stat_hits} 回・hash {len(self._mut_hashes)} file・"
            f"{self._mut_seconds:.6f} 秒・依存変更で再作成 {self._mut_rebuilds} file"
        )
