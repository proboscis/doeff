#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""変えた所の検 — 登記(`ai land request`)の前に手元で走らせる入口(agora-redesign #2605 の 2 本目・1 便目)。

commit の hook ではテストを走らせない(operator #1122・#794)。この入口は登記の前に書き手が `make test-changed` で走らせ、
日次と並ぶ。選び方と打ち切りの定義はこの file の 1 点:

1. 変えた file = 分岐点(既定 `git merge-base HEAD origin/main`)から作業木までの差分(commit 済みと未 commit の両方・
   追跡外の新しい file を含む)。検は index ではなく作業木の中身で走る(一部だけ stage した commit ではずれる)。
2. 当たる契約の組 = root の pyproject.toml の `[[tool.doeff.contract-tests]]`(path の接頭辞 → 検の列)のうち、
   接頭辞が変えた file に当たる行の検。表の読み手はここの `read_contract_table` の 1 か所で、形が違う・書いた検の
   file が無い時は止める(rc 2)。stub の突き合わせ・dogfood の登録・母集団の検は module を文字列で持つか木を歩くので、
   import の逆依存では選べない — だから固定の組で先頭に置く。
3. 変えた検の file そのもの(検の file かは pytest の集め手と同じ名の規則 — root の ini の python_files・
   doeff_hy_test_files・doeff_adr_hy_files と doeff-adr の DEFAULT_FILE_PATTERNS)。
4. 契約の組を先頭に 1 回の pytest(`-m "not e2e"`)にまとめ、合計の壁時計の上限(既定 60 秒)で打ち切る。

出力は 走った(緑)・赤・未測 の 3 つ。終わらなかった file・集めた検が 0 本の file・venv の無い作業木は「未測」と
名指し、赤と分ける。rc は 赤が在れば 1・表や入力が読めなければ 2・それ以外 0(未測は登記を止めない — 名指すだけ)。

pytest の結果は、この同じ file を pytest の plugin として読ませて(`-p run_changed_tests`)行ごとの JSON で受け取る
— 打ち切った時も、そこまでに終わった検の結末が残る。stdlib 単独(呼び口は `uv run --script`・機体の python は撃たない)。
import の逆依存(doeff-linter の `--affected-tests`)は 2 便目。
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import json
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

import tomllib

#: pytest の plugin として読まれた時に結末を書く file を運ぶ環境変数。
REPORT_ENV = "RUN_CHANGED_TESTS_REPORT"
#: pytest の既定の python_files(root の ini が書けば、そちらを読む)。
PYTEST_DEFAULT_PYTHON_FILES = ("test_*.py", "*_test.py")
#: doeff-adr の executable ADR の既定の pattern の定義元(ここを ast で読む — 2 つ目の一覧を書かない)。
ADR_PLUGIN_SOURCE = "packages/doeff-adr/src/doeff_adr/pytest_plugin.py"
ADR_PATTERNS_NAME = "DEFAULT_FILE_PATTERNS"
#: uv の project の環境の既定の置き場(UV_PROJECT_ENVIRONMENT が無い時)。無ければ何も測れない — 未測と名指す
#: (`uv run --no-sync` は環境が無いと空の環境を作ってしまうので、走らせる前に確かめる)。
DEFAULT_PROJECT_ENVIRONMENT = ".venv"
#: pytest の中で、この file を plugin として読ませて pytest を走らせる口(入口が `uv run --project` の環境から呼ぶ)。
PYTEST_MODE = "--as-pytest"
DEFAULT_BUDGET_SECONDS = 60.0
#: 上限の後、SIGTERM から SIGKILL までの猶予の秒。
KILL_GRACE_SECONDS = 3.0
ROW_KEYS = frozenset({"prefix", "tests"})
#: pytest の rc のうち、検の結末ではなく道具の誤り(3 = 内部の誤り・4 = 命令行の誤り)— 未測に畳まず止める。
PYTEST_TOOL_FAILURES = frozenset({3, 4})


class StopError(Exception):
    """表・設定・git の答えが読めない — 既定に倒さず止める(rc 2)。"""


# ---------------------------------------------------------------------------
# 契約の検の表
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContractRow:
    """表の 1 行: path の接頭辞(空 = repo 全体)と、当たった時に必ず走らせる検の列。"""

    prefix: str
    tests: tuple[str, ...]

    def covers(self, path: str) -> bool:
        return self.prefix in {"", path} or (
            self.prefix.endswith("/") and path.startswith(self.prefix)
        )


@dataclass(frozen=True)
class ContractTable:
    rows: tuple[ContractRow, ...]


def _test_path(test: str) -> str:
    """検の指定(`path` か `path::名`)の path の部分。"""
    return test.split("::", 1)[0]


def _parse_row(index: int, raw: object, repo: Path) -> ContractRow:
    where = f"[[tool.doeff.contract-tests]] の {index + 1} 行目"
    match raw:
        case dict() if set(raw) == ROW_KEYS:
            pass
        case dict():
            raise StopError(f"{where}: 鍵は {sorted(ROW_KEYS)} だけ(実際 {sorted(raw)})")
        case _:
            raise StopError(f"{where}: 表(prefix と tests)でない: {raw!r}")
    match raw["prefix"], raw["tests"]:
        case str() as prefix, list() as tests if tests and all(isinstance(t, str) for t in tests):
            pass
        case _:
            raise StopError(f"{where}: prefix は文字列・tests は空でない文字列の列: {raw!r}")
    if prefix and not (
        (prefix.endswith("/") and (repo / prefix).is_dir())
        or (not prefix.endswith("/") and (repo / prefix).is_file())
    ):
        raise StopError(
            f"{where}: 接頭辞 {prefix!r} が木に無い(dir は `/` で終え、file は名そのものを書く)"
        )
    missing = [t for t in tests if not (repo / _test_path(t)).is_file()]
    if missing:
        raise StopError(f"{where}(接頭辞 {prefix!r}): 書いた検の file が木に無い: {missing}")
    if len(set(tests)) != len(tests):
        raise StopError(f"{where}(接頭辞 {prefix!r}): 同じ検を 2 度書いている: {tests}")
    return ContractRow(prefix=prefix, tests=tuple(tests))


def read_contract_table(repo: Path) -> ContractTable:
    """root の pyproject.toml の `[[tool.doeff.contract-tests]]` を読む(読み手はこの 1 か所)。"""
    pyproject = repo / "pyproject.toml"
    if not pyproject.is_file():
        raise StopError(f"{pyproject} が無い")
    config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    match config.get("tool", {}).get("doeff", {}).get("contract-tests"):
        case list() as raw_rows if raw_rows:
            pass
        case None:
            raise StopError(f"{pyproject} に [[tool.doeff.contract-tests]] の表が無い")
        case other:
            raise StopError(f"[[tool.doeff.contract-tests]] は空でない表の列: {other!r}")
    rows = tuple(_parse_row(i, raw, repo) for i, raw in enumerate(raw_rows))
    prefixes = [row.prefix for row in rows]
    duplicated = sorted({p for p in prefixes if prefixes.count(p) > 1})
    if duplicated:
        raise StopError(f"[[tool.doeff.contract-tests]] に同じ接頭辞の行が 2 つ以上: {duplicated}")
    return ContractTable(rows=rows)


# ---------------------------------------------------------------------------
# 検の file の名の規則(pytest の集め手と同じ値を読む)
# ---------------------------------------------------------------------------


def _adr_default_patterns(repo: Path) -> tuple[str, ...]:
    source = repo / ADR_PLUGIN_SOURCE
    if not source.is_file():
        raise StopError(f"executable ADR の pattern の定義元 {ADR_PLUGIN_SOURCE} が無い")
    tree = ast.parse(source.read_text(encoding="utf-8"))
    values = [
        node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and [t.id for t in node.targets if isinstance(t, ast.Name)] == [ADR_PATTERNS_NAME]
    ]
    match values:
        case [value]:
            pass
        case _:
            raise StopError(f"{ADR_PLUGIN_SOURCE} に {ADR_PATTERNS_NAME} の代入が 1 つでない")
    try:
        patterns = ast.literal_eval(value)
    except ValueError as exc:
        raise StopError(f"{ADR_PLUGIN_SOURCE} の {ADR_PATTERNS_NAME} が literal でない") from exc
    match patterns:
        case tuple() if all(isinstance(p, str) for p in patterns):
            return patterns
        case _:
            raise StopError(f"{ADR_PATTERNS_NAME} が文字列の tuple でない: {patterns!r}")


def _ini_strings(ini: dict[str, object], key: str, default: tuple[str, ...]) -> tuple[str, ...]:
    match ini.get(key, default):
        case list() | tuple() as values if all(isinstance(v, str) for v in values):
            return tuple(values)
        case other:
            raise StopError(f"[tool.pytest.ini_options] の {key} が文字列の列でない: {other!r}")


def collector_patterns(repo: Path) -> tuple[str, ...]:
    """pytest が集める 3 つの経路の名の規則(Python・Hy の検・executable ADR)。"""
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    ini = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    return (
        *_ini_strings(ini, "python_files", PYTEST_DEFAULT_PYTHON_FILES),
        *_ini_strings(ini, "doeff_hy_test_files", ()),
        *_adr_default_patterns(repo),
        *_ini_strings(ini, "doeff_adr_hy_files", ()),
    )


def is_test_file(path: str, patterns: tuple[str, ...]) -> bool:
    """pytest の照合と同じ: `/` を含まない pattern は file の名に、含む pattern は repo の根からの path に当てる。"""
    name = path.rsplit("/", 1)[-1]
    return "fixtures" not in path.split("/") and any(
        fnmatch.fnmatchcase(path if "/" in p else name, p) for p in patterns
    )


# ---------------------------------------------------------------------------
# 変えた file と選び
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeSet:
    base: str
    files: tuple[str, ...]


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise StopError(f"git {' '.join(args)} が rc {proc.returncode}: {proc.stderr.strip()}")
    return proc.stdout


def changed_files(repo: Path, base: str | None) -> ChangeSet:
    """分岐点から作業木までに変えた file(削除を含む・rename は旧名と新名の両方)と追跡外の新しい file。"""
    resolved = base if base is not None else _git(repo, "merge-base", "HEAD", "origin/main").strip()
    sha = _git(repo, "rev-parse", "--verify", f"{resolved}^{{commit}}").strip()
    tracked = _git(repo, "diff", "--name-only", "--no-renames", sha).splitlines()
    untracked = _git(repo, "ls-files", "--others", "--exclude-standard").splitlines()
    return ChangeSet(base=sha, files=tuple(sorted({*tracked, *untracked} - {""})))


class Origin(Enum):
    CONTRACT = "契約の組"
    CHANGED = "変えた検"


@dataclass(frozen=True)
class Target:
    """1 回の pytest に渡す引数 1 つと、選ばれた理由。"""

    arg: str
    origin: Origin
    why: str


def select_targets(
    table: ContractTable, changes: ChangeSet, patterns: tuple[str, ...], repo: Path
) -> tuple[Target, ...]:
    """契約の組(表の順)を先頭に、変えた検の file(名の順)を後ろに — 同じ引数は 1 度だけ。"""
    contract = [
        Target(arg=test, origin=Origin.CONTRACT, why=f"接頭辞 {row.prefix or '(repo 全体)'}")
        for row in table.rows
        if any(row.covers(path) for path in changes.files)
        for test in row.tests
    ]
    changed = [
        Target(arg=path, origin=Origin.CHANGED, why="変えた検の file")
        for path in changes.files
        if is_test_file(path, patterns) and (repo / path).is_file()
    ]
    ordered = (*contract, *changed)
    return tuple(t for i, t in enumerate(ordered) if t.arg not in {u.arg for u in ordered[:i]})


# ---------------------------------------------------------------------------
# pytest の結末(plugin が書く行ごとの JSON)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Collected:
    nodeids: tuple[str, ...]


@dataclass(frozen=True)
class ItemReport:
    nodeid: str
    when: str
    outcome: str


@dataclass(frozen=True)
class CollectError:
    nodeid: str


Event = Collected | ItemReport | CollectError


def _parse_event(line: str) -> Event:
    match json.loads(line):
        case {"kind": "collected", "nodeids": list() as nodeids}:
            return Collected(nodeids=tuple(str(n) for n in nodeids))
        case {
            "kind": "report",
            "nodeid": str() as nodeid,
            "when": str() as when,
            "outcome": str() as outcome,
        }:
            return ItemReport(nodeid=nodeid, when=when, outcome=outcome)
        case {"kind": "collect_error", "nodeid": str() as nodeid}:
            return CollectError(nodeid=nodeid)
        case other:
            raise StopError(f"pytest の結末の行が読めない: {other!r}")


class _Item(Protocol):
    nodeid: str


class _Session(Protocol):
    items: list[_Item]


class _Report(Protocol):
    nodeid: str
    when: str | None
    outcome: str
    failed: bool


def _write_event(payload: dict[str, object]) -> None:
    """plugin の側: 結末を 1 行の JSON で足す(打ち切られても、そこまでの行が残るように 1 行ずつ閉じる)。"""
    os.write(int(os.environ[REPORT_ENV]), (json.dumps(payload, ensure_ascii=False) + "\n").encode())


# pytest の hook(この file を `-p run_changed_tests` で読ませた時だけ呼ばれる)。
def pytest_collection_finish(session: _Session) -> None:
    """どの検を集めたか(-m で外した後)を残す — 終わらなかった検を名指すための母数。"""
    _write_event({"kind": "collected", "nodeids": [item.nodeid for item in session.items]})


def pytest_runtest_logreport(report: _Report) -> None:
    """検 1 本の段(setup・call・teardown)の結末を残す — teardown が来た検を「終わった」と数える。"""
    _write_event(
        {"kind": "report", "nodeid": report.nodeid, "when": report.when, "outcome": report.outcome}
    )


def pytest_collectreport(report: _Report) -> None:
    """集める時に落ちた file を残す — 赤として名指す。"""
    if report.failed:
        _write_event({"kind": "collect_error", "nodeid": report.nodeid})


# ---------------------------------------------------------------------------
# 走らせる・分ける
# ---------------------------------------------------------------------------


class Verdict(Enum):
    GREEN = "走った"
    RED = "赤"
    UNMEASURED = "未測"


@dataclass(frozen=True)
class TargetResult:
    target: Target
    verdict: Verdict
    detail: str


@dataclass(frozen=True)
class PytestRun:
    events: tuple[Event, ...]
    returncode: int | None
    timed_out: bool
    seconds: float


def _belongs(nodeid: str, arg: str) -> bool:
    return (
        nodeid == arg
        or nodeid.startswith(arg + "::")
        or ("::" in arg and nodeid.startswith(arg + "["))
    )


def classify(target: Target, run: PytestRun, budget: float) -> TargetResult:
    collected = next((e for e in run.events if isinstance(e, Collected)), None)
    collect_errors = [
        e
        for e in run.events
        if isinstance(e, CollectError) and _belongs(e.nodeid, _test_path(target.arg))
    ]
    if collect_errors:
        return TargetResult(target, Verdict.RED, "集める時に落ちた")
    if collected is None:
        reason = (
            f"上限 {budget:g} 秒の内に集め終わらなかった"
            if run.timed_out
            else f"pytest が集める前に止まった(rc {run.returncode})"
        )
        return TargetResult(target, Verdict.UNMEASURED, reason)
    items = tuple(n for n in collected.nodeids if _belongs(n, target.arg))
    if not items:
        return TargetResult(
            target, Verdict.UNMEASURED, "集めた検が 0 本(e2e だけか、集め手に当たらない)"
        )
    reports = [e for e in run.events if isinstance(e, ItemReport) and e.nodeid in items]
    failed = sorted({r.nodeid for r in reports if r.outcome == "failed"})
    finished = {r.nodeid for r in reports if r.when == "teardown"}
    unfinished = [n for n in items if n not in finished]
    if failed:
        return TargetResult(target, Verdict.RED, f"赤 {len(failed)} 本: {', '.join(failed)}")
    if unfinished:
        reason = (
            f"上限 {budget:g} 秒で打ち切り"
            if run.timed_out
            else f"pytest が途中で止まった(rc {run.returncode})"
        )
        return TargetResult(
            target,
            Verdict.UNMEASURED,
            f"{reason} — {len(items) - len(unfinished)}/{len(items)} 本で止まった",
        )
    return TargetResult(target, Verdict.GREEN, f"{len(items)} 本")


def _read_pipe(fd: int, into: Future[str]) -> None:
    """pipe の読み口を書き手が全員閉じるまで読む(pytest の plugin が書く結末の行の全部)。"""
    with os.fdopen(fd, "r", encoding="utf-8") as fh:
        into.set_result(fh.read())


def _stop_group(proc: subprocess.Popen[bytes]) -> None:
    """上限を越えた pytest を process group ごと止める(SIGTERM・猶予の後 SIGKILL)。"""
    os.killpg(proc.pid, signal.SIGTERM)
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()


def project_environment(repo: Path) -> Path:
    """uv が `uv run --project <repo>` で使う環境の置き場(UV_PROJECT_ENVIRONMENT か `<repo>/.venv`)。"""
    configured = os.environ.get("UV_PROJECT_ENVIRONMENT")
    return repo / (configured if configured else DEFAULT_PROJECT_ENVIRONMENT)


def run_pytest(repo: Path, targets: tuple[Target, ...], budget: float) -> PytestRun:
    """1 回の pytest を作業木の uv の環境で壁時計の上限つきで走らせ、plugin が pipe へ書いた結末を受け取る
    (file には残さない)。"""
    read_fd, write_fd = os.pipe()
    env = {
        **{
            k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"
        },  # 外の `uv run --script` の環境を渡さない
        REPORT_ENV: str(write_fd),
    }
    command = [
        "uv",
        "run",
        "--no-sync",
        "--project",
        str(repo),
        "python",
        str(Path(__file__).resolve()),
        PYTEST_MODE,
        "-q",
        "-m",
        "not e2e",
        "--continue-on-collection-errors",
        *(t.arg for t in targets),
    ]
    received: Future[str] = Future()
    threading.Thread(target=_read_pipe, args=(read_fd, received), daemon=True).start()
    started = time.monotonic()
    proc = subprocess.Popen(
        command, cwd=repo, env=env, start_new_session=True, pass_fds=(write_fd,)
    )
    os.close(write_fd)
    try:
        returncode: int | None = proc.wait(timeout=budget)
        timed_out = False
    except subprocess.TimeoutExpired:
        _stop_group(proc)
        returncode, timed_out = None, True
    seconds = time.monotonic() - started
    try:
        text = received.result(timeout=KILL_GRACE_SECONDS)
    except FutureTimeout as exc:
        raise StopError(
            "pytest が終わった後も結末の pipe を持つ process が残っている(検が group の外へ逃がした子)"
        ) from exc
    events = tuple(_parse_event(line) for line in text.splitlines() if line.strip())
    if not timed_out and (returncode in PYTEST_TOOL_FAILURES or not events):
        raise StopError(
            f"pytest が rc {returncode} で、検の結末を{'返さずに' if not events else ''}止まった"
            "(uv・conftest・命令行の誤り)— 上の出力を読む"
        )
    return PytestRun(events=events, returncode=returncode, timed_out=timed_out, seconds=seconds)


def _print_summary(results: tuple[TargetResult, ...], seconds: float, budget: float) -> None:
    """3 つ(走った・赤・未測)に分けた要約を出す — 未測は理由つきで名指し、赤と混ぜない。"""
    print()
    print(f"== 変えた所の検の要約(所要 {seconds:.1f} 秒 / 上限 {budget:g} 秒)==")
    for verdict in Verdict:
        rows = [r for r in results if r.verdict is verdict]
        print(f"{verdict.value}: {len(rows)}")
        for r in rows:
            print(f"  {r.target.arg} — {r.detail}({r.target.origin.value}・{r.target.why})")


def main(argv: list[str] | None = None) -> int:
    """登記の前の入口: 変えた file → 契約の組 + 変えた検 → 1 回の pytest → 3 つに分けた要約と rc。"""
    parser = argparse.ArgumentParser(
        description="変えた所の検 — 登記の前に、契約の検の組と変えた検を 60 秒の上限で走らせる"
    )
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="走らせる作業木(既定 = 今の dir)"
    )
    parser.add_argument("--base", help="分岐点(既定 = git merge-base HEAD origin/main)")
    parser.add_argument(
        "--budget", type=float, default=DEFAULT_BUDGET_SECONDS, help="合計の壁時計の上限の秒"
    )
    args = parser.parse_args(argv)
    try:
        repo = Path(_git(args.repo, "rev-parse", "--show-toplevel").strip())
        table = read_contract_table(repo)
        patterns = collector_patterns(repo)
        changes = changed_files(repo, args.base)
    except StopError as exc:
        print(f"変えた所の検: 止める — {exc}", file=sys.stderr)
        return 2
    print(
        f"変えた所の検: 作業木の中身で走らせる(index ではない)— {repo}・分岐点 {changes.base[:12]}"
        f" から作業木までに変えた file {len(changes.files)} 本",
        flush=True,  # pytest の出力より先に出す
    )
    targets = select_targets(table, changes, patterns, repo)
    if not targets:
        print("当たる契約の組も変えた検の file も無い — 走らせる検は 0 本")
        return 0
    environment = project_environment(repo)
    if not (environment / "pyvenv.cfg").is_file():
        reason = f"作業木に uv の環境({environment})が無い — `uv sync --frozen --group dev` で作る"
        _print_summary(
            tuple(TargetResult(t, Verdict.UNMEASURED, reason) for t in targets), 0.0, args.budget
        )
        return 0
    try:
        run = run_pytest(repo, targets, args.budget)
    except StopError as exc:
        print(f"変えた所の検: 止める — {exc}", file=sys.stderr)
        return 2
    results = tuple(classify(t, run, args.budget) for t in targets)
    _print_summary(results, run.seconds, args.budget)
    return 1 if any(r.verdict is Verdict.RED for r in results) else 0


def _main_as_pytest(pytest_args: list[str]) -> int:
    """作業木の環境の中の口: この module を plugin として渡して pytest を走らせる(PYTHONPATH を継ぎ足さない)。"""
    # この口だけが作業木の環境の中で走る(入口の側は stdlib 単独なので module の頭では import しない)。
    import pytest

    return int(pytest.main(pytest_args, plugins=[sys.modules[__name__]]))


if __name__ == "__main__":
    sys.exit(_main_as_pytest(sys.argv[2:]) if sys.argv[1:2] == [PYTEST_MODE] else main())
