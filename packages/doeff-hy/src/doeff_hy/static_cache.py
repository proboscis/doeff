"""doeff-hy-check の展開の cache(agora-redesign #2153)。

変えた file 3 つを検めるだけでも、それが import する根の下の .hy(agora の画面の core で 105 個)を全部展開するので
36 秒かかった。依存の展開は source が変わらなければ同じ結果になるので、展開した Python・位置の対応表・所見を
鍵ごとに保存し、次の実行は変えた file だけを展開する。

鍵(sha256)= 次のどれかが変われば別の鍵になる:
- 展開する source の中身・その module 名・根からの相対 path(診断の path と import の解決に効く)。
- doeff-hy の macro と型検査の展開の source(doeff_hy の .hy / .py 全部 — macro が変われば展開が変わる)。
- source が `require` する根の下の macro の module の中身(推移的に)。
- この file の版(CACHE_VERSION — 保存の形を変えたら上げる)。

保存の形は 1 鍵 1 file の JSON(<cache dir>/<鍵の頭 2 字>/<鍵>.json)。壊れた file は読めない物として捨てて展開し直す。
"""

import hashlib
import json
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import hy
from hy.errors import HyLanguageError
from hy.models import Expression, Symbol

import doeff_hy

CACHE_VERSION = 1


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
class CachedProjection:
    """1 つの .hy の展開の結果のうち、保存して読み戻す部分。"""

    text: str
    spans: tuple[CachedSpan, ...]
    findings: tuple[CachedFinding, ...]


@dataclass(frozen=True)
class CacheMiss:
    """保存した展開を使えない理由(無い・壊れている・形が違う)。呼び手は展開し直して保存する。"""

    reason: str


@cache
def _doeff_hy_digest() -> str:
    """doeff-hy の macro と展開の source の指紋(macro が変われば全部の展開を作り直すため)。"""
    package = Path(doeff_hy.__file__).parent
    digest = hashlib.sha256()
    for path in sorted(p for p in package.rglob("*") if p.suffix in {".hy", ".py", ".pyi"}):
        digest.update(str(path.relative_to(package)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _required_modules(text: str) -> tuple[str, ...]:
    """source の一番外の `(require M ...)` の M の名(macro の展開が依る module を鍵に入れるため)。"""
    try:
        forms = hy.read_many(text)
        return tuple(
            str(form[1])
            for form in forms
            if isinstance(form, Expression)
            and len(form) >= 2
            and form[0] == Symbol("require")
            and isinstance(form[1], Symbol)
        )
    except HyLanguageError:  # 読めない source は展開も失敗する(CompileFailure)— 鍵は source の中身だけで決まる
        return ()


def _macro_sources(roots: tuple[Path, ...], text: str, seen: frozenset[Path] = frozenset()) -> tuple[Path, ...]:
    """source が require する根の下の .hy を推移的に集める(根の外 = doeff-hy などは _doeff_hy_digest が持つ)。"""
    found = tuple(
        candidate
        for name in _required_modules(text)
        for root in roots
        for candidate in (
            root.joinpath(*hy.mangle(name).split(".")).with_suffix(".hy"),
            root.joinpath(*hy.mangle(name).split(".")) / "__init__.hy",
        )
        if candidate.is_file() and candidate not in seen
    )
    known = seen | frozenset(found)
    return found + tuple(
        deeper
        for path in found
        for deeper in _macro_sources(roots, path.read_text(encoding="utf-8"), known)
    )


def cache_key(roots: tuple[Path, ...], source: Path, module: str, relative: str) -> str:
    """展開を引く鍵(上の docstring の 4 つが同じなら同じ展開になる)。"""
    text = source.read_text(encoding="utf-8")
    digest = hashlib.sha256()
    for part in (str(CACHE_VERSION), _doeff_hy_digest(), module, relative, text):
        digest.update(part.encode())
        digest.update(b"\0")
    for macro in sorted(set(_macro_sources(roots, text))):
        digest.update(macro.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _entry(cache_dir: Path, key: str) -> Path:
    return cache_dir / key[:2] / f"{key}.json"


def load(cache_dir: Path, key: str) -> CachedProjection | CacheMiss:
    """保存した展開を読む。使えない時は理由つきの CacheMiss(呼び手が展開し直す)。"""
    path = _entry(cache_dir, key)
    if not path.is_file():
        return CacheMiss("無い")
    try:
        loaded: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return CacheMiss(f"読めない: {error}")
    match loaded:
        case {"version": 1, "text": str(text), "spans": list(spans), "findings": list(findings)}:
            try:
                return CachedProjection(
                    text,
                    tuple(
                        CachedSpan((s[0], s[1]), (s[2], s[3]), s[4], s[5]) for s in spans
                    ),
                    tuple(CachedFinding(**f) for f in findings),
                )
            except (TypeError, IndexError) as error:
                return CacheMiss(f"欄の形が違う: {error}")
        case _:
            return CacheMiss("版か形が違う")


def store(cache_dir: Path, key: str, projection: CachedProjection) -> None:
    """展開を保存する。書けなくても検めは続ける(cache は速さのためだけ)。"""
    path = _entry(cache_dir, key)
    payload = {
        "version": CACHE_VERSION,
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
    import os

    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "doeff-hy-check"
