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
   import の逆依存では選べない — だから固定の組で先頭に置く。表は package(uv の workspace の member)ごとに、組か
   「組の要らない理由」(`reason` の行)のどちらかを持つ。どちらも無い package は `table_gaps` が名指し、日次と表の
   repo 全体の行で走る検(tests/test_contract_table_follows_packages.py)が赤にする(agora-redesign #2658)。
3. 変えた検の file そのもの(検の file かは pytest の集め手と同じ名の規則 — root の ini の python_files・
   doeff_hy_test_files・doeff_adr_hy_files と doeff-adr の DEFAULT_FILE_PATTERNS)。
4. 逆依存の検 = 変えた file を直接・間接に import(Hy は require も)する検の file を、doeff-linter の
   `--affected-tests`(2 便目)に問い、距離の近い順(同じ距離は path の順)で後ろに足す。検の file の名の規則は 3 と
   同じ値を `--test-pattern` で渡し、答えも 3 と同じ判定で絞る。直接の使い手が多すぎる module(`doeff-hy.macros`・
   `doeff/__init__.py` の形)は linter が通り抜けず名を返すので「逆依存が広すぎて辿らなかった」と名指す。linter が無い・
   答えが読めない時は逆依存を足さずに「逆依存を測れなかった」と名指す(黙って空にしない・赤とは分ける)。
5. 契約の組を先頭に 1 回の pytest(`-m "not e2e"`)にまとめ、合計の壁時計の上限(既定 60 秒)で打ち切る。

出力は 走った(緑)・赤・未測 の 3 つ。終わらなかった file・集めた検が 0 本の file・venv の無い作業木は「未測」と
名指し、赤と分ける。rc は 赤が在れば 1・表や入力が読めなければ 2・それ以外 0(未測は登記を止めない — 名指すだけ)。

pytest の結果は、この同じ file の ReportPlugin を pytest の plugin として渡して行ごとの JSON で受け取る
— 打ち切った時も、そこまでに終わった検の結末が残る。stdlib 単独(呼び口は `uv run --script`・機体の python は撃たない)。
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
from collections.abc import Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol

import tomllib

#: uv の環境の置き場を指す環境変数(入口が 1 度だけ読む — RunEnvironment)。
UV_PROJECT_ENVIRONMENT = "UV_PROJECT_ENVIRONMENT"
#: pytest の子の process に渡さない環境変数(外の `uv run --script` の環境を渡さない)。
CHILD_DROPPED_ENV = frozenset({"VIRTUAL_ENV"})
#: pytest の既定の python_files(root の ini が書けば、そちらを読む)。
PYTEST_DEFAULT_PYTHON_FILES = ("test_*.py", "*_test.py")
#: doeff-adr の executable ADR の既定の pattern の定義元(ここを ast で読む — 2 つ目の一覧を書かない)。
ADR_PLUGIN_SOURCE = "packages/doeff-adr/src/doeff_adr/pytest_plugin.py"
ADR_PATTERNS_NAME = "DEFAULT_FILE_PATTERNS"
#: uv の project の環境の既定の置き場(UV_PROJECT_ENVIRONMENT が無い時)。無ければ何も測れない — 未測と名指す
#: (`uv run --no-sync` は環境が無いと空の環境を作ってしまうので、走らせる前に確かめる)。
DEFAULT_PROJECT_ENVIRONMENT = ".venv"
#: pytest の中で、この file の ReportPlugin を渡して pytest を走らせる口(入口が `uv run --project` の環境から呼ぶ・
#: 次の引数は結末を書く pipe の書き口の番号)。
PYTEST_MODE = "--as-pytest"
DEFAULT_BUDGET_SECONDS = 60.0
#: 上限の後、SIGTERM から SIGKILL までの猶予の秒。
KILL_GRACE_SECONDS = 3.0
#: 表の行の鍵: 契約の組(接頭辞と検の列)か、組の要らない package の除外(接頭辞と理由)のどちらか。
CONTRACT_ROW_KEYS = frozenset({"prefix", "tests"})
EXEMPTION_ROW_KEYS = frozenset({"prefix", "reason"})
#: pytest の rc のうち、検の結末ではなく道具の誤り(3 = 内部の誤り・4 = 命令行の誤り)— 未測に畳まず止める。
PYTEST_TOOL_FAILURES = frozenset({3, 4})
#: 逆依存を問う linter の既定の命令(PATH の上の doeff-linter — `make lint-doeff` と同じ入れ方)。
DEFAULT_LINTER = "doeff-linter"
#: linter の答えを待つ上限の秒(doeff で cache が温まって約 0.1 秒・冷えて約 0.2 秒 — 60 秒の検の予算とは別に数える)。
LINTER_TIMEOUT_SECONDS = 30.0


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
class ContractExemption:
    """表の 1 行: 契約の組の要らない package(接頭辞 = その package の dir)と、要らない理由(空でない文)。
    黙って外さないための行 — 理由の無い除外・package でない接頭辞の除外は読む時点で止める。"""

    prefix: str
    reason: str


@dataclass(frozen=True)
class ContractTable:
    rows: tuple[ContractRow, ...]
    exemptions: tuple[ContractExemption, ...]


@dataclass(frozen=True)
class PackageUnits:
    """契約の表が覆う単位 — root の `[tool.uv.workspace]` の member の dir(`packages/<名>/` の形の接頭辞・名の順)。

    表の package ごとの行の接頭辞は、どれも workspace の member の dir ちょうど(公開の形・stub・自分の pyproject を
    持つ単位)なので、同じ単位で数える。workspace の宣言を読み、2 つ目の一覧を書かない。"""

    prefixes: tuple[str, ...]


@dataclass(frozen=True)
class TableGaps:
    """表と木のずれ — 契約の組も理由つきの除外も無い package の接頭辞(名の順)。"""

    uncovered: tuple[str, ...]


TableRow = ContractRow | ContractExemption


@dataclass(frozen=True)
class WorkspaceGlobs:
    """`[tool.uv.workspace]` の glob の列 1 つ(members か exclude)。"""

    globs: tuple[str, ...]


def _test_path(test: str) -> str:
    """検の指定(`path` か `path::名`)の path の部分。"""
    return test.split("::", 1)[0]


def _workspace_globs(workspace: dict[str, object], key: str) -> WorkspaceGlobs:
    """`[tool.uv.workspace]` の glob の列の鍵を 1 つ読む(無い鍵は空の列 — uv と同じ意味・文字列の列でなければ止める)。"""
    match workspace.get(key, []):
        case list() as values if all(isinstance(v, str) for v in values):
            return WorkspaceGlobs(globs=tuple(values))
        case other:
            raise StopError(f"[tool.uv.workspace] の {key} が文字列の列でない: {other!r}")


def workspace_packages(repo: Path) -> PackageUnits:
    """root の pyproject.toml の `[tool.uv.workspace]` の members の glob に当たる dir から exclude に当たる dir を除き、
    pyproject.toml を持つ物(uv が member と読む物)を package の単位にする。workspace の宣言の無い repo は member が
    無い(uv と同じ意味)。pyproject.toml の無い dir(退役した crate の追跡外の残り)は package ではない。"""
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    match config.get("tool", {}).get("uv", {}).get("workspace"):
        case None:
            return PackageUnits(prefixes=())
        case dict() as workspace:
            pass
        case other:
            raise StopError(f"[tool.uv.workspace] が表でない: {other!r}")
    excluded = {
        path
        for pattern in _workspace_globs(workspace, "exclude").globs
        for path in repo.glob(pattern)
    }
    members = {
        path
        for pattern in _workspace_globs(workspace, "members").globs
        for path in repo.glob(pattern)
        if path.is_dir() and path not in excluded and (path / "pyproject.toml").is_file()
    }
    return PackageUnits(
        prefixes=tuple(sorted(f"{path.relative_to(repo).as_posix()}/" for path in members))
    )


def _parse_exemption(where: str, raw: dict[str, object], units: PackageUnits) -> ContractExemption:
    """除外の行(prefix と reason)を読む — 理由が空・接頭辞が package の単位でない行は止める。"""
    match raw["prefix"], raw["reason"]:
        case str() as prefix, str() as reason if reason.strip():
            pass
        case _:
            raise StopError(
                f"{where}: 除外の行は prefix が文字列・reason が空でない理由の文: {raw!r}"
            )
    if prefix not in units.prefixes:
        raise StopError(
            f"{where}: 除外の接頭辞 {prefix!r} が package(uv の workspace の member の dir)でない"
            f" — 除外は package ごとに書く(package: {list(units.prefixes)})"
        )
    return ContractExemption(prefix=prefix, reason=reason.strip())


def _parse_row(index: int, raw: object, repo: Path, units: PackageUnits) -> TableRow:
    where = f"[[tool.doeff.contract-tests]] の {index + 1} 行目"
    match raw:
        case dict() if set(raw) == CONTRACT_ROW_KEYS:
            pass
        case dict() if set(raw) == EXEMPTION_ROW_KEYS:
            return _parse_exemption(where, raw, units)
        case dict():
            raise StopError(
                f"{where}: 鍵は {sorted(CONTRACT_ROW_KEYS)}(契約の組)か {sorted(EXEMPTION_ROW_KEYS)}"
                f"(組の要らない package の理由つきの除外)だけ(実際 {sorted(raw)})"
            )
        case _:
            raise StopError(f"{where}: 表(prefix と tests か、prefix と reason)でない: {raw!r}")
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
    """root の pyproject.toml の `[[tool.doeff.contract-tests]]` を読む(読み手はこの 1 か所)。組の行と除外の行の接頭辞は
    重ねない(同じ package に組と除外の両方を書くと、どちらが本当かを黙って決めることになる)。"""
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
    units = workspace_packages(repo)
    parsed = tuple(_parse_row(i, raw, repo, units) for i, raw in enumerate(raw_rows))
    prefixes = [row.prefix for row in parsed]
    duplicated = sorted({p for p in prefixes if prefixes.count(p) > 1})
    if duplicated:
        raise StopError(f"[[tool.doeff.contract-tests]] に同じ接頭辞の行が 2 つ以上: {duplicated}")
    return ContractTable(
        rows=tuple(row for row in parsed if isinstance(row, ContractRow)),
        exemptions=tuple(row for row in parsed if isinstance(row, ContractExemption)),
    )


def table_gaps(table: ContractTable, units: PackageUnits) -> TableGaps:
    """package の単位のうち、表に契約の組(接頭辞がその package の dir ちょうどの行)も理由つきの除外も無い物。
    repo 全体の行("")や package の中の dir の行は、その package の組に数えない — package ごとに「何がその package の
    公開の形を確かめるか」を決めた行だけを数える。純粋な関数(呼び手が読んだ表と単位を渡す)。"""
    named = {row.prefix for row in table.rows} | {row.prefix for row in table.exemptions}
    return TableGaps(uncovered=tuple(p for p in units.prefixes if p not in named))


# ---------------------------------------------------------------------------
# 検の file の名の規則(pytest の集め手と同じ値を読む)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestFilePatterns:
    """検の file の名の規則の列(pytest の集め手と同じ値 — 逆依存を問う linter にも同じ値を渡す)。"""

    globs: tuple[str, ...]


def _adr_default_patterns(repo: Path) -> TestFilePatterns:
    """executable ADR の既定の名の規則を、定義元の doeff-adr の source から読む(2 つ目の一覧を書かないため)。"""
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
            return TestFilePatterns(globs=patterns)
        case _:
            raise StopError(f"{ADR_PATTERNS_NAME} が文字列の tuple でない: {patterns!r}")


def _ini_strings(ini: dict[str, object], key: str, default: tuple[str, ...]) -> TestFilePatterns:
    """root の ini の名の規則の鍵を 1 つ読む(文字列の列でなければ既定に倒さず止める)。"""
    match ini.get(key, default):
        case list() | tuple() as values if all(isinstance(v, str) for v in values):
            return TestFilePatterns(globs=tuple(values))
        case other:
            raise StopError(f"[tool.pytest.ini_options] の {key} が文字列の列でない: {other!r}")


def collector_patterns(repo: Path) -> TestFilePatterns:
    """pytest が集める 3 つの経路の名の規則(Python・Hy の検・executable ADR)。"""
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    ini = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    parts = (
        _ini_strings(ini, "python_files", PYTEST_DEFAULT_PYTHON_FILES),
        _ini_strings(ini, "doeff_hy_test_files", ()),
        _adr_default_patterns(repo),
        _ini_strings(ini, "doeff_adr_hy_files", ()),
    )
    return TestFilePatterns(globs=tuple(glob for part in parts for glob in part.globs))


def is_test_file(path: str, patterns: TestFilePatterns) -> bool:
    """pytest の照合と同じ: `/` を含まない pattern は file の名に、含む pattern は repo の根からの path に当てる。"""
    name = path.rsplit("/", 1)[-1]
    return "fixtures" not in path.split("/") and any(
        fnmatch.fnmatchcase(path if "/" in p else name, p) for p in patterns.globs
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
    REVERSE = "逆依存"


@dataclass(frozen=True)
class Target:
    """1 回の pytest に渡す引数 1 つと、選ばれた理由。"""

    arg: str
    origin: Origin
    why: str


@dataclass(frozen=True)
class Selection:
    """1 回の pytest に渡す引数の並び(先頭から走らせ、上限で打ち切られた後ろは未測になる)。"""

    targets: tuple[Target, ...]


def _unique(targets: tuple[Target, ...]) -> Selection:
    """同じ引数を 2 度渡さない(最初に選ばれた理由を残す)。"""
    return Selection(
        targets=tuple(t for i, t in enumerate(targets) if t.arg not in {u.arg for u in targets[:i]})
    )


def select_targets(
    table: ContractTable, changes: ChangeSet, patterns: TestFilePatterns, repo: Path
) -> Selection:
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
    return _unique((*contract, *changed))


# ---------------------------------------------------------------------------
# 逆依存(doeff-linter の `--affected-tests`)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AffectedTest:
    """linter が選んだ検の file 1 つ(変えた file からの辺の数と、最短の道)。"""

    path: str
    distance: int
    via: tuple[str, ...]


@dataclass(frozen=True)
class Hub:
    """直接の使い手が多すぎて linter が通り抜けなかった module。"""

    module: str
    root: str
    dependents: int


@dataclass(frozen=True)
class Affected:
    """linter の答え(測れた)。"""

    tests: tuple[AffectedTest, ...]
    hubs: tuple[Hub, ...]
    unreadable: tuple[str, ...]


@dataclass(frozen=True)
class AffectedUnmeasured:
    """逆依存を測れなかった(linter が無い・止まった・答えが読めない)— 理由を名指す。"""

    reason: str


AffectedAnswer = Affected | AffectedUnmeasured


def _parse_affected_test(raw: object) -> AffectedTest:
    """答えの tests の 1 つを読む(形が違えば ValueError — 答え全体を「測れなかった」にするため)。"""
    match raw:
        case {"path": str() as path, "distance": int() as distance, "via": list() as via} if all(
            isinstance(step, str) for step in via
        ):
            return AffectedTest(path=path, distance=distance, via=tuple(via))
        case _:
            raise ValueError(f"tests の 1 つの形が違う: {raw!r}")


def _parse_hub(raw: object) -> Hub:
    """答えの hubs の 1 つを読む(広すぎて辿らなかった module を要約で名指すため)。"""
    match raw:
        case {"module": str() as module, "root": str() as root, "dependents": int() as dependents}:
            return Hub(module=module, root=root, dependents=dependents)
        case _:
            raise ValueError(f"hubs の 1 つの形が違う: {raw!r}")


def parse_affected(text: str) -> AffectedAnswer:
    """`--affected-tests` の JSON を読む — 形が違えば既定に倒さず「測れなかった」にする。"""
    try:
        match json.loads(text):
            case {
                "tests": list() as tests,
                "hubs": list() as hubs,
                "unreadable": list() as unreadable,
            }:
                return Affected(
                    tests=tuple(_parse_affected_test(t) for t in tests),
                    hubs=tuple(_parse_hub(h) for h in hubs),
                    unreadable=tuple(_parse_unreadable(u) for u in unreadable),
                )
            case other:
                return AffectedUnmeasured(f"linter の答えの形が違う: {str(other)[:200]}")
    except (json.JSONDecodeError, ValueError) as exc:
        return AffectedUnmeasured(f"linter の答えが読めない: {exc}")


def _parse_unreadable(raw: object) -> str:
    """依存を読めなかった file の path(選び漏れの在りうる所として要約で数える)。"""
    match raw:
        case {"path": str() as path}:
            return path
        case _:
            raise ValueError(f"unreadable の 1 つの形が違う: {raw!r}")


def ask_affected(
    linter: str, repo: Path, changes: ChangeSet, patterns: TestFilePatterns
) -> AffectedAnswer:
    """linter の `--affected-tests` に、変えた file と検の file の名の規則(3 と同じ値)を渡して問う(実 I/O はここだけ)。"""
    command = [
        linter,
        "--root",
        str(repo),
        "--affected-tests",
        *changes.files,
        *(arg for pattern in patterns.globs for arg in ("--test-pattern", pattern)),
    ]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, timeout=LINTER_TIMEOUT_SECONDS, check=False
        )
    except FileNotFoundError:
        return AffectedUnmeasured(
            f"linter {linter!r} が無い(PATH に doeff-linter を入れるか --linter で渡す)"
        )
    except subprocess.TimeoutExpired:
        return AffectedUnmeasured(f"linter が {LINTER_TIMEOUT_SECONDS:g} 秒の内に答えなかった")
    if proc.returncode != 0:
        last = (proc.stderr.strip().splitlines() or ["(stderr なし)"])[-1]
        return AffectedUnmeasured(f"linter が rc {proc.returncode} で止まった — {last[:300]}")
    return parse_affected(proc.stdout)


def extend_with_reverse(
    selection: Selection, answer: AffectedAnswer, patterns: TestFilePatterns, repo: Path
) -> Selection:
    """選んだ組の後ろに、逆依存の検を距離の近い順(同じ距離は path の順)で足す — 既に在る引数は足さない。
    検の file かは 3 と同じ判定で絞る。測れなかった答えは何も足さない(要約で名指す)。"""
    match answer:
        case AffectedUnmeasured():
            return selection
        case Affected(tests=tests):
            pass
    reverse = tuple(
        Target(
            arg=test.path,
            origin=Origin.REVERSE,
            why=f"距離 {test.distance}: {' → '.join(test.via)}",
        )
        for test in sorted(tests, key=lambda t: (t.distance, t.path))
        if is_test_file(test.path, patterns) and (repo / test.path).is_file()
    )
    return _unique((*selection.targets, *reverse))


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


@dataclass(frozen=True)
class ReportPlugin:
    """pytest の中の口が pytest に渡す plugin — 結末を、入口が命令行で渡した pipe の書き口(report_fd)へ 1 行の JSON
    ずつ書く(打ち切られても、そこまでの行が残るように 1 行ずつ閉じる)。書き口は子の process の入口
    (`_main_as_pytest`)が 1 度だけ読んだ値で、環境変数は読まない。"""

    report_fd: int

    def _write_event(self, payload: dict[str, object]) -> None:
        """結末 1 つを pipe へ書く(入口の側の `_parse_event` が読む形)。"""
        os.write(self.report_fd, (json.dumps(payload, ensure_ascii=False) + "\n").encode())

    def pytest_collection_finish(self, session: _Session) -> None:
        """どの検を集めたか(-m で外した後)を残す — 終わらなかった検を名指すための母数。"""
        self._write_event({"kind": "collected", "nodeids": [item.nodeid for item in session.items]})

    def pytest_runtest_logreport(self, report: _Report) -> None:
        """検 1 本の段(setup・call・teardown)の結末を残す — teardown が来た検を「終わった」と数える。"""
        self._write_event(
            {
                "kind": "report",
                "nodeid": report.nodeid,
                "when": report.when,
                "outcome": report.outcome,
            }
        )

    def pytest_collectreport(self, report: _Report) -> None:
        """集める時に落ちた file を残す — 赤として名指す。"""
        if report.failed:
            self._write_event({"kind": "collect_error", "nodeid": report.nodeid})


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


@dataclass(frozen=True)
class Unset:
    """環境変数が無い(空の値も uv と同じく無いと読む)。"""

    name: str


@dataclass(frozen=True)
class Setting:
    """環境変数の空でない値。"""

    name: str
    value: str


EnvironmentSetting = Setting | Unset


@dataclass(frozen=True)
class EnvironmentEntry:
    """子の process に渡す環境変数 1 つ。"""

    name: str
    value: str


@dataclass(frozen=True)
class RunEnvironment:
    """入口が 1 度だけ読んだ環境 — 中の関数は環境変数を読まず、この値を受け取る。

    uv_project_environment = uv の環境の置き場の指定(UV_PROJECT_ENVIRONMENT)。
    child = pytest の子の process に渡す環境(外の `uv run --script` の VIRTUAL_ENV は除く)。
    """

    uv_project_environment: EnvironmentSetting
    child: tuple[EnvironmentEntry, ...]


def _setting(environ: Mapping[str, str], name: str) -> EnvironmentSetting:
    """環境変数 1 つを、在る(空でない値)か無いかの型で読む。"""
    match environ.get(name, ""):
        case "":
            return Unset(name=name)
        case value:
            return Setting(name=name, value=value)


def read_run_environment(environ: Mapping[str, str]) -> RunEnvironment:
    """入口の 1 か所で、process の環境(呼び手が渡す)から要る値を読む。"""
    return RunEnvironment(
        uv_project_environment=_setting(environ, UV_PROJECT_ENVIRONMENT),
        child=tuple(
            EnvironmentEntry(name=k, value=v)
            for k, v in sorted(environ.items())
            if k not in CHILD_DROPPED_ENV
        ),
    )


def project_environment(repo: Path, environment: RunEnvironment) -> Path:
    """uv が `uv run --project <repo>` で使う環境の置き場(UV_PROJECT_ENVIRONMENT か `<repo>/.venv`)。"""
    match environment.uv_project_environment:
        case Setting(value=value):
            return repo / value
        case Unset():
            return repo / DEFAULT_PROJECT_ENVIRONMENT


def run_pytest(
    repo: Path, targets: tuple[Target, ...], budget: float, environment: RunEnvironment
) -> PytestRun:
    """1 回の pytest を作業木の uv の環境で壁時計の上限つきで走らせ、plugin が pipe へ書いた結末を受け取る
    (file には残さない)。pipe の書き口の番号は子の命令行で渡す。"""
    read_fd, write_fd = os.pipe()
    env = {entry.name: entry.value for entry in environment.child}
    command = [
        "uv",
        "run",
        "--no-sync",
        "--project",
        str(repo),
        "python",
        str(Path(__file__).resolve()),
        PYTEST_MODE,
        str(write_fd),
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


def _print_reverse(answer: AffectedAnswer, added: int) -> None:
    """逆依存の測り方の結末を名指す — 測れなかった時・広すぎて辿らなかった module を、赤と混ぜずに出す。"""
    match answer:
        case AffectedUnmeasured(reason=reason):
            print(f"逆依存を測れなかった — {reason}(赤ではない・契約の組と変えた検だけを選んだ)")
        case Affected(tests=tests, hubs=hubs, unreadable=unreadable):
            print(
                f"逆依存: 足した検 {added} 本(linter の答え {len(tests)} 本・重なりと検の file でない物を除く)"
            )
            for hub in hubs:
                print(
                    f"  逆依存が広すぎて辿らなかった module: {hub.module}(根 {hub.root or '.'}・直接の使い手"
                    f" {hub.dependents})— その先は日次の全体の検が測る"
                )
            if unreadable:
                print(
                    f"  依存を読めなかった file {len(unreadable)} 本(その先の選び漏れが在りうる):"
                    f" {', '.join(unreadable[:10])}"
                )


def _print_summary(
    results: tuple[TargetResult, ...], seconds: float, budget: float, reverse: AffectedAnswer
) -> None:
    """3 つ(走った・赤・未測)に分けた要約を出す — 未測は理由つきで名指し、赤と混ぜない。逆依存の測り方も名指す。"""
    print()
    print(f"== 変えた所の検の要約(所要 {seconds:.1f} 秒 / 上限 {budget:g} 秒)==")
    for verdict in Verdict:
        rows = [r for r in results if r.verdict is verdict]
        print(f"{verdict.value}: {len(rows)}")
        for r in rows:
            print(f"  {r.target.arg} — {r.detail}({r.target.origin.value}・{r.target.why})")
    _print_reverse(reverse, sum(1 for r in results if r.target.origin is Origin.REVERSE))


def main(environ: Mapping[str, str], argv: list[str] | None = None) -> int:
    """登記の前の入口: 変えた file → 契約の組 + 変えた検 + 逆依存の検 → 1 回の pytest → 3 つに分けた要約と rc。"""
    parser = argparse.ArgumentParser(
        description="変えた所の検 — 登記の前に、契約の検の組・変えた検・逆依存の検を 60 秒の上限で走らせる"
    )
    parser.add_argument(
        "--repo", type=Path, default=Path.cwd(), help="走らせる作業木(既定 = 今の dir)"
    )
    parser.add_argument("--base", help="分岐点(既定 = git merge-base HEAD origin/main)")
    parser.add_argument(
        "--budget", type=float, default=DEFAULT_BUDGET_SECONDS, help="合計の壁時計の上限の秒"
    )
    parser.add_argument(
        "--linter",
        default=DEFAULT_LINTER,
        help="逆依存を問う doeff-linter の命令(既定 = PATH の上の doeff-linter)",
    )
    args = parser.parse_args(argv)
    run_environment = read_run_environment(environ)
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
    reverse: AffectedAnswer = (
        ask_affected(args.linter, repo, changes, patterns)
        if changes.files
        else Affected(tests=(), hubs=(), unreadable=())
    )
    targets = extend_with_reverse(
        select_targets(table, changes, patterns, repo), reverse, patterns, repo
    ).targets
    if not targets:
        print("当たる契約の組も変えた検の file も逆依存の検も無い — 走らせる検は 0 本")
        _print_reverse(reverse, 0)
        return 0
    environment = project_environment(repo, run_environment)
    if not (environment / "pyvenv.cfg").is_file():
        reason = f"作業木に uv の環境({environment})が無い — `uv sync --frozen --group dev` で作る"
        _print_summary(
            tuple(TargetResult(t, Verdict.UNMEASURED, reason) for t in targets),
            0.0,
            args.budget,
            reverse,
        )
        return 0
    try:
        run = run_pytest(repo, targets, args.budget, run_environment)
    except StopError as exc:
        print(f"変えた所の検: 止める — {exc}", file=sys.stderr)
        return 2
    results = tuple(classify(t, run, args.budget) for t in targets)
    _print_summary(results, run.seconds, args.budget, reverse)
    return 1 if any(r.verdict is Verdict.RED for r in results) else 0


def _main_as_pytest(argv: list[str]) -> int:
    """作業木の環境の中の子の process の入口: 命令行の頭の pipe の書き口の番号を 1 度だけ読み、ReportPlugin を渡して
    pytest を走らせる(PYTHONPATH を継ぎ足さない・環境変数は読まない)。"""
    # この口だけが作業木の環境の中で走る(入口の側は stdlib 単独なので module の頭では import しない)。
    import pytest

    match argv:
        case [fd, *pytest_args] if fd.isdigit():
            return int(pytest.main(pytest_args, plugins=[ReportPlugin(report_fd=int(fd))]))
        case _:
            print(f"{PYTEST_MODE} の次は結末を書く pipe の番号: {argv[:1]!r}", file=sys.stderr)
            return 2


if __name__ == "__main__":
    sys.exit(
        _main_as_pytest(sys.argv[2:])
        if sys.argv[1:2] == [PYTEST_MODE]
        else main(os.environ)  # 環境を読む入口の 1 か所(中へは RunEnvironment で渡す)
    )
