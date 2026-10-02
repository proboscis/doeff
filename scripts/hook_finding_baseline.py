"""commit の hook の doeff-linter(error)と semgrep(Python)の所見を、規則ごと × file ごとの基点と比べる(agora-redesign #2848)。

本線には hook を据え付ける前からの所見が在る(2026-10-02 の測り: doeff-linter の error 721 件・296 file、semgrep の Python 74 件・
24 file)。hook がそれを丸ごと止めると、その file に触れる commit が全部止まる。だから hook は「基点より増えた所見」だけを止める
(packages/doeff-cluster の warning の基準値の検査 scripts/doeff_cluster_warning_baseline.py と、Hy の型の門と同じ形)。

基点(scripts/hook_finding_baseline/<道具>.json = {"version": 数えた道具の版, "counts": {規則: {repo の根からの path: 数}}})は
「通す一覧」ではなく比べの元。鍵は行番号に依らない(行がずれただけでは赤にしない)。基点は下がる向きにだけ動く:

  check <道具> [path …]  測った file について、数が基点より多ければ赤(新しい所見 — 直す)。少ないのに基点が下がっていなければ
                         赤(直した便が同じ commit で基点を下げる — 下げ忘れると次の新しい所見が黙って入る)。path が無ければ
                         repo の全部の file。道具の版が基点の版と違う時は、赤でも緑でもなく「測れない」と名指して通す — hook の
                         道具(doeff-linter は ~/.cargo/bin の binary)は repo の main と別に版が動き、版が変わると同じ code で
                         数が変わる(版の違いで全席の commit が止まる・黙って通る、のどちらも避ける)。
  lower <道具>           repo の全部の file を今の道具で測り、基点を今の数まで下げ、版を今の版にする(上げない・新しい組を足さない)。
                         直した便と、道具の版を入れ直した便が実行して stage する。今の版で基点より増えた組は下げられないので名指す。
  init <道具>            基点の file が無い時だけ、repo の全部の file を測って版と一緒に書く(在れば断る — 上げる道にしない)。

道具 = doeff-linter(severity が error の物 — warning と info は hook を止めない)・semgrep(.semgrep.yaml・hook と同じく規則の
検体 tests/semgrep/fixtures/ を外す)。repo の全部の file = git が追跡している .py / .pyi のうち symlink でない物(semgrep は
symlink を測れない)。semgrep は全部の file を測る時も core を 2 つに絞る(既定は全部の core を使い、機体の load を跳ね上げた)。
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

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


def population(top: Path, tool: str) -> list[str]:
    """repo の全部の file(git が追跡している .py / .pyi・symlink でない物・semgrep は規則の検体を外す)。"""
    listed: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py", "*.pyi"], cwd=top, capture_output=True, text=True, check=True
    )
    return [
        path
        for path in sorted(p for p in listed.stdout.split("\0") if p)
        if not (top / path).is_symlink() and not (tool == "semgrep" and path.startswith(SEMGREP_FIXTURES))
    ]


LINTER_SOURCE: str = "packages/doeff-linter"


def tool_command(top: Path, tool: str) -> list[str]:
    """道具を呼ぶ命令。semgrep は repo の dev の依存(uv.lock が版を決める)を uv で呼ぶ — 探し道の semgrep は、venv が有効かどうかで
    版が割れ(2026-10-02: venv 1.169.0・uv tool 1.161.0)、基点と版が合わない「測れない」に黙って倒れるため。uv.lock の無い根
    (検の一時の repo)では探し道の物。doeff-linter は Rust の binary で、探し道(~/.cargo/bin)の物 — 版は tool_version が鍵にする。"""
    if tool == "semgrep" and (top / "uv.lock").is_file():
        return ["uv", "run", "--frozen", "--no-sync", "--project", str(top), "semgrep"]
    return [tool]


def tool_version(top: Path, tool: str) -> str:
    """hook が呼ぶ道具の版。semgrep = `--version` の 1 行目。doeff-linter = build した doeff の commit(`--version` が名乗る)の
    packages/doeff-linter の木の hash — linter の中身が同じなら、branch で build しても着地の後に main から build し直しても同じ鍵に
    なる(数を変える linter の便が、自分の build で基点を下げて同じ便で出せるように)。その commit を手元の git が知らない時は、
    `--version` の 1 行目をそのまま鍵にする(基点と合わず「測れない」になる — 黙って通さない)。"""
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        [*tool_command(top, tool), "--version"] if tool == "doeff-linter"
        else [*tool_command(top, tool), "--disable-version-check", "--version"],
        cwd=top, capture_output=True, text=True, check=False,
    )
    lines: list[str] = completed.stdout.strip().splitlines()
    if completed.returncode != 0 or not lines:
        raise SystemExit(f"{tool} の版を読めなかった(rc {completed.returncode}): {completed.stderr.strip()[:300]}")
    named: str = lines[0].strip()
    built = re.search(r"\(doeff ([0-9a-f]{7,40})\)", named) if tool == "doeff-linter" else None
    if built is None:
        return named
    tree: subprocess.CompletedProcess[str] = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{built.group(1)}:{LINTER_SOURCE}"],
        cwd=top, capture_output=True, text=True, check=False,
    )
    return f"doeff-linter の source の木 {tree.stdout.strip()}" if tree.returncode == 0 and tree.stdout.strip() else named


def read_baseline(file: Path) -> Baseline:
    """基点の file を読むため(形が違えば止める — 既定の 0 にしない)。"""
    raw = json.loads(file.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("version"), str) or not isinstance(raw.get("counts"), dict):
        raise SystemExit(f"基点の file の形が違う(version と counts が要る): {file}")
    return Baseline(version=raw["version"], counts=raw["counts"])


def measure(top: Path, tool: str, paths: list[str]) -> Counts:
    """道具を repo の根で 1 回走らせて、規則 × file の数を得るため(測れなかった時は止める — 0 として通さない)。"""
    if not paths:
        return {}
    if tool == "doeff-linter":
        completed: subprocess.CompletedProcess[str] = subprocess.run(
            [*tool_command(top, tool), "--no-log", "--output-format", "json", *paths],
            cwd=top, capture_output=True, text=True, check=False,
        )
        if completed.returncode not in (0, 1):
            raise SystemExit(f"doeff-linter が測れなかった(rc {completed.returncode}): {completed.stderr.strip()}")
        report = json.loads(completed.stdout or "[]")
        if not isinstance(report, list):
            raise SystemExit("doeff-linter の JSON が組の列でない")
        return linter_error_counts(report, top)
    completed = subprocess.run(
        [*tool_command(top, tool), "--metrics=off", "--disable-version-check", "--config", ".semgrep.yaml", "--json",
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


def main(argv: list[str]) -> int:
    """check(commit の hook)・lower(直した便)・init(基点が無い時の 1 回)の 3 つの入口。"""
    # 根は呼び手が --root で渡せる(検が一時の repo で試すため)。無ければこの script の在り処の 1 つ上。
    top: Path = Path(__file__).resolve().parents[1]
    if argv[:1] == ["--root"] and len(argv) >= 2:
        top, argv = Path(argv[1]), argv[2:]
    if len(argv) < 2 or argv[0] not in ("check", "lower", "init") or argv[1] not in TOOLS:
        print("使い方: hook_finding_baseline.py [--root <dir>] check|lower|init doeff-linter|semgrep [path …]", file=sys.stderr)
        return 2
    command, tool, paths = argv[0], argv[1], argv[2:]
    baseline_file: Path = top / BASELINE_DIR / f"{tool}.json"
    version: str = tool_version(top, tool)
    if command == "init":
        if baseline_file.exists():
            print(f"基点の file が在る: {baseline_file}(init は書き直さない — 下げるのは lower)", file=sys.stderr)
            return 2
        baseline_file.parent.mkdir(parents=True, exist_ok=True)
        _write(baseline_file, Baseline(version=version, counts=measure(top, tool, population(top, tool))))
        print(f"基点を書いた: {baseline_file}({version})")
        return 0
    if not baseline_file.is_file():
        print(f"基点の file が無い: {baseline_file}(既定の 0 にしない)", file=sys.stderr)
        return 2
    baseline: Baseline = read_baseline(baseline_file)
    if command == "lower":
        current: Counts = measure(top, tool, population(top, tool))
        grown_now: tuple[Finding, ...] = compare(baseline.counts, current, None).grown
        _write(baseline_file, Baseline(version=version, counts=lowered(baseline.counts, current)))
        print(f"基点を下げた: {baseline_file}({baseline.version} → {version})")
        if grown_now:
            print(f"{tool} の今の版で基点より多い組(下げる道では上げない — 直すか、規則の持ち主が扱いを決める):", file=sys.stderr)
            print("\n".join(_line(f) for f in grown_now), file=sys.stderr)
        return 0
    if version != baseline.version:
        print(f"{tool}: 測れない(道具の版 {version} ≠ 基点の版 {baseline.version})— 基点と同じ版の道具で測るか、"
              f"版を入れ直した便が `uv run --no-project python scripts/hook_finding_baseline.py lower {tool}` で基点を数え直す",
              file=sys.stderr)
        return 0
    checked: list[str] = (
        [p for p in paths if not (tool == "semgrep" and p.startswith(SEMGREP_FIXTURES))] if paths else population(top, tool)
    )
    comparison: Comparison = compare(baseline.counts, measure(top, tool, checked), frozenset(checked) if paths else None)
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
