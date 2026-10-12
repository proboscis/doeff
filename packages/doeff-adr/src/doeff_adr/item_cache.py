"""Hy の test file の item の記録のキャッシュ — 鍵は source の内容の hash と、展開が使った macro の記録(agora-redesign #1291)。

item を作る macro は、展開の式で記録を module に積む(``doeff_hy.pytest_items``)。この module は、収集で 1 度 import した
file の記録を保存し、次の収集からは import せずに引けるようにする。Hy の importer には手を入れない(#1290 の利用者の決定)。

- 置き場: ``<dir>/<source の sha256>.json``。dir は :func:`items_cache_dir_for` の 1 か所で決める(ini → 環境変数
  ``DOEFF_ADR_ITEMS_CACHE`` → ``~/.cache/doeff-adr/pytest-items`` の順 — plugin が ini を読んで渡す)。内容の
  hash が鍵なので、同じ内容の file は worktree をまたいで同じ記録を引く。
- 有効判定: source の sha256 で引き(鍵)、保存した時の展開が使った macro の記録が今の環境に合い(.pyc と同じ照らし方 —
  展開で実行された関数の中身だけを覆う)、記録を積む実行時の口(:data:`RECORDING_RUNTIME` の module の file の sha256)が今も
  同じで、記録の形の版が同じ時だけ有効。item の記録は展開の結果の文字列の定数で、import の時に実行時の口が module に積む
  だけなので、展開と実行時の口が同じなら同じ記録になる。記録は module 名で macro を引き直すので、絶対 path に縛られない。
- 展開の記録の取り方と照らし方は、doeff-hy の ``doeff_hy_bytecode_guard`` の公開の口(``loaded_record`` と
  ``record_is_current_here`` — 古い bytecode を見つける仕組み #1292 と同じ 1 つの照らし方)を使う。ここに 2 つ目の照らし方を
  書かない。前は macro の提供元の file の sha256 で照らし、doeff_hy の macros.hy のどの行の変更でも Hy の test file を全部
  import で収集し直して、doeff の日次が時間の上限を越えた(10-12 04:00・08:00 の回 — #3769 の子)。
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
from doeff_hy_bytecode_guard import (
    file_sha256,
    loaded_record,
    record_from_json,
    record_is_current_here,
    record_to_json,
)

from doeff_adr.env_places import ITEMS_CACHE_ENV, environment_value
from doeff_adr.source_dependencies import (
    DependencyChecks,
    ProviderFound,
    ProviderMissing,
    ProviderState,
    SourceDependency,
    SourceSnapshot,
    loaded_origins,
)

TYPE_CHECKING = False  # 型検査器はこの名の分岐を真として読む(記録の型は注記だけに使う公開の名)
if TYPE_CHECKING:
    from doeff_hy_bytecode_guard import MacroRecord

# 記録の形か鍵の決め方を変えたら上げる(古い版のキャッシュは読まない)。2 = macro の提供元を doeff_hy_bytecode_guard の辿り方で求める。
# 3 = module の fixture の記録を足す(agora-redesign #1227 の案 B)。
# 4 = import後の実値・明示idとproject内sourceの状態を保存する(#1459)。
# fixture の別名も pytest の登録名で保存する (#1551)。古い記録の名前を使わない。
# 6 = macro の提供元の file の sha256 の代わりに、展開が使った macro の記録と記録を積む実行時の口の file を保存する(#3769 の子)。
CACHE_FORMAT = 6

#: 記録を積む実行時の口の module — 展開の式が import の時に呼ぶ ``record-at-import`` の在処。展開の記録はこの code を覆わない
#: (展開の間には実行されない)ので、file の sha256 で別に覆う。
RECORDING_RUNTIME = "doeff_hy.pytest_items"

#: 読めなかった file の sha256 の代わり(16 進の sha256 と決して一致しない — 記録はいつも古いと判じられる)。
_UNREADABLE = "unreadable"


def items_cache_dir_for(configured: str, rootpath: Path) -> Path:
    """記録の置き場の dir を決める唯一の所。``configured`` は pytest の ini ``doeff_adr_items_cache`` の値。

    順: ini の値が在ればそれ(project の設定が最も狭い)→ 無ければ環境変数 ITEMS_CACHE_ENV(走らせる側が process ごとに渡す)→
    どちらも無ければ利用者の cache の dir の下(``~/.cache/doeff-adr/pytest-items``)。環境変数の段は、日次の全体検証の task が
    HOME を回ごとに空の dir に替えるため(既定の置き場では記録が毎回空になり、atlas の日次は収集のために 309 file を import
    していた — card acp:kanban-issue:ki-9338eec1d15e の責務 R2)。ini と環境変数の相対の path は rootdir から解く。
    """
    named = configured.strip() or (environment_value(ITEMS_CACHE_ENV) or "").strip()
    if not named:
        return Path.home() / ".cache" / "doeff-adr" / "pytest-items"
    path = Path(named).expanduser()
    return path if path.is_absolute() else rootpath / path


@dataclass(frozen=True)
class RuntimeDependency:
    """保存した、記録を積む実行時の口の module 1 つ(module 名と、保存した時の file の sha256)。file の path は worktree で
    変わるので鍵にしない — 読む時に module 名から今の file を引く。"""

    module: str
    digest: str


@dataclass(frozen=True)
class ExpansionUnknown:
    """import した test file の展開が使った macro の記録が無い(理由の文)— その file の記録は保存しない。"""

    reason: str


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
    expansion: "MacroRecord"
    runtime: tuple[RuntimeDependency, ...]
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


def _provider_state(name: str) -> ProviderState:
    source = _module_file(name)
    return ProviderMissing() if source is None else ProviderFound(file_sha256(str(source)))


def _parse_entry(text: str) -> CacheEntry:
    """キャッシュの file の JSON を型へ解く(JSON の境界はここだけ)。形が違えば MalformedCacheEntry。"""
    try:
        raw = json.loads(text)
        format_ = int(raw["format"])
        if format_ != CACHE_FORMAT:
            raise MalformedCacheEntry(f"キャッシュの形の版が違う({format_} ≠ {CACHE_FORMAT})")
        expansion = record_from_json(raw["expansion"])
        if expansion is None:
            raise MalformedCacheEntry("展開の記録の形が違う")
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
    """CacheEntry を JSON へ(書く側の境界)。``source`` は人が読むための控え。"""
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
    """source の記録をキャッシュから引く。無い・古い(記録を積む実行時の口・展開が使った macro が変わった・形の版が違う)なら
    CacheMiss とその理由。"""
    entry_path = _entry_path(source, cache_dir)
    try:
        text = entry_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return CacheMiss("記録なし(この内容の file は収集で import したことが無い)")
    try:
        entry = _parse_entry(text)
    except MalformedCacheEntry as exc:
        return CacheMiss(f"キャッシュの形が違う: {exc}")
    for dependency in entry.runtime:
        match checks.macro_provider(dependency.module, _provider_state):
            case ProviderMissing():
                return CacheMiss(f"記録を積む実行時の口 {dependency.module} が見つからない")
            case ProviderFound(digest):
                if digest != dependency.digest:
                    return CacheMiss(f"記録を積む実行時の口 {dependency.module} が変わった")
    if not record_is_current_here(entry.expansion):
        return CacheMiss("展開が使った macro が変わった")
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


@dataclass(frozen=True)
class PendingEntry:
    """import した直後に照合した記録 1 つ — 書くのは収集の終わり。書き込みと、読込済みの source の読みと hash は、doeff-hy-pytest の
    読み込みの上限が測る import の区間の外で行う(収集の道具の費用で、検の file の重さではない — card ki-b2383fcc8b25)。
    expansion = import した時の展開が使った macro の記録(:func:`expansion_of`)。
    origins = 実値の記録が依存する、import した時点で読込済みだった module の file(静的な記録は空)。"""

    source: Path
    module: types.ModuleType
    fixtures: tuple[FixtureRecord, ...]
    records: tuple[Record, ...]
    expansion: "MacroRecord"
    origins: frozenset[str]


def expansion_of(module: types.ModuleType) -> "MacroRecord | ExpansionUnknown":
    """import した test file の展開が使った macro の記録(読みの口が返した code に載った物 — 無ければ理由)。import の直後に
    引く(後で同じ file を読み直すと、覚えがその code の記録に替わる)。"""
    file = vars(module).get("__file__")
    record = loaded_record(file) if isinstance(file, str) else None
    if record is None:
        return ExpansionUnknown("展開の記録が無い(読みの口を通らなかったか、記録の無い code)")
    return record


def pending_entry(
    source: Path,
    module: types.ModuleType,
    fixtures: tuple[FixtureRecord, ...],
    records: tuple[Record, ...],
    expansion: "MacroRecord",
    dynamic: bool,
) -> PendingEntry:
    """照合済みの記録を、書くまで持つ形にする。実値の記録なら、この時点で読込済みの module の file の名だけを集める(読まない)。"""
    return PendingEntry(
        source, module, fixtures, records, expansion, loaded_origins() if dynamic else frozenset()
    )


def write_cached(pending: PendingEntry, cache_dir: Path, root: Path, checks: DependencyChecks) -> None:
    """照合済みの記録を保存する。実値の記録には、import した時点で読込済みだった local source も記録する(収集 1 回の観測 checks を
    通して、同じ source は 1 度だけ読む)。"""
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
    """記録を積む実行時の口の module の今の file の sha256(見つからない・読めない file は、どの sha256 とも合わない印で
    書く — 書かないと、その file の変更を見落とす)。"""
    match checks.macro_provider(module, _provider_state):
        case ProviderFound(digest) if digest is not None:
            return RuntimeDependency(module, digest)
        case _:
            return RuntimeDependency(module, _UNREADABLE)


def _write_entry(entry_path: Path, entry: CacheEntry, source: Path) -> None:
    """初回保存と、内容が同一だった依存fileのstat更新を原子的に書く。"""
    handle, temporary = tempfile.mkstemp(dir=entry_path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(handle, "w", encoding="utf-8") as out:
        out.write(_dump_entry(entry, source))
    os.replace(temporary, entry_path)


def forget_cached(source: Path, cache_dir: Path) -> None:
    """source の記録をキャッシュから消す(setup の照合で実物と食い違った時)。"""
    _entry_path(source, cache_dir).unlink(missing_ok=True)
