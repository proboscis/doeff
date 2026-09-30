"""Hy の test file の item の記録のキャッシュ — 鍵は source の内容の hash と、macro の提供元の内容の hash(agora-redesign #1291)。

item を作る macro は、展開の式で記録を module に積む(``doeff_hy.pytest_items``)。この module は、収集で 1 度 import した
file の記録を保存し、次の収集からは import せずに引けるようにする。Hy の importer には手を入れない(#1290 の利用者の決定)。

- 置き場: ``~/.cache/doeff-adr/pytest-items/<source の sha256>.json``(pytest の ini ``doeff_adr_items_cache`` で変えられる —
  plugin が読んで引数で渡す)。内容の
  hash が鍵なので、同じ内容の file は worktree をまたいで同じ記録を引く。
- 有効判定: source の sha256 で引き(鍵)、保存した時の macro の提供元(module 名と、その file の sha256)が今も同じで、
  記録の形の版が同じ時だけ有効。提供元は module 名で覚えて読む時に今の file を引くので、絶対 path に縛られない。
- macro の提供元の一覧と file の sha256 は、doeff-hy の ``doeff_hy_bytecode_guard`` の公開の口(``macro_dependencies`` と
  ``file_sha256`` — 古い bytecode を見つける仕組み #1292 と同じ 1 つの辿り方)を使う。ここに 2 つ目の辿り方を書かない。
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

from doeff_hy.pytest_items import Record, decode_records, encode_records
from doeff_hy_bytecode_guard import file_sha256, macro_dependencies

from doeff_adr.source_dependencies import (
    DependencyChecks,
    SourceDependency,
    SourceSnapshot,
    loaded_sources,
)

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "doeff-adr" / "pytest-items"
# 記録の形か鍵の決め方を変えたら上げる(古い版のキャッシュは読まない)。2 = macro の提供元を doeff_hy_bytecode_guard の辿り方で求める。
# 3 = module の fixture の記録を足す(agora-redesign #1227 の案 B)。
# 4 = import後の実値・明示idとproject内sourceの状態を保存する(#1459)。
# 5 = module の pytest_generate_tests は記録から再現できないので保存しない(#1551)。
CACHE_FORMAT = 5


@dataclass(frozen=True)
class MacroDependency:
    """保存した macro の提供元 1 つ(module 名と、保存した時の file の sha256)。file の path は worktree で変わるので
    鍵にしない — 読む時に module 名から今の file を引く。"""

    module: str
    digest: str


class MalformedCacheEntryError(ValueError):
    """キャッシュの file の形が違う(版の違う doeff-adr が書いた・書きかけ等)。"""


MalformedCacheEntry = MalformedCacheEntryError


FixtureScope = Literal["function", "class", "module", "package", "session"]


def fixture_scope(value: object) -> FixtureScope:
    """fixture の scope の語を閉じた型へ(語彙の外は MalformedCacheEntry — 黙って function にしない)。"""
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
            raise MalformedCacheEntry(f"fixture の scope が語彙の外: {value!r}")


@dataclass(frozen=True)
class FixtureRecord:
    """module の fixture 1 つ — 仮の module に同じ名・scope・引数の仮の fixture を置くための記録。

    保存の時に本物の module の pytest の fixture の印から作る(fixture の定義元は本物の module のまま — 仮の fixture は
    setup で本物の関数を呼ぶだけ・agora-redesign #1227 の案 B)。params / autouse つきの fixture は記録しない
    (収集の結果を変えるので、その file は今までどおり import で集める)。
    """

    attribute: str
    name: str
    scope: FixtureScope
    argnames: tuple[str, ...]
    generator: bool


@dataclass(frozen=True)
class CacheEntry:
    """キャッシュの file 1 つの中身(JSON の境界で解いた形)。"""

    format: int
    records: tuple[str, ...]
    deps: tuple[MacroDependency, ...]
    fixtures: tuple[FixtureRecord, ...]
    sources: tuple[SourceDependency, ...]


@dataclass(frozen=True)
class CacheHit:
    """有効なキャッシュから引いた記録。"""

    records: tuple[Record, ...]
    fixtures: tuple[FixtureRecord, ...]


@dataclass(frozen=True)
class CacheMiss:
    """キャッシュに使える記録が無い — 理由は収集の終わりに報告する。"""

    reason: str


CacheLookup = CacheHit | CacheMiss


def _entry_path(source: Path, cache_dir: Path) -> Path:
    """source の内容の hash から、キャッシュの file の path を決める。"""
    return cache_dir / f"{hashlib.sha256(source.read_bytes()).hexdigest()}.json"


def _module_file(name: str) -> Path | None:
    """module 名から今の file を引く(import してあればその file、無ければ探す — 提供元の親の package は import 済み)。"""
    module = sys.modules.get(name)
    origin = module.__file__ if module is not None else None
    if origin is None:
        spec = importlib.util.find_spec(name)
        origin = spec.origin if spec is not None else None
    if origin is None or not origin.endswith((".py", ".hy")):
        return None
    return Path(origin)


def _parse_entry(text: str) -> CacheEntry:
    """キャッシュの file の JSON を型へ解く(JSON の境界はここだけ)。形が違えば MalformedCacheEntry。"""
    try:
        raw = json.loads(text)
        return CacheEntry(
            format=int(raw["format"]),
            records=tuple(str(t) for t in raw["records"]),
            deps=tuple(MacroDependency(str(d["module"]), str(d["digest"])) for d in raw["deps"]),
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
    """CacheEntry を JSON へ(書く側の境界)。``source`` は人が読むための控え。"""
    return json.dumps(
        {
            "format": entry.format,
            "source": str(source),
            "records": list(entry.records),
            "deps": [{"module": d.module, "digest": d.digest} for d in entry.deps],
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
    """source の記録をキャッシュから引く。無い・古い(提供元が変わった・形の版が違う)なら CacheMiss とその理由。"""
    entry_path = _entry_path(source, cache_dir)
    try:
        text = entry_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CacheMiss("記録なし(この内容の file は収集で import したことが無い)")
    try:
        entry = _parse_entry(text)
    except MalformedCacheEntry as exc:
        return CacheMiss(f"キャッシュの形が違う: {exc}")
    if entry.format != CACHE_FORMAT:
        return CacheMiss(f"キャッシュの形の版が違う({entry.format} ≠ {CACHE_FORMAT})")
    for dependency in entry.deps:
        path = _module_file(dependency.module)
        if path is None:
            return CacheMiss(f"macro の提供元 {dependency.module} が見つからない")
        if file_sha256(str(path)) != dependency.digest:
            return CacheMiss(f"macro の提供元 {dependency.module} が変わった")
    return _read_runtime_entry(entry_path, entry, source, root, checks)


def _read_runtime_entry(
    entry_path: Path, entry: CacheEntry, source: Path, root: Path, checks: DependencyChecks
) -> CacheLookup:
    """macroの版が合う記録の、実値の依存を照合しstatを更新する。"""
    refreshed: SourceSnapshot | str = checks.verify(root, entry.sources)
    if isinstance(refreshed, str):
        return CacheMiss(refreshed)
    if refreshed.sources != entry.sources:
        _write_entry(entry_path, replace(entry, sources=refreshed.sources), source)
    return CacheHit(tuple(decode_records(entry.records)), entry.fixtures)


def write_cached(
    source: Path, module: types.ModuleType, fixtures: tuple[FixtureRecord, ...], cache_dir: Path,
    records: tuple[Record, ...], root: Path, dynamic: bool,
) -> None:
    """照合済みの記録を保存する。動的な値には読込済みのlocal sourceも記録する。"""
    entry_path = _entry_path(source, cache_dir)
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    dependencies = tuple(
        MacroDependency(dependency.module, dependency.sha256)
        for dependency in macro_dependencies(module, module.__file__ or str(source))
    )
    sources: tuple[SourceDependency, ...] = loaded_sources(root).sources if dynamic else ()
    entry = CacheEntry(CACHE_FORMAT, tuple(encode_records(records)), dependencies, fixtures, sources)
    _write_entry(entry_path, entry, source)


def _write_entry(entry_path: Path, entry: CacheEntry, source: Path) -> None:
    """初回保存と、内容が同一だった依存fileのstat更新を原子的に書く。"""
    handle, temporary = tempfile.mkstemp(dir=entry_path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(_dump_entry(entry, source))
    os.replace(temporary, entry_path)


def forget_cached(source: Path, cache_dir: Path) -> None:
    """source の記録をキャッシュから消す(setup の照合で実物と食い違った時)。"""
    _entry_path(source, cache_dir).unlink(missing_ok=True)
