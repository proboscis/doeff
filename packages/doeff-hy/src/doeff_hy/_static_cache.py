"""doeff-hy-check の展開の cache(agora-redesign #2153)。

変えた file 3 つを検めるだけでも、それが import する根の下の .hy(agora の画面の core で 105 個)を全部展開するので
36 秒かかった。依存の展開は、展開が依った物が変わらなければ同じ結果になるので、展開した Python・位置の対応表・所見を
保存し、次の実行は変わった物だけを展開する。

展開が依った物(agora-redesign #3862 — テストの実行の側の bytecode の記録と同じ 1 つの作り方):
- 展開する source の中身・その module 名・根からの相対 path(診断の path と import の解決に効く)。
- 展開が使った macro の記録(``doeff_hy_bytecode_guard.macro_recording``)— Hy の版、展開が実際に使った macro(提供元の
  module 名・macro 名・macro の関数の code の閉包の digest — macro が呼ぶ補助・読む定数・本体の中の import を含む)、表を
  引いて無かった名と表に macro を入れた提供元、file 単位で覆う module(macro の本体の中の import の先など)の file の sha256。
  macro から辿れない型検査の展開の後処理は、doeff_hy._static_check の ``POST_PROCESSING``(``project``)の閉包の digest で入れる
  (``also``)。使っていない macro・注釈・行番号だけの変更では作り直さない。
以前は doeff_hy の package 全体の指紋と、source を Hy の reader で読んで集めた require の先の .hy だけをキーに入れていた。
展開に関係ない doeff_hy の commit 1 つで全部の展開が作り直しになり(1 file の測りが 8 秒から 72 秒)、一方で macro が呼ぶ
補助の .py を変えても古い展開が当たっていた。

使った macro は展開した後にしか分からないので、保存は 2 段にする(doeff-effect-analyzer の展開の保存 — agora-redesign
#3598 — と同じ形): source の中身・module 名・相対 path・この file の版で決まる場所(<cache dir>/<頭 2 字>/<場所>/)の中に、
記録ごとの entry(<記録の指紋>.json)を置き、記録が今の環境に合う entry だけを読む(``record_is_current_here`` — 使った
macro を module 名から今の環境で引き直して digest を比べる)。
doeff の版が違う作業木どうしは、互いの entry を上書きせずに並べて持つ。

保存の形は 1 entry 1 file の JSON。壊れた file は読めない物として飛ばし、展開し直す。

温まった実行の open を減らすため、検める file の組ごとに closure 全体を 1 file に束ねた写し(束 — 下の節)も持つ。束は写しで、正本は entry。
"""

import hashlib
import json
import os
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from doeff_hy_bytecode_guard import (
    record_fingerprint,
    record_from_json,
    record_is_current_here,
    record_to_json,
)

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard import MacroRecord

# 保存の形の版(形を変えたら上げる — 場所の名に入るので、古い版の entry は読まれない)。
# 2 = 展開が通った file の記録で照らす形(agora-redesign #3862)。
# 3 = 展開が使った macro 単位の記録で照らす形(記録の行 = 表の名・提供元の module 名・macro 名・digest)。
CACHE_VERSION = 3


@dataclass(frozen=True)
class Span:
    """展開した Python の範囲(0 始まりの行・列、終わりは含まない)と、対応する Hy の位置(1 始まり)— static_check.Span と同じ型。"""

    start: tuple[int, int]
    end: tuple[int, int]
    hy_line: int
    hy_column: int


#: 保存の形の呼び名(Span と同じ型 — repr の型名が運びの digest に入るので、型の名は Span のまま)。
CachedSpan = Span


class LazySpans(Sequence[Span]):
    """entry から読んだ位置の対応を、使われる時まで Span にしない列(1 file で千個を超え、使うのは検める file だけ — 引く側が
    依存の数百 file の分を作らずに済む)。tuple と同じ中身・同じ repr・同じ等しさで読める。"""

    __slots__ = ("_mut_items", "_raw")

    def __init__(self, raw: list[object]) -> None:
        self._raw = raw
        self._mut_items: tuple[Span, ...] | None = None

    def _items(self) -> tuple[Span, ...]:
        if self._mut_items is None:
            self._mut_items = tuple(
                Span((s[0], s[1]), (s[2], s[3]), s[4], s[5])  # type: ignore[index]
                for s in self._raw
            )
        return self._mut_items

    def __len__(self) -> int:
        return len(self._raw)

    def __getitem__(self, index: int | slice) -> "Span | tuple[Span, ...]":  # type: ignore[override]
        return self._items()[index]

    def __iter__(self) -> Iterator[Span]:
        return iter(self._items())

    def __eq__(self, other: object) -> bool:
        return self._items() == (other._items() if isinstance(other, LazySpans) else other)

    def __hash__(self) -> int:
        return hash(self._items())

    def __repr__(self) -> str:
        return repr(self._items())


@dataclass(frozen=True)
class CachedFinding:
    """保存した所見(static_check.Diagnostic と同じ欄)。"""

    path: str
    line: int
    column: int
    severity: str
    rule: str
    message: str


