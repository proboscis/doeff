"""commit の hook の doeff-linter(error)と semgrep(Python)の所見を、規則ごと × file ごとの基点と比べる(agora-redesign #2848)。

本線には hook を据え付ける前からの所見が在る(2026-10-02 の測り: doeff-linter の error 721 件・296 file、semgrep の Python 74 件・
24 file)。hook がそれを丸ごと止めると、その file に触れる commit が全部止まる。だから hook は「基点より増えた所見」だけを止める
(packages/doeff-cluster の warning の基準値の検査 scripts/doeff_cluster_warning_baseline.py と、Hy の型の門と同じ形)。

基点(scripts/hook_finding_baseline/<道具>.json = {"version": 数えた道具の版, "counts": {規則: {repo の根からの path: 数}}})は
「通す一覧」ではなく比べの元。鍵は行番号に依らない(行がずれただけでは赤にしない)。基点は下がる向きにだけ動く:

  check <道具> [path …]  測った file について、数が基点より多ければ赤(新しい所見 — 直す)。少ないのに基点が下がっていなければ
                         赤(直した便が同じ commit で基点を下げる — 下げ忘れると次の新しい所見が黙って入る)。path が無ければ
                         repo の全部の file。doeff-linter は、基点が名乗る組み立ての入力の鍵と同じ鍵の binary を land-arm の
                         開発版 → 断面の置き場から探して呼ぶ(scripts/doeff_linter_locked.py・#2906 — 探し道の linter は見ない)。
                         置き場に無ければ(linter を変えた便の commit の間・land-arm が組み直すまでの数分)、赤でも緑でもなく
                         「測れない」と名指して通す — hook の中では組まない。semgrep の版は木の中の uv.lock だけで決まる
                         (scripts/semgrep_locked.py・#2906)ので、基点と違えば「lock を上げたのに数え直していない」木として赤。
  lower <道具>           repo の全部の file を測り、基点を今の数まで下げ、版をその道具の版にする(上げない・新しい組を足さない)。
                         直した便と、道具の版を変えた便が実行して stage する。今の版で基点より増えた組は下げられないので名指す。
                         doeff-linter は `--linter <binary>` で渡した物(linter を変えた便の自分の build)か、HEAD の組み立ての
                         入力の鍵の binary(置き場に無ければ止めて名指す)。
  init <道具>            基点の file が無い時だけ、repo の全部の file を測って版と一緒に書く(在れば断る — 上げる道にしない)。

道具 = doeff-linter(severity が error の物 — warning と info は hook を止めない)・semgrep(.semgrep.yaml・hook と同じく規則の
検体 tests/semgrep/fixtures/ を外す)。repo の全部の file = git が追跡している .py / .pyi のうち symlink でない物(semgrep は
symlink を測れない)。semgrep は全部の file を測る時も core を 2 つに絞る(既定は全部の core を使い、機体の load を跳ね上げた)。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

# 隣の module(doeff_linter_locked・semgrep_locked)は、この script の dir を置き場として明示で読む。script の dir が sys.path に入るのは
# 暗黙の既定で、PYTHONSAFEPATH=1(作業役の shell に在る)の下では入らず、commit の hook が import で落ちた(agora-redesign #3866 の続き)。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from doeff_linter_locked import binary_key, dev_key, input_key, locate, searched
from semgrep_locked import locked_command, locked_version

BASELINE_DIR: str = "scripts/hook_finding_baseline"
TOOLS: tuple[str, ...] = ("doeff-linter", "semgrep")
SEMGREP_FIXTURES: str = "tests/semgrep/fixtures/"
SEMGREP_JOBS: str = "2"

# 基点の file の JSON の形そのもの(規則 → file → 数)。JSON の境界の値なので写像のまま持つ。
Counts = dict[str, dict[str, int]]


@dataclass(frozen=True)
class Finding:
    """1 つの組(規則・file)の基点の数と今の数。"""

    rule: str
    path: str
    baseline: int
    current: int


@dataclass(frozen=True)
class Baseline:
    """基点: 数えた道具の版と、規則 × file の数(counts は JSON の境界の値なので写像のまま持つ)。"""

    version: str
    counts: Counts


@dataclass(frozen=True)
class Comparison:
    """比べの結末: grown = 基点より増えた組(新しい所見)・stale = 減ったのに基点が下がっていない組。"""

    grown: tuple[Finding, ...]
    stale: tuple[Finding, ...]


def repo_path(top: Path, reported: str) -> str:
    """道具が報せた path(絶対のことも根からのこともある)を、repo の根からの path に揃えるため(鍵を作業木の置き場に依らせない)。"""
    path: Path = Path(reported)
    if not path.is_absolute():
        return path.as_posix()
    resolved: Path = path.resolve()
    if not resolved.is_relative_to(top.resolve()):
        raise SystemExit(f"道具が repo の外の path を報せた: {reported}(基点の鍵にできない)")
    return resolved.relative_to(top.resolve()).as_posix()


def tally(pairs: list[tuple[str, str]]) -> Counts:
    """(規則, path) の列を、規則 × file の数にするため。"""
    rules: list[str] = sorted({rule for rule, _ in pairs})
    return {
        rule: {path: count for path in sorted({p for r, p in pairs if r == rule}) if (count := pairs.count((rule, path)))}
        for rule in rules
    }


def linter_error_counts(report: list[dict[str, object]], top: Path) -> Counts:
    """doeff-linter の JSON(規則ごとの組の列)から、severity が error の所見だけを規則 × file で数える。"""
    return tally(
        [
            (str(entry["rule"]), repo_path(top, str(violation["file"])))
            for entry in report
            if entry.get("severity") == "error" and isinstance(violations := entry.get("violations"), list)
            for violation in violations
        ]
    )


def semgrep_counts(report: dict[str, object], top: Path) -> Counts:
    """semgrep の JSON の results から、規則(check_id の最後の名)× file で数える。"""
    results = report.get("results")
    if not isinstance(results, list):
        raise SystemExit("semgrep の JSON に results の列が無い")
    return tally([(str(r["check_id"]).split(".")[-1], repo_path(top, str(r["path"]))) for r in results])


def _count(counts: Counts, rule: str, path: str) -> int:
    """基点にも今にも無い組を 0 として比べるため(無い = その規則の所見がその file に 1 つも無い)。"""
    return counts.get(rule, {}).get(path, 0)


def compare(baseline: Counts, current: Counts, measured: frozenset[str] | None) -> Comparison:
    """基点と今の数を、測った file(None = 全部の file)の組ごとに比べる。"""
    pairs: set[tuple[str, str]] = {
        (rule, path)
        for rule in set(baseline) | set(current)
        for path in set(baseline.get(rule, {})) | set(current.get(rule, {}))
        if measured is None or path in measured
    }
    findings: list[Finding] = sorted(
        (Finding(rule, path, _count(baseline, rule, path), _count(current, rule, path)) for rule, path in pairs),
        key=lambda f: (f.rule, f.path),
    )
    return Comparison(
        grown=tuple(f for f in findings if f.current > f.baseline),
        stale=tuple(f for f in findings if f.current < f.baseline),
    )


def lowered(baseline: Counts, current: Counts) -> Counts:
    """基点を今の数まで下げる(上げない・基点に無い組は足さない・0 になった組は消す)。"""
    result: Counts = {
        rule: {
            path: min(count, _count(current, rule, path))
            for path, count in sorted(per_file.items())
            if min(count, _count(current, rule, path)) > 0
        }
        for rule, per_file in sorted(baseline.items())
    }
    return {rule: per_file for rule, per_file in result.items() if per_file}


# git の無い木(日次の段が zeus へ写した木 — remote_check は git ls-files の名簿の file だけを送り .git を送らない)を歩く時に
# 入らない dir: 検査の走行や環境が作る物で、git が追跡しない(#2906)。
UNTRACKED_DIRS: frozenset[str] = frozenset({".git", ".venv", "node_modules", "__pycache__"})


def _tracked_python(top: Path) -> list[str] | None:
    """git が追跡している .py / .pyi(git の作業木でなければ None)。"""
    listed: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py", "*.pyi"], cwd=top, capture_output=True, text=True, check=False
    )
    return sorted(p for p in listed.stdout.split("\0") if p) if listed.returncode == 0 else None


def _walked_python(top: Path) -> list[str]:
    """git の無い木の .py / .pyi(作られる dir には入らない)— 日次の段の木は git の追跡している file の写しなので、歩けば同じ名簿。"""
    def walk() -> Iterator[str]:
        for directory, subdirectories, files in os.walk(top):
            subdirectories[:] = sorted(d for d in subdirectories if d not in UNTRACKED_DIRS)
            yield from ((Path(directory) / f).relative_to(top).as_posix() for f in files if f.endswith((".py", ".pyi")))
    return sorted(walk())


def population(top: Path, tool: str) -> list[str]:
    """repo の全部の file(git が追跡している .py / .pyi・symlink でない物・semgrep は規則の検体を外す)。git の作業木でない木
    (日次の段)では dir を歩いて名簿を作り、そう名乗る。"""
    tracked: list[str] | None = _tracked_python(top)
    if tracked is None:
        print(f"{tool}: git の作業木でない木 — dir を歩いて名簿を作った({'・'.join(sorted(UNTRACKED_DIRS))} には入らない)",
              file=sys.stderr)
    return [
        path
        for path in (tracked if tracked is not None else _walked_python(top))
        if not (top / path).is_symlink() and not (tool == "semgrep" and path.startswith(SEMGREP_FIXTURES))
    ]


@dataclass(frozen=True)
class Instrument:
    """測りに使う道具: 呼ぶ命令と、その道具の版(基点に書く・基点と照らす鍵)。"""

    command: tuple[str, ...]
    version: str


def semgrep_instrument(top: Path) -> Instrument:
    """semgrep は uv.lock が決める版を uv の道具の置き場から呼ぶ(scripts/semgrep_locked.py・#2906)— 探し道や作業木の .venv の
    semgrep は機体と作業木の状態で版が割れていた。版は lock から読み、`--version` を聞き直さない(1 回 約 2 秒)。"""
    return Instrument(tuple(locked_command(top)), locked_version(top))


def linter_for_writing(top: Path, linter: Path | None) -> Instrument:
    """基点を書く(init・lower)linter: 渡された binary(linter を変えた便が自分の build を渡す)か、HEAD の組み立ての入力の鍵の
    binary(land-arm の開発版 → 断面の置き場)。無ければ名指して止める(探し道の linter で数えて違う鍵を書かない)。"""
    if linter is not None:
        return Instrument((str(linter),), binary_key(top, linter))
    head: str | None = input_key(top, "HEAD")
    located = locate(top, head) if head is not None else None
    if located is None:
        raise SystemExit(f"HEAD の linter の組み立ての入力({head})の binary が {searched()} に無い — 組んだ binary を "
                         "`--linter <path>` で渡す")
    return Instrument((str(located.binary),), binary_key(top, located.binary))


def linter_for_checking(top: Path, baseline: Baseline) -> Instrument | None:
    """基点と比べる linter: 基点が名乗る鍵と同じ組み立ての入力の binary(scripts/doeff_linter_locked.py・#2906)。hook の中では
    組まない — 置き場に無ければ None(呼び手が「測れない」と名指す)。"""
    located = locate(top, baseline.version)
    return Instrument((str(located.binary),), baseline.version) if located is not None else None


def read_baseline(file: Path) -> Baseline:
    """基点の file を読むため(形が違えば止める — 既定の 0 にしない)。"""
    raw = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("version"), str) or not isinstance(raw.get("counts"), dict):
        raise SystemExit(f"基点の file の形が違う(version と counts が要る): {file}")
    return Baseline(version=raw["version"], counts=raw["counts"])


def measure(top: Path, tool: str, instrument: Instrument, paths: list[str]) -> Counts:
    """道具を repo の根で 1 回走らせて、規則 × file の数を得るため(測れなかった時は止める — 0 として通さない)。"""
    if not paths:
        return {}
    if tool == "doeff-linter":
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            # --force-exclude: 名指した file にも根の設定の exclude(規則の対象の外 — pyproject.toml の [tool.doeff-linter] の註)を
            # 当てる。無いと、file を名指して測るこの入口では exclude が効かない(agora-redesign #3012)。
            [*instrument.command, "--no-log", "--force-exclude", "--output-format", "json", *paths],
            cwd=top, capture_output=True, text=True, check=False,
        )
        if completed.returncode not in (0, 1):
            raise SystemExit(f"doeff-linter が測れなかった(rc {completed.returncode}): {completed.stderr.strip()}")
        report = json.loads(completed.stdout or "[]")
        if not isinstance(report, list):
            raise SystemExit("doeff-linter の JSON が組の列でない")
        return linter_error_counts(report, top)
    completed = subprocess.run(
        [*instrument.command, "--metrics=off", "--disable-version-check", "--config", ".semgrep.yaml", "--json",
         "--quiet", "-j", SEMGREP_JOBS, *paths],
        cwd=top, capture_output=True, text=True, check=False,
    )
    report = json.loads(completed.stdout or "{}")
    if not isinstance(report, dict):
        raise SystemExit(f"semgrep が測れなかった(rc {completed.returncode}): {completed.stderr.strip()[:500]}")
    errors = report.get("errors")
    if errors:
        raise SystemExit(f"semgrep が測れなかった: {json.dumps(errors, ensure_ascii=False)[:500]}")
    return semgrep_counts(report, top)


def _line(finding: Finding) -> str:
    """赤の理由の 1 行(規則・file・基点と今の数)を出すため。"""
    return f"  {finding.rule} {finding.path}: 基点 {finding.baseline} → 今 {finding.current}"


def _write(file: Path, baseline: Baseline) -> None:
    """基点の file を同じ形(鍵の順・字下げ)で書くため。"""
    payload = {"version": baseline.version, "counts": baseline.counts}
    file.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _unmeasured_hint(top: Path, baseline: Baseline) -> str:
    """「測れない」の時にどうすれば測れるか: land-arm の開発版が基点と別の鍵で在れば、main を取り込めば(基点がその鍵に揃い)測れる。"""
    dev: str | None = dev_key(top)
    if dev is not None and dev != baseline.version:
        return "main を取り込めば land-arm の開発版で測れる"
    return "land-arm が main の linter を組み直すのを待つ"


USAGE: str = (
    "使い方: hook_finding_baseline.py [--root <dir>] [--linter <組んだ doeff-linter>] [--strict] "
    "check|lower|init doeff-linter|semgrep [path …]"
)
#: 「測れない」を赤(3)にする旗の答え — 日次の段が使う(hook は通す・日次は通した commit を測る所なので通さない・#2906)。
UNMEASURED_RC: int = 3


@dataclass(frozen=True)
class Request:
    """入口の引数: 根(検が一時の repo で試すため)・lower と init で使う linter の binary(linter を変えた便の自分の build)・
    「測れない」の時に返す値(hook は 0 で通す・日次の段は --strict で UNMEASURED_RC)・動詞・道具・file。"""

    top: Path
    linter: Path | None
    unmeasured_rc: int
    command: str
    tool: str
    paths: list[str]


def parse(argv: list[str]) -> Request | None:
    """入口の引数を読むため(読めなければ None — 使い方を出して 2)。根の既定はこの script の在り処の 1 つ上。"""
    top: Path = Path(__file__).resolve().parents[1]
    linter: Path | None = None
    unmeasured_rc: int = 0
    rest: list[str] = argv
    while rest[:1] == ["--strict"] or (rest[:1] in (["--root"], ["--linter"]) and len(rest) >= 2):
        if rest[0] == "--strict":
            unmeasured_rc, rest = UNMEASURED_RC, rest[1:]
            continue
        if rest[0] == "--root":
            top = Path(rest[1])
        else:
            linter = Path(rest[1]).resolve()
        rest = rest[2:]
    if len(rest) < 2 or rest[0] not in ("check", "lower", "init") or rest[1] not in TOOLS:
        return None
    return Request(top=top, linter=linter, unmeasured_rc=unmeasured_rc, command=rest[0], tool=rest[1], paths=rest[2:])


def main(argv: list[str]) -> int:
    """check(commit の hook)・lower(直した便)・init(基点が無い時の 1 回)の 3 つの入口。"""
    request: Request | None = parse(argv)
    if request is None:
        print(USAGE, file=sys.stderr)
        return 2
    top, tool = request.top, request.tool
    baseline_file: Path = top / BASELINE_DIR / f"{tool}.json"
    if request.command != "init" and not baseline_file.is_file():
        print(f"基点の file が無い: {baseline_file}(既定の 0 にしない)", file=sys.stderr)
        return 2
    if request.command == "check":
        return _check(top, tool, read_baseline(baseline_file), request.paths, request.unmeasured_rc)
    instrument: Instrument = semgrep_instrument(top) if tool == "semgrep" else linter_for_writing(top, request.linter)
    if request.command == "init":
        if baseline_file.exists():
            print(f"基点の file が在る: {baseline_file}(init は書き直さない — 下げるのは lower)", file=sys.stderr)
            return 2
        baseline_file.parent.mkdir(parents=True, exist_ok=True)
        _write(baseline_file, Baseline(version=instrument.version,
                                       counts=measure(top, tool, instrument, population(top, tool))))
        print(f"基点を書いた: {baseline_file}({instrument.version})")
        return 0
    baseline: Baseline = read_baseline(baseline_file)
    current: Counts = measure(top, tool, instrument, population(top, tool))
    grown_now: tuple[Finding, ...] = compare(baseline.counts, current, None).grown
    _write(baseline_file, Baseline(version=instrument.version, counts=lowered(baseline.counts, current)))
    print(f"基点を下げた: {baseline_file}({baseline.version} → {instrument.version})")
    if grown_now:
        print(f"{tool} の今の版で基点より多い組(下げる道では上げない — 直すか、規則の持ち主が扱いを決める):", file=sys.stderr)
        print("\n".join(_line(f) for f in grown_now), file=sys.stderr)
    return 0


def _check(top: Path, tool: str, baseline: Baseline, paths: list[str], unmeasured_rc: int) -> int:
    """check の入口: 基点と同じ版の道具で、測った file の数を基点と比べる(赤 = 1・通す = 0・測れない = unmeasured_rc)。"""
    instrument: Instrument | None = semgrep_instrument(top) if tool == "semgrep" else linter_for_checking(top, baseline)
    if instrument is None:
        # doeff-linter: 基点の鍵の binary が置き場に無い(少し前の main から切った作業木・linter を変えた便の commit の間・
        # land-arm が組み直すまでの数分)。hook は止めずに通す — 通した commit は日次の段(--strict)が同じ基点の形で測り、
        # 日次では測れないことそのものを赤にする(#2906)。
        print(f"doeff-linter: 測れない(基点の版 {baseline.version} の binary が {searched()} のどちらにも無い — hook の中では"
              f"組まない)。{_unmeasured_hint(top, baseline)}。linter を変えた便は自分の build を "
              "`uv run --no-project python scripts/hook_finding_baseline.py --linter <binary> lower doeff-linter` で渡して基点を数え直す"
              + ("(--strict: 測れないを赤にする)" if unmeasured_rc else ""), file=sys.stderr)
        return unmeasured_rc
    if instrument.version != baseline.version:
        # semgrep の版は木の中の uv.lock だけで決まる(機体に依らない)— 違うのは lock を上げたのに基点を数え直していない木だけ。
        print(f"semgrep: 基点の版 {baseline.version} と uv.lock の版 {instrument.version} が違う — lock を上げた便が "
              "`uv run --no-project python scripts/hook_finding_baseline.py lower semgrep` で基点を数え直し、同じ commit に入れる",
              file=sys.stderr)
        return 1
    checked: list[str] = (
        [p for p in paths if not (tool == "semgrep" and p.startswith(SEMGREP_FIXTURES))] if paths else population(top, tool)
    )
    comparison: Comparison = compare(baseline.counts, measure(top, tool, instrument, checked),
                                     frozenset(checked) if paths else None)
    if comparison.grown:
        print(f"{tool} の所見が基点より増えた(新しい所見は直す — 基点に足さない):", file=sys.stderr)
        print("\n".join(_line(f) for f in comparison.grown), file=sys.stderr)
    if comparison.stale:
        print(f"{tool} の所見が基点より減ったのに基点が下がっていない — "
              f"`uv run --no-project python scripts/hook_finding_baseline.py lower {tool}` を実行して stage する:", file=sys.stderr)
        print("\n".join(_line(f) for f in comparison.stale), file=sys.stderr)
    return 1 if comparison.grown or comparison.stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
