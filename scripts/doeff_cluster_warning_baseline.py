"""packages/doeff-cluster の warning(major)の数を、規則ごと × file ごとの基点と比べる(agora-redesign #2683)。

error(critical)は lint-doeff-cluster.sh が doeff-linter の終了コードで止める。warning は linter が 0 で終わるので、ここで
基点の file(packages/doeff-cluster/lint-warning-baseline.json = {規則: {package の dir からの path: 数}})と比べる:

  check [path …]  測った file について、数が基点より多ければ赤(新しい warning)。少ないのに基点が下がっていなければ赤
                  (直した便が同じ commit で基点を下げる — 下げ忘れると次の新しい違反が黙って入る)。path を渡さなければ package 全体。
  lower           package 全体を測り、基点を今の数まで下げる(上げない・新しい組を足さない)。直した便が実行して stage する。

既知の一覧で「通す」物ではない: 基点は下がる向きにだけ動く。linter は package の dir で走らせる(lint-doeff-cluster.sh と同じ)。
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from doeff import run, with_handlers
from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import ReadEnvironment

PACKAGE: str = "packages/doeff-cluster"
BASELINE_NAME: str = "lint-warning-baseline.json"
DECLARATION: str = "architecture.hy"
BASELINE_ENV: str = "DOEFF_CLUSTER_WARNING_BASELINE"

# 基点の file の JSON の形そのもの(規則 → file → 数)。JSON の境界の値なので写像のまま持つ。
Counts = dict[str, dict[str, int]]


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    baseline: int
    current: int


def package_path(package_dir: Path, reported: str) -> str:
    """linter が報せた path(絶対のことも package の dir からのこともある)を、package の dir からの path に揃えるため。

    鍵が作業木の置き場に依らないようにする(ddc164310 は絶対 path を鍵にしたので、別の作業木では lower が基点を空にし、
    path を渡す check は 1 つも比べずに通った — agora-redesign #2683 の cc1-w38 の実測)。package の外の path は名指して止める。"""
    path: Path = Path(reported)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve().relative_to(package_dir.resolve()).as_posix()
    except ValueError:
        raise SystemExit(f"linter が package の外の path を報せた: {reported}(基点の鍵にできない)") from None


def warning_counts(report: list[dict[str, object]], package_dir: Path) -> Counts:
    """linter の JSON(規則ごとの組の列)から、warning だけを規則 × file(package の dir からの path)で数える。"""
    paths_by_rule: dict[str, list[str]] = {
        str(entry["rule"]): [package_path(package_dir, str(v["file"])) for v in violations]
        for entry in report
        if entry.get("severity") == "warning" and isinstance(violations := entry.get("violations"), list)
    }
    return {rule: {path: paths.count(path) for path in sorted(set(paths))} for rule, paths in paths_by_rule.items() if paths}


def _count(counts: Counts, rule: str, path: str) -> int:
    """基点にも今にも無い組を 0 として比べるため(無い = その規則の warning がその file に 1 つも無い)。"""
    return counts.get(rule, {}).get(path, 0)


@dataclass(frozen=True)
class Comparison:
    """比べの結末: grown = 基点より増えた組(新しい違反)・stale = 減ったのに基点が下がっていない組。"""

    grown: tuple[Finding, ...]
    stale: tuple[Finding, ...]


def compare(baseline: Counts, current: Counts, measured: frozenset[str] | None) -> Comparison:
    """基点と今の数を比べる。measured = 測った file の集合(None = package 全体を測った)。"""
    rules: set[str] = set(baseline) | set(current)
    pairs: set[tuple[str, str]] = {
        (rule, path)
        for rule in rules
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


def _run_linter(linter: str, package_dir: Path, paths: list[str]) -> list[dict[str, object]]:
    """package の dir で linter を 1 回走らせて JSON の報告を得るため(rc 0 / 1 以外は測れなかったとして止める)。"""
    arguments: list[str] = [linter, "--no-log", "--output-format", "json", *paths]
    completed: subprocess.CompletedProcess[str] = subprocess.run(
        arguments, cwd=package_dir, capture_output=True, text=True, check=False,
    )
    if completed.returncode not in (0, 1):
        raise SystemExit(f"doeff-linter が測れなかった(rc {completed.returncode}): {completed.stderr.strip()}")
    report = json.loads(completed.stdout or "[]")
    if not isinstance(report, list):
        raise SystemExit("doeff-linter の JSON が組の列でない")
    return report


def _package_paths(repo_paths: list[str]) -> list[str]:
    """commit の hook が渡す repo の根からの path を package の dir からの path にし、宣言の file を必ず足すため。"""
    prefix: str = f"{PACKAGE}/"
    inside: list[str] = [p[len(prefix):] for p in repo_paths if p.startswith(prefix) and p != f"{prefix}{DECLARATION}"]
    return [*inside, DECLARATION]


def _line(finding: Finding) -> str:
    """赤の理由の 1 行(規則・file・基点と今の数)を出すため。"""
    return f"  {finding.rule} {finding.path}: 基点 {finding.baseline} → 今 {finding.current}"


def main(argv: list[str]) -> int:
    """check(commit の hook と make lint-doeff)と lower(直した便)の 2 つの入口。"""
    # 置き場の根は呼び手(lint-doeff-cluster.sh)が --root で渡す。無ければこの script の在り処の 1 つ上。linter は commit の hook が
    # --linter で HEAD の組み立ての入力の鍵の binary を渡す(#2906)。無ければ探し道の doeff-linter(make lint-doeff)。
    top: Path = Path(__file__).resolve().parents[1]
    linter: str = "doeff-linter"
    while argv[:1] in (["--root"], ["--linter"]) and len(argv) >= 2:
        if argv[0] == "--root":
            top = Path(argv[1])
        else:
            linter = argv[1]
        argv = argv[2:]
    package_dir: Path = top / PACKAGE
    # 基点の置き場を差し替えるのは make の入口の検だけ(偽の linter と空の基点で終了コードの伝わり方を試す)。
    # 置き場の差し替えは環境変数の値 — os.environ を直に読まず、本物の答え手の下の ReadEnvironment で問う(agora-redesign #3012)。
    entries = run(with_handlers([subprocess_handler], ReadEnvironment((BASELINE_ENV,))))
    configured = next((entry.value for entry in entries if entry.name == BASELINE_ENV), None)
    baseline_file: Path = Path(configured or package_dir / BASELINE_NAME)
    if not argv or argv[0] not in ("check", "lower"):
        print("使い方: doeff_cluster_warning_baseline.py check [path …] | lower", file=sys.stderr)
        return 2
    if not baseline_file.is_file():
        print(f"基点の file が無い: {baseline_file}(既定の 0 にしない)", file=sys.stderr)
        return 2
    baseline: Counts = json.loads(baseline_file.read_text(encoding="utf-8"))
    if argv[0] == "lower":
        current: Counts = warning_counts(_run_linter(linter, package_dir, []), package_dir)
        baseline_file.write_text(json.dumps(lowered(baseline, current), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")
        print(f"基点を下げた: {baseline_file}")
        return 0
    paths: list[str] = _package_paths(argv[1:]) if len(argv) > 1 else []
    current = warning_counts(_run_linter(linter, package_dir, paths), package_dir)
    measured: frozenset[str] | None = frozenset(paths) if paths else None
    comparison: Comparison = compare(baseline, current, measured)
    if comparison.grown:
        print(f"{PACKAGE} の warning が基点より増えた(新しい違反は直す — 基点に足さない):", file=sys.stderr)
        print("\n".join(_line(f) for f in comparison.grown), file=sys.stderr)
    if comparison.stale:
        print(f"{PACKAGE} の warning が基点より減ったのに基点が下がっていない — "
              "`uv run --no-project python scripts/doeff_cluster_warning_baseline.py lower` を実行して stage する:", file=sys.stderr)
        print("\n".join(_line(f) for f in comparison.stale), file=sys.stderr)
    return 1 if comparison.grown or comparison.stale else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
