"""doeff-hy-check の展開の cache(agora-redesign #2153)。

変えた file 3 つを検めるだけでも、それが import する根の下の .hy(agora の画面の core で 105 個)を全部展開するので
36 秒かかった。依存の展開は、展開が依った物が変わらなければ同じ結果になるので、展開した Python・位置の対応表・所見を
保存し、次の実行は変わった物だけを展開する。

展開が依った物(agora-redesign #3862 — テストの実行の側の bytecode の記録と同じ 1 つの作り方):
- 展開する source の中身・その module 名・根からの相対 path(診断の path と import の解決に効く)。
- 展開が通った file の記録(``doeff_hy_bytecode_guard.current_record``)— Hy の版、require した macro の module、macro が
  呼ぶ同じ package の補助の module(名前空間の値と、macro の本体の中の import)、推移的な require、根の外の別の package の
  macro。macro から辿れない型検査の展開の後処理(doeff_hy.static_check とそれが参照する doeff_hy の module)も同じ辿り方で
  入れる(``also``)。
以前は doeff_hy の package 全体の指紋と、source を Hy の reader で読んで集めた require の先の .hy だけをキーに入れていた。
展開に関係ない doeff_hy の commit 1 つで全部の展開が作り直しになり(1 file の測りが 8 秒から 72 秒)、一方で macro が呼ぶ
補助の .py を変えても古い展開が当たっていた。

展開が通った file は展開した後にしか分からないので、保存は 2 段にする(doeff-effect-analyzer の展開の保存 — agora-redesign
#3598 — と同じ形): source の中身・module 名・相対 path・この file の版で決まる場所(<cache dir>/<頭 2 字>/<場所>/)の中に、
展開が通った file の記録ごとの entry(<記録の指紋>.json)を置き、記録が今の環境に合う entry だけを読む
(``record_is_current_here`` — 記録の path は作った作業木の物なので、module 名から今の環境の file を引き直して照らす)。
doeff の版が違う作業木どうしは、互いの entry を上書きせずに並べて持つ。

保存の形は 1 entry 1 file の JSON。壊れた file は読めない物として飛ばし、展開し直す。
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from doeff_hy_bytecode_guard import record_from_rows, record_is_current_here

if TYPE_CHECKING:
    from doeff_hy_bytecode_guard import MacroRecord

# 保存の形の版(形を変えたら上げる — 場所の名に入るので、古い版の entry は読まれない)。
# 2 = 展開が通った file の記録で照らす形(agora-redesign #3862)。
CACHE_VERSION = 2


@dataclass(frozen=True)
class CachedSpan:
    """保存した位置の対応(static_check.Span と同じ欄)。"""

    start: tuple[int, int]
    end: tuple[int, int]
    hy_line: int
    hy_column: int


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
class CachedDependency:
    """保存した記録の 1 行(展開が通った file 1 つ — doeff_hy_bytecode_guard の MacroDependency と同じ欄)。"""

    module: str
    file: str
    sha256: str


@dataclass(frozen=True)
class CachedProjection:
    """1 つの .hy の展開の結果のうち、保存して読み戻す部分と、展開が通った file の記録。"""

    text: str
    spans: tuple[CachedSpan, ...]
    findings: tuple[CachedFinding, ...]
    used: "MacroRecord"


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


def _record_name(record: "MacroRecord") -> str:
    """場所の中の entry の名(展開が通った file の module 名と中身の指紋 — file の path は書いた作業木の物なので入れない。
    同じ中身の別の作業木は同じ entry に当たる。doeff-effect-analyzer の entry の名と同じ)。"""
    digest = hashlib.sha256()
    for part in (record.hy_version, *(f"{d.module}\0{d.sha256}" for d in record.dependencies)):
        digest.update(part.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _place_dir(cache_dir: Path, name: str) -> Path:
    return cache_dir / name[:2] / name


def load(cache_dir: Path, name: str) -> CachedProjection | CacheMiss:
    """場所の中で、展開が通った file の記録が今の環境に合う entry を読む。無ければ理由つきの CacheMiss(呼び手が展開し直す)。"""
    directory = _place_dir(cache_dir, name)
    if not directory.is_dir():
        return CacheMiss("無い")
    for entry in sorted(directory.glob("*.json")):
        match _read_entry(entry):
            case CachedProjection() as cached if record_is_current_here(cached.used):
                return cached
            case _:
                continue
    return CacheMiss("今の環境に合う記録の entry が無い")


def _read_entry(path: Path) -> CachedProjection | CacheMiss:
    """entry 1 つを読む(読めない・形が違う時は CacheMiss)。"""
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return CacheMiss(f"読めない: {error}")
    match loaded:
        case {
            "version": 2,
            "hy": str(hy_version),
            "dependencies": list(dependencies),
            "text": str(text),
            "spans": list(spans),
            "findings": list(findings),
        }:
            rows = tuple(_row(d) for d in dependencies)
            read = tuple(row for row in rows if not isinstance(row, CacheMiss))
            if len(read) != len(rows):
                return CacheMiss("記録の行の形が違う")
            try:
                return CachedProjection(
                    text,
                    tuple(CachedSpan((s[0], s[1]), (s[2], s[3]), s[4], s[5]) for s in spans),
                    tuple(CachedFinding(**f) for f in findings),
                    record_from_rows(hy_version, tuple((d.module, d.file, d.sha256) for d in read)),
                )
            except (TypeError, IndexError) as error:
                return CacheMiss(f"欄の形が違う: {error}")
        case _:
            return CacheMiss("版か形が違う")


def _row(value: object) -> "CachedDependency | CacheMiss":
    """記録の 1 行(module 名・file・sha256 の 3 つの文字列)。形が違えば CacheMiss。"""
    match value:
        case [str(module), str(file), str(sha256)]:
            return CachedDependency(module, file, sha256)
        case _:
            return CacheMiss(f"記録の行の形が違う: {value!r}")


def store(cache_dir: Path, name: str, projection: CachedProjection) -> None:
    """展開を、場所の中の記録の entry に保存する。書けなくても検めは続ける(cache は速さのためだけ)。"""
    path = _place_dir(cache_dir, name) / f"{_record_name(projection.used)}.json"
    payload = {
        "version": CACHE_VERSION,
        "hy": projection.used.hy_version,
        "dependencies": [[d.module, d.file, d.sha256] for d in projection.used.dependencies],
        "text": projection.text,
        "spans": [[*s.start, *s.end, s.hy_line, s.hy_column] for s in projection.spans],
        "findings": [vars(f) for f in projection.findings],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        return


def default_cache_dir() -> Path:
    """既定の置き場(XDG の cache の下 — 消費 repo の木を汚さない)。"""
    from doeff_hy import env_places

    return env_places.cache_home() / "doeff-hy-check"
