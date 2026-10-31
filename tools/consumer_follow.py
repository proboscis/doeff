"""doeff を読む repo(消費者)の追従の針を、測った doeff の木の環境で回して、赤を報告の行にする(日次の全体検証の後段)。

決まり: doeff の公開名を消す・移す変更は、doeff の検証が緑でも消費者を壊し得る。
消費者の検証は消費者が固定した doeff の版で回るので、doeff の本線の変更で壊れた事は、消費者が版を上げるまで見えない。この道具は
消費者の針(PROBE — 消費者の docs/adr の .hy が import する doeff_adr の名が在るかの検)を、この道具を走らせた python(測った doeff の木の
環境)で回し、赤の検を 1 行ずつ

    consumer_follow: 赤 <消費者> <消費者の頭 12 字> <検の id> — <訳の 1 行目>

として標準出力へ出す。日次の task が後段の報告(agora-controllers の AfterReport)として集めて赤の card に載せる。

消費者は表に書かず導出する: 探す所(家の dotfiles と、家の repos の下の各 repo)のうち、根か 1 段下の pyproject.toml が doeff の配布物
(doeff・doeff-*)に依存する repo。針を持つ消費者だけを回す。消費者の conftest は消費者の環境の依存を読むので読まない(--noconftest)—
消費者の conftest の fixture を求める検は「測らない検」に数え、赤にしない。最後に、後段が最後まで走った印として `consumer_follow: 数え …` を
出す(報告の行が 0 の回を「直った」と読むため)。針を持つ消費者が 1 つも無い回は、測りが消えた事を赤の行にする(緑と読ませない)。
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tomllib

REPORT_PREFIX = "consumer_follow: 赤 "
COUNT_PREFIX = "consumer_follow: 数え "
PROBE = Path("docs/adr/test_doeff_adr_api_follows.py")
# 針 1 回の上限。実測 = 冷えた環境の dotfiles の針 1 回 172 秒(2026-10-30)— その約 7 倍。
PROBE_TIMEOUT_S = 1200
_MESSAGE_MAX = 400
_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_MISSING_FIXTURE_RE = re.compile(r"fixture '[^']+' not found")
CaseKind = Literal["red", "measured", "unmeasured"]


@dataclass(frozen=True)
class ProbeResult:
    """消費者 1 つの針の結末(red = 赤の検の (id, 訳) の列・measured = 測った検の数・unmeasured = 測らない検の数)。"""

    red: tuple[tuple[str, str], ...]
    measured: int
    unmeasured: int


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_names(pyproject: Path) -> set[str]:
    """pyproject.toml の依存(project の dependencies・optional-dependencies と dependency-groups)の配布物の名の集合。"""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    project = data.get("project", {})
    groups = (project.get("dependencies", []), *project.get("optional-dependencies", {}).values(),
              *data.get("dependency-groups", {}).values())  # fmt: skip
    found = (
        _REQUIREMENT_NAME_RE.match(spec)
        for group in groups
        for spec in group
        if isinstance(spec, str)
    )
    return {_normalized(match.group(1)) for match in found if match}


def _project_name(pyproject: Path) -> str:
    try:
        return _normalized(
            str(
                tomllib.loads(pyproject.read_text(encoding="utf-8"))
                .get("project", {})
                .get("name", "")
            )
        )
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def depends_on_doeff(repo: Path) -> bool:
    """repo の根か 1 段下の pyproject.toml が doeff の配布物に依存するか。doeff 自身(根の project の名が doeff)は消費者でない。"""
    if _project_name(repo / "pyproject.toml") == "doeff":
        return False
    return any(
        name == "doeff" or name.startswith("doeff-")
        for pyproject in (repo / "pyproject.toml", *sorted(repo.glob("*/pyproject.toml")))
        for name in _requirement_names(pyproject)
    )


def candidate_roots(home: Path) -> list[Path]:
    """消費者を探す所: 家の dotfiles と、家の repos の下の各 repo(git の checkout だけ・同じ実体は 1 度)。"""
    repos = home / "repos"
    found = [home / "dotfiles", *(sorted(repos.iterdir()) if repos.is_dir() else [])]
    checkouts = [root for root in found if (root / ".git").exists()]
    return [
        root
        for i, root in enumerate(checkouts)
        if root.resolve() not in {r.resolve() for r in checkouts[:i]}
    ]


def consumers(roots: list[Path]) -> list[Path]:
    return [root for root in roots if depends_on_doeff(root)]


def _case_outcome(case: ET.Element) -> tuple[CaseKind, str, str]:
    """junit の testcase 1 つを (種類, 検の名, 訳) にする。fixture の欠けの error は、消費者の conftest の fixture を求める検 —
    消費者の環境でしか測れないので「測らない」。"""
    name = case.get("name", "")
    failure = case.find("failure")
    found = failure if failure is not None else case.find("error")
    if found is None:
        return ("unmeasured" if case.find("skipped") is not None else "measured", name, "")
    message = found.get("message", "")
    text = found.text or ""
    if failure is None and _MISSING_FIXTURE_RE.search(message + "\n" + text):
        return ("unmeasured", name, "")
    # 集めの失敗の message は「collection failure」だけなので、訳は本文の最後の例外の行(pytest の `E   ` の行)から取る
    raised = [line.removeprefix("E").strip() for line in text.splitlines() if line.startswith("E ")]
    return (
        "red",
        name,
        _first_line(raised[-1] if message == "collection failure" and raised else message or text),
    )


def classify(junit_xml: str) -> ProbeResult:
    """pytest の junit の XML から、赤の検(failure と、fixture の欠けでない error)と、測った検・測らない検の数を読む。"""
    outcomes = [_case_outcome(case) for case in ET.fromstring(junit_xml).iter("testcase")]
    return ProbeResult(
        red=tuple((name, why) for kind, name, why in outcomes if kind == "red"),
        measured=sum(1 for kind, _name, _why in outcomes if kind != "unmeasured"),
        unmeasured=sum(1 for kind, _name, _why in outcomes if kind == "unmeasured"),
    )


def _first_line(text: str) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "(訳なし)")
    return line if len(line) <= _MESSAGE_MAX else line[: _MESSAGE_MAX - 1] + "…"


def run_probe(
    consumer: Path, python: str = sys.executable, timeout_s: float = PROBE_TIMEOUT_S
) -> ProbeResult:
    """消費者の針を、python の環境で 1 度回して結末を読む。走らない・終わらない・検が 1 本も無い時は、その事を赤の 1 件にする。"""
    probe = consumer / PROBE
    with tempfile.TemporaryDirectory(prefix="consumer-follow-") as tmp:
        junit = Path(tmp) / "probe.xml"
        command = [
            python, "-m", "pytest", str(probe), "-q", "--noconftest", "-p", "no:cacheprovider",
            "-o", "doeff_adr_wiring=off", "--rootdir", str(probe.parent), f"--junitxml={junit}",
        ]  # fmt: skip
        try:
            out = subprocess.run(
                command,
                cwd=probe.parent,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ProbeResult(
                red=(("(針)", f"針が {int(timeout_s)} 秒で終わらなかった"),),
                measured=0,
                unmeasured=0,
            )
        if not junit.exists():
            # 走らなかった訳は出力の終わりに在るので、終わりの側から最初の空でない行を取る
            tail = _first_line("\n".join(reversed((out.stdout + out.stderr).splitlines())))
            return ProbeResult(
                red=(("(針)", f"針が走らなかった(pytest の終了 {out.returncode})— {tail}"),),
                measured=0,
                unmeasured=0,
            )
        result = classify(junit.read_text(encoding="utf-8"))
    if not result.red and result.measured == 0:
        return ProbeResult(
            red=(("(針)", "針の検が 1 本も測れなかった"),), measured=0, unmeasured=result.unmeasured
        )
    return result


def _head(repo: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=False
    ).stdout
    return out.strip()[:12] or "(頭不明)"


def report_lines(home: Path, python: str = sys.executable) -> list[str]:
    """消費者ごとの赤の報告の行と、最後の数えの行を作るため。"""
    roots = candidate_roots(home)
    found = consumers(roots)
    probed = [consumer for consumer in found if (consumer / PROBE).is_file()]
    results = [(consumer, _head(consumer), run_probe(consumer, python)) for consumer in probed]
    red = [
        f"{REPORT_PREFIX}{consumer.name} {head} {PROBE.as_posix()}::{name} — {why}"
        for consumer, head, result in results
        for name, why in result.red
    ]
    places = "・".join(str(root) for root in roots) or "(探す所なし)"
    absent = (
        []
        if probed
        else [
            f"{REPORT_PREFIX}(消費者なし) - {PROBE.as_posix()} — 針を持つ消費者が見つからない(探した所 = {places})"
        ]
    )
    lines = [*red, *absent]
    count = (
        f"{COUNT_PREFIX}赤 {len(lines)} 行(消費者 {len(found)} 個・針のある {len(probed)} 個・"
        f"測った検 {sum(r.measured for _c, _h, r in results)} 本・測らない検 {sum(r.unmeasured for _c, _h, r in results)} 本)"
    )
    return [*lines, count]


def main() -> int:
    """報告の行と、後段が最後まで走った印の数えの行を出すため。後段は結末を変えないので、いつも 0 で終わる。"""
    for line in report_lines(Path.home()):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