@dataclass(frozen=True)
class CachedProjection:
    """1 つの .hy の展開の結果のうち、保存して読み戻す部分と、展開が使った macro の記録。"""

    text: str
    spans: Sequence[Span]
    findings: tuple[CachedFinding, ...]
    used: "MacroRecord"
    # 展開した Python が import する module の名(辿り先を決める — 引くたびに展開の text を ast で読み直さないため。
    # 古い entry は持たない = None・引いた側が一度読んで書き足す)。
    imports: tuple[str, ...] | None = None


@dataclass(frozen=True)
class CacheMiss:
    """保存した展開を使えない理由(無い・今の環境に合う記録の entry が無い)。呼び手は展開し直して保存する。"""

    reason: str


def place(text: str, module: str, relative: str) -> str:
    """source の展開の場所の名(source の中身・module 名・相対 path・保存の形の版で決まる — 上の docstring)。"""
    digest = hashlib.sha256()
    for part in (str(CACHE_VERSION), module, relative, text):
        digest.update(part.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _place_dir(cache_dir: Path, name: str) -> Path:
    return cache_dir / name[:2] / name


def load(cache_dir: Path, name: str) -> CachedProjection | CacheMiss:
    """場所の中で、展開が使った macro の記録が今の環境に合う entry を読む。無ければ理由つきの CacheMiss(呼び手が展開し直す)。"""
    directory = _place_dir(cache_dir, name)
    if not directory.is_dir():
        return CacheMiss("無い")
    for entry in _newest_first(directory):
        match _read_entry(entry):
            case CachedProjection() as cached if record_is_current_here(cached.used):
                return cached
            case _:
                continue
    return CacheMiss("今の環境に合う記録の entry が無い")


def _newest_first(directory: Path) -> list[Path]:
    """場所の中の entry を新しい順に並べる — 今の環境に合う entry は、普通は一番新しく書かれた物なので、古い版の entry を読まずに
    済む(1 つしか無い場所は stat しない)。"""
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
    """entry 1 つを読む(読めない・形が違う時は CacheMiss)。保存の形の版は CACHE_VERSION と照らす(literal の版で照らすと、
    CACHE_VERSION を上げた版は自分の書いた entry を読めない — 設計検証 lt-TBEF3PAMAVQ8B3Z07MDSCA994C の盲検 A)。"""
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return CacheMiss(f"読めない: {error}")
    return _entry_from_json(loaded)


def _entry_from_json(loaded: object) -> CachedProjection | CacheMiss:
    """JSON から読んだ entry 1 つを組む(形が違う時は CacheMiss — entry の file も束の行も同じ形)。"""
    match loaded:
        case {
            "version": int(version),
            "used": used,
            "text": str(text),
            "spans": list(spans),
            "findings": list(findings),
        } if version == CACHE_VERSION:
            record = record_from_json(used)
            if record is None:
                return CacheMiss("記録の形が違う")
            try:
                return CachedProjection(
                    text,
                    LazySpans(spans),
                    tuple(CachedFinding(**f) for f in findings),
                    record,
                    _imports_of(loaded),
                )
            except (TypeError, IndexError) as error:
                return CacheMiss(f"欄の形が違う: {error}")
        case _:
            return CacheMiss("版か形が違う")


def _imports_of(loaded: object) -> tuple[str, ...] | None:
    """entry の import の名(無い古い entry・形が違う時は None)。"""
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
        "spans": [[*s.start, *s.end, s.hy_line, s.hy_column] for s in projection.spans],
        "findings": [vars(f) for f in projection.findings],
    }
    if projection.imports is not None:
        payload["imports"] = list(projection.imports)
    return payload


def store(cache_dir: Path, name: str, projection: CachedProjection) -> None:
    """展開を、場所の中の記録の entry に保存する。書けなくても検めは続ける(cache は速さのためだけ)。"""
    path = _place_dir(cache_dir, name) / f"{record_fingerprint(projection.used)}.json"
    payload = _entry_payload(projection)
    # 一時の file は書き手ごとの名(pid と時刻)— 保存先を共有する 2 台の worker が同じ entry を同時に書いても、互いの一時の file へ
    # 書き込まずに済み、どちらかの中身がそのまま rename される(agora-redesign #3863 の (b))。書けなければ一時の file を残さない。
    _write_atomically(path, payload)


def _write_atomically(path: Path, payload: object) -> None:
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)


