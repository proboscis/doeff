"""doeff-hy-check の展開の cache(agora-redesign #2153)。

変えた file 3 つを検めるだけでも、それが import する根の下の .hy(agora の画面の core で 105 個)を全部展開するので
36 秒かかった。依存の展開は source が変わらなければ同じ結果になるので、展開した Python・位置の対応表・所見を
鍵ごとに保存し、次の実行は変えた file だけを展開する。

鍵(sha256)= 次のどれかが変われば別の鍵になる:
- 展開する source の中身・その module 名・根からの相対 path(診断の path と import の解決に効く)。
- doeff-hy の macro と型検査の展開の source(doeff_hy の .hy / .py 全部 — macro が変われば展開が変わる)。
- source が `require` する根の下の macro の module の中身(推移的に)。名は Hy の compiler が require に渡すのと同じ
  名で拾う — 点つきの `a.b.c`(reader は `(. a b c)` の式に読む)・1 つの require に並べた 2 つ目からの module・
  相対の `.x`(source の package から解く)も。点つきの名を拾わず、macro を変えても古い展開が当たっていた
  (agora-redesign #2696)。
- この file の版(CACHE_VERSION — 保存の形を変えたら上げる)。

鍵を作るには source が `require` する module の名が要り、それを知るには source を Hy の reader で読む。読みは展開の
次に重く、cache が温かくても検める file の依存の全部(agora の controllers/agora_sim/screen.hy で 350 個)を毎回読み直して
いた(1 file の測りの約 29 秒のほとんど — agora-redesign #2675)。だから読みの結果(require する名の列)も source の中身の
指紋ごとに保存して引く(<cache dir>/requires/<頭 2 字>/<指紋>.txt — 1 行 1 名)。指紋 = 読みの版(REQUIRES_VERSION)・
Hy の版・source の中身(reader の答えは後の 2 つだけで決まる)。読みの答えの意味を変えたら REQUIRES_VERSION を上げ、
古い版の保存(点つきの名を欠いた答え)を読まない。展開の保存と別の拡張子にして、展開の数え(*.json)に混ぜない。

保存の形は 1 鍵 1 file の JSON(<cache dir>/<鍵の頭 2 字>/<鍵>.json)。壊れた file は読めない物として捨てて展開し直す。
"""

import hashlib
import importlib.util
import json
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import hy
from hy.errors import HyLanguageError
from hy.models import Expression, Keyword, Symbol

import doeff_hy

CACHE_VERSION = 1
# require の読みの保存の版(上の docstring)。1 = 一番外の require の 1 つ目の点なしの名だけ。
# 2 = Hy の compiler が require に渡す名を全部(点つき・2 つ目からの module・相対)— agora-redesign #2696。
REQUIRES_VERSION = 2


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


def _requires_entry(cache_dir: Path, text: str) -> Path:
    """require する名の列の保存先(読みの版・Hy の版・source の中身の指紋 — 上の docstring)。"""
    digest = hashlib.sha256()
    for part in ("requires", str(REQUIRES_VERSION), hy.__version__, text):
        digest.update(part.encode())
        digest.update(b"\0")
    key = digest.hexdigest()
    return cache_dir / "requires" / key[:2] / f"{key}.txt"


def _required_modules(text: str, cache_dir: Path | None = None) -> tuple[str, ...]:
    """source の一番外の `(require …)` が require する module の名(macro の展開が依る module を鍵に入れるため)。cache_dir があれば
    読みの結果を source の中身の指紋ごとに引き、無ければ読んで保存する(上の docstring — 毎回の reader の読みを省くため)。"""
    if cache_dir is None:
        return _read_required_modules(text)
    entry = _requires_entry(cache_dir, text)
    try:
        return tuple(name for name in entry.read_text(encoding="utf-8").split("\n") if name)
    except OSError:
        pass
    names = _read_required_modules(text)
    try:
        entry.parent.mkdir(parents=True, exist_ok=True)
        temporary = entry.with_suffix(".tmp")
        temporary.write_text("\n".join(names), encoding="utf-8")
        temporary.replace(entry)
    except OSError:
        pass
    return names


def _read_required_modules(text: str) -> tuple[str, ...]:
    """source を Hy の reader で読み、一番外の `(require …)` が require する module の名を並べる(保存しない読みの 1 か所)。"""
    try:
        forms = hy.read_many(text)
        return tuple(
            name
            for form in forms
            if isinstance(form, Expression) and len(form) >= 2 and form[0] == Symbol("require")
            for name in _require_entries(form[1:])
        )
    except HyLanguageError:  # 読めない source は展開も失敗する(CompileFailure)— 鍵は source の中身だけで決まる
        return ()


