"""Hy の test file の item の記録のキャッシュ — 鍵は source の内容の hash と、macro の提供元の内容の hash(agora-redesign #1291)。

item を作る macro は、展開の式で記録を module に積む(``doeff_hy.pytest_items``)。この module は、収集で 1 度 import した
file の記録を保存し、次の収集からは import せずに引けるようにする。Hy の importer には手を入れない(#1290 の利用者の決定)。

- 置き場: ``~/.cache/doeff-adr/pytest-items/<source の sha256>.json``(pytest の ini ``doeff_adr_items_cache`` で変えられる —
  plugin が読んで引数で渡す)。内容の
  hash が鍵なので、同じ内容の file は worktree をまたいで同じ記録を引く。
- 有効判定: source の sha256 で引き(鍵)、保存した時の macro の提供元(module 名と、その file の sha256)が今も同じで、
  記録の形の版が同じ時だけ有効。提供元は module 名で覚えて読む時に今の file を引くので、絶対 path に縛られない。
- macro の提供元: import した module の ``_hy_macros`` / ``_hy_reader_macros`` の macro の定義元の module、提供元が
  参照する同じ top package の module(macro の補助の関数)、提供元が使う macro の提供元をたどった集合(Hy の fork が
  pyc に付けた macro の依存の記録と同じ範囲)。
"""

import hashlib
import importlib.util
import inspect
import json
import os
import sys
import tempfile
import types
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from doeff_hy.pytest_items import Record, decode_records, module_record_texts

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "doeff-adr" / "pytest-items"
# 記録の形か鍵の決め方を変えたら上げる(古い版のキャッシュは読まない)。
CACHE_FORMAT = 1


@dataclass(frozen=True)
class MacroDependency:
    """macro の提供元 1 つ(module 名と、保存した時の file の sha256)。"""

    module: str
    digest: str


@dataclass(frozen=True)
class CacheEntry:
    """キャッシュの file 1 つの中身(JSON の境界で解いた形)。"""

    format: int
    records: tuple[str, ...]
    deps: tuple[MacroDependency, ...]


@dataclass(frozen=True)
class CacheHit:
    """有効なキャッシュから引いた記録。"""

    records: tuple[Record, ...]


@dataclass(frozen=True)
class CacheMiss:
    """キャッシュに使える記録が無い — 理由は収集の終わりに報告する。"""

    reason: str


CacheLookup = CacheHit | CacheMiss


class MalformedCacheEntry(ValueError):
    """キャッシュの file の形が違う(版の違う doeff-adr が書いた・書きかけ等)。"""


_FILE_DIGESTS: dict[Path, str] = {}


def _file_digest(path: Path) -> str:
    """file の内容の sha256(同じ process の中では 1 file 1 回 — 提供元は多くの test file で同じ)。"""
    cached = _FILE_DIGESTS.get(path)
    if cached is None:
        cached = hashlib.sha256(path.read_bytes()).hexdigest()
        _FILE_DIGESTS[path] = cached
    return cached


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
        )
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
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def read_cached(source: Path, cache_dir: Path) -> CacheLookup:
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
        if _file_digest(path) != dependency.digest:
            return CacheMiss(f"macro の提供元 {dependency.module} が変わった")
    return CacheHit(tuple(decode_records(entry.records)))


def write_cached(source: Path, module: types.ModuleType, cache_dir: Path) -> None:
    """import した module の記録と macro の提供元を、source の内容の hash を鍵に保存する(書き換えは rename で 1 度に)。"""
    entry_path = _entry_path(source, cache_dir)
    entry_path.parent.mkdir(parents=True, exist_ok=True)
    entry = CacheEntry(CACHE_FORMAT, tuple(module_record_texts(module)), tuple(macro_dependencies(module)))
    handle, temporary = tempfile.mkstemp(dir=entry_path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(_dump_entry(entry, source))
    os.replace(temporary, entry_path)


def forget_cached(source: Path, cache_dir: Path) -> None:
    """source の記録をキャッシュから消す(setup の照合で実物と食い違った時)。"""
    _entry_path(source, cache_dir).unlink(missing_ok=True)


def _macro_providers(namespace: dict[str, object]) -> Iterator[types.ModuleType]:
    """``namespace`` の macro の表から、macro を定義した module を並べる。"""
    for table in ("_hy_macros", "_hy_reader_macros"):
        macros = namespace.get(table)
        if not isinstance(macros, dict):
            continue
        for macro in list(macros.values()):
            provider = sys.modules.get(getattr(macro, "__module__", "") or "")
            if provider is not None:
                yield provider


def _referenced_module_names(namespace: dict[str, object]) -> Iterator[str]:
    """``namespace`` の値が属する module の名を並べる(macro の補助の関数を探すため)。"""
    for value in list(namespace.values()):
        name = value.__name__ if inspect.ismodule(value) else getattr(value, "__module__", None)
        if isinstance(name, str):
            yield name


def _top_package(name: str) -> str:
    return name.partition(".")[0]


def macro_dependencies(module: types.ModuleType) -> list[MacroDependency]:
    """``module`` の展開が頼る macro の提供元の module(と、その file の sha256)を、名の順に返す。

    Hy 自身(top package ``hy``)は含めない — Hy の版が変われば doeff の固定の変更で分かる。
    """
    seen: set[str] = set()
    found: dict[str, Path] = {}
    pending = list(_macro_providers(vars(module)))
    while pending:
        provider = pending.pop()
        name = provider.__name__
        if name in seen:
            continue
        seen.add(name)
        if provider is module or _top_package(name) == "hy":
            continue
        path = _module_file(name)
        if path is not None:
            found[name] = path
        pending.extend(_macro_providers(vars(provider)))
        for other in _referenced_module_names(vars(provider)):
            referenced = sys.modules.get(other)
            if other not in seen and referenced is not None and _top_package(other) == _top_package(name):
                pending.append(referenced)
    return [MacroDependency(name, _file_digest(path)) for name, path in sorted(found.items())]