# --- closure 単位の束(card ki-2829daea29fa) -------------------------------------------------------------------------
#
# 1 .hy ごとの entry は、引くたびに source の読みと entry の読みの 2 回 open する(367 file の closure で 700 回超)。束は、
# 検める file の組ごとに closure 全体の行(場所の名・展開・展開が使った macro の記録)を 1 file にして、温まった実行の open を
# 束 1 回にする。束は速さのためだけの写しで、正本は entry のまま: 束に無い・合わない file は今までの引き方(source の中身で
# 場所を決め、entry を引く)に落ち、その実行の終わりに束を書き直す。
#
# 古い展開を使わない性質(entry の引きと同じ 2 つの照らし):
# - source: 行は、束を書いた時の source の大きさと更新時刻を持つ。今の stat が同じなら中身が同じ物として信じ、違えば source の
#   中身から場所の名を作り直して行の名と比べる(更新時刻だけ新しくなった file は、名が同じなら束の展開をそのまま使う)。
#   大きさと更新時刻が同じまま中身が変わる書き換えを信じて逃さないため、束を書いた時に更新時刻が RACY_NS 以内に新しい file は
#   stat を持たない行にして、毎回中身で照らす(git の index の racy 判定と同じ)。
# - macro: 行ごとに ``record_is_current_here(used)`` で今の環境と照らす(entry と同じ)。
BUNDLE_VERSION = 1
#: 束を書く時刻からこの幅以内に更新された file は、更新時刻と大きさで信じない(粗い時計の同じ刻みの中の書き換え)。
RACY_NS = 2_000_000_000


@dataclass(frozen=True)
class FileStat:
    """束の行と比べる source の stat(大きさ・更新時刻の ns)。"""

    size: int
    mtime_ns: int


@dataclass(frozen=True)
class BundleRow:
    """束の行 1 つ — 相対 path の file の module 名・場所の名・書いた時の stat(信じない時は None)・展開。"""

    module: str
    place: str
    stat: FileStat | None
    cached: CachedProjection


def bundle_key(root: Path, targets: Sequence[str]) -> str:
    """束の名(根・検める file の相対 path の組・保存の形の版で決まる)。"""
    digest = hashlib.sha256()
    for part in (str(BUNDLE_VERSION), str(CACHE_VERSION), str(root), *sorted(set(targets))):
        digest.update(part.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _bundle_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / "bundles" / f"{key}.json"


def load_bundle(cache_dir: Path, key: str) -> dict[str, BundleRow]:
    """束を読む(無い・読めない・形が違う時は空 — 呼び手は今までの引き方で引く。壊れた行だけ飛ばす)。"""
    try:
        loaded: object = json.loads(_bundle_path(cache_dir, key).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    match loaded:
        case {"version": int(version), "rows": dict(rows)} if version == BUNDLE_VERSION:
            pass
        case _:
            return {}
    found: dict[str, BundleRow] = {}
    for relative, row in rows.items():
        match row:
            case {"module": str(module), "place": str(place), "stat": stat, "entry": entry}:
                cached = _entry_from_json(entry)
                if isinstance(cached, CacheMiss):
                    continue
                match stat:
                    case [int(size), int(mtime)]:
                        found[relative] = BundleRow(module, place, FileStat(size, mtime), cached)
                    case None:
                        found[relative] = BundleRow(module, place, None, cached)
    return found


def bundle_signature(rows: dict[str, BundleRow]) -> dict[str, tuple[str, str, str, FileStat | None]]:
    """束の行の、書き直すかを決める比べ物(module 名・場所の名・展開が使った macro の記録の指紋・信じる stat — 更新時刻が新しい行の
    stat は書かないので None)。記録の指紋を入れないと、macro を変えて展開し直した行が束に反映されず、毎回 entry へ落ちる。"""
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


def store_bundle(cache_dir: Path, key: str, rows: dict[str, BundleRow]) -> None:
    """束を書く(書けなくても検めは続ける)。"""
    signature = bundle_signature(rows)
    payload = {
        "version": BUNDLE_VERSION,
        "rows": {
            relative: {
                "module": signature[relative][0],
                "place": signature[relative][1],
                "stat": [stat.size, stat.mtime_ns] if (stat := signature[relative][3]) is not None else None,
                "entry": _entry_payload(row.cached),
            }
            for relative, row in rows.items()
        },
    }
    _write_atomically(_bundle_path(cache_dir, key), payload)


def stat_of(path: Path) -> FileStat | None:
    """束の行と比べる file の stat(大きさ・更新時刻の ns — 読めなければ None)。"""
    try:
        status = path.stat()
    except OSError:
        return None
    return FileStat(status.st_size, status.st_mtime_ns)


def row_is_current(row: BundleRow, module: str, stat: FileStat | None) -> bool:
    """行を、source を読まずに信じてよいか(module 名と stat が書いた時と同じで、展開が使った macro が今の環境に合う)。"""
    return (
        row.stat is not None
        and row.stat == stat
        and row.module == module
        and record_is_current_here(row.cached.used)
    )


def default_cache_dir() -> Path:
    """既定の置き場 — $DOEFF_HY_CHECK_CACHE が名指す dir(走りをまたいで残る dir へ向ける呼び手のため — agora-redesign
    #3863)、無ければ XDG の cache の下(消費 repo の木を汚さない)。"""
    from doeff_hy import env_places

    configured = env_places.check_cache_setting()
    return Path(configured) if configured else env_places.cache_home() / "doeff-hy-check"