def _require_entries(arguments: Sequence[object]) -> tuple[str, ...]:
    """`(require …)` の引数のうち module の名の物を並べる。Hy の文法では module の名の後に `[名 …]`・`*`・
    `:as 別名`・`:macros …`・`:readers …` が 0〜2 つ続き、その後に次の module の名が来てよい。名でない物 =
    括弧・`*`・keyword・`:as` の次の別名。"""
    return tuple(
        name
        for previous, argument in zip((None, *arguments), arguments)
        if not (isinstance(previous, Keyword) and previous == Keyword("as"))
        and (name := _module_name(argument)) is not None
    )


def _module_name(argument: object) -> str | None:
    """require の 1 つの引数が module の名なら、Hy の compiler が require に渡すのと同じ名(hy.core.result_macros の
    module_name_str と compile_require の規則 — 部分ごとに mangle・相対は先頭の点を残す)。名でなければ None。
    reader は `a.b.c` を `(. a b c)`、相対の `.x` を `(. None x)`・`..x.y` を `(.. None x y)` の式に読む。"""
    match argument:
        case Symbol() if argument == Symbol("*"):
            return None
        case Symbol() if not argument.strip("."):
            return str(argument)  # `.` だけ = その source の package(相対)
        case Symbol():
            return hy.mangle(argument)
        case Expression():
            return _dotted_module_name(tuple(argument))
        case _:
            return None


def _dotted_module_name(parts: tuple[object, ...]) -> str | None:
    """`(. a b c)`・`(. None x)`・`(.. None x y)` の式の module の名(頭が点だけの記号で、残りが全部記号の時だけ)。"""
    symbols = tuple(part for part in parts if isinstance(part, Symbol))
    if len(symbols) != len(parts) or len(symbols) < 2 or symbols[0].strip("."):
        return None
    head, *rest = symbols
    relative = rest[0] == Symbol("None")
    dotted = ".".join(hy.mangle(part) for part in (rest[1:] if relative else rest))
    return f"{head}{dotted}" if relative else dotted


@dataclass(frozen=True)
class _MacroSource:
    """require で読む根の下の macro の module の file と、その file の相対の require(`.x`)を解く package。"""

    path: Path
    package: str


def _package_of(module: str, path: Path) -> str:
    """module の相対の require を解く package(__init__.hy なら module 自身・他は親の package)。"""
    return module if path.name == "__init__.hy" else module.rpartition(".")[0]


def _absolute_module(name: str, package: str) -> str | None:
    """require の名を絶対の module 名にする。相対の名は package から解き、解けなければ None(Hy の require も失敗する)。"""
    if not name.startswith("."):
        return name
    try:
        return importlib.util.resolve_name(name, package)
    except (ImportError, ValueError):
        return None


def _module_files(roots: tuple[Path, ...], module: str) -> tuple[Path, ...]:
    """module の名が根の下で当たりうる file(根ごとに <名>.hy と <名>/__init__.hy)。"""
    return tuple(
        candidate
        for root in roots
        for candidate in (
            root.joinpath(*module.split(".")).with_suffix(".hy"),
            root.joinpath(*module.split(".")) / "__init__.hy",
        )
    )


def _macro_sources(
    roots: tuple[Path, ...],
    text: str,
    package: str,
    seen: frozenset[Path] = frozenset(),
    cache_dir: Path | None = None,
) -> tuple[Path, ...]:
    """source が require する根の下の .hy を推移的に集める(根の外 = doeff-hy などは _doeff_hy_digest が持つ)。
    package = source の相対の require を解く package。"""
    modules = tuple(
        absolute
        for name in _required_modules(text, cache_dir)
        if (absolute := _absolute_module(name, package)) is not None
    )
    found = tuple(
        _MacroSource(candidate, _package_of(module, candidate))
        for module in modules
        for candidate in _module_files(roots, module)
        if candidate.is_file() and candidate not in seen
    )
    known = seen | frozenset(macro.path for macro in found)
    return tuple(macro.path for macro in found) + tuple(
        deeper
        for macro in found
        for deeper in _macro_sources(
            roots, macro.path.read_text(encoding="utf-8"), macro.package, known, cache_dir
        )
    )


def cache_key(
    roots: tuple[Path, ...], source: Path, module: str, relative: str, cache_dir: Path | None = None
) -> str:
    """展開を引く鍵(上の docstring の 4 つが同じなら同じ展開になる)。cache_dir があれば require の読みもそこで引く。"""
    text = source.read_text(encoding="utf-8")
    digest = hashlib.sha256()
    for part in (str(CACHE_VERSION), _doeff_hy_digest(), module, relative, text):
        digest.update(part.encode())
        digest.update(b"\0")
    package = _package_of(module, source)
    for macro in sorted(set(_macro_sources(roots, text, package, cache_dir=cache_dir))):
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
