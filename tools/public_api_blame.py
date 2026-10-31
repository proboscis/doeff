"""表に書かずに足された公開名を、足した commit と card に引いて報告の行にする(日次の全体検証の後段)。

決まり: tests/test_public_api_known_violations.py は、merge の列も commit の時の hook も回さない。だから表を直さずに公開名を足した
commit は main に入り、日次の全体検証で初めて failing になる。この道具は、その failing の名を 1 行ずつ

    public_api_blame: <package> <module>.<名> <足した commit の頭 12 字> <card か依頼の id> <題>

として標準出力へ出し、日次の task が後段の報告(agora-controllers の AfterReport)として集めて赤の card に載せる。足した commit は
`git log -S<名> --reverse` の最初の 1 件(名を最初に足した commit)。照合は test と同じ関数(unlisted_names)を使う。
最後に、後段が最後まで走った印として `public_api_blame: 数え <N> 個` を出す(報告の行が 0 の回を「直った」と読むため)。
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
REPORT_PREFIX = "public_api_blame: 足した "
COUNT_PREFIX = "public_api_blame: 数え "
_ID_RE = re.compile(r"\b(ki-[0-9a-f]{12}|lt-[0-9A-Z]{26})")


def _load_test_module() -> ModuleType:
    """表の照合の関数を test の file から読むため(tests/ は package ではない)。"""
    spec = importlib.util.spec_from_file_location(
        "test_public_api_known_violations", ROOT / "tests" / "test_public_api_known_violations.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def first_commit_adding(name: str, package_dir: str, repo: Path = ROOT) -> tuple[str, str, str]:
    """名を最初に足した commit の (頭 12 字, 本文から引いた card か依頼の id, 題)。見つからなければ空の組。"""
    out = subprocess.run(
        ["git", "log", f"-S{name}", "--reverse", "--format=%H%x1f%s%x1f%b%x1e", "--", f"packages/{package_dir}"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    first = out.split("\x1e", 1)[0].strip()
    if not first:
        return ("", "", "")
    sha, subject, body = (first.split("\x1f") + ["", ""])[:3]
    found = _ID_RE.search(subject + "\n" + body)
    return (sha[:12], found.group(1) if found else "", subject)


def _report_line(package_dir: str, module: str, name: str, repo: Path) -> str:
    """名 1 つの報告の行を作るため: 足した commit と id を引いて、頭と同じ綴りで 1 行にする。"""
    sha, card, subject = first_commit_adding(name, package_dir, repo)
    return f"{REPORT_PREFIX}{package_dir} {module}.{name} {sha or '(commit 不明)'} {card or '(id 無し)'} {subject}"


def report_lines(repo: Path = ROOT) -> list[str]:
    """全 package の、表に書かずに足された外れの報告の行を作るため(照合は test の unlisted_names と同じ 1 点)。"""
    test = _load_test_module()
    table = test._read_table()
    allowed = test._read_allowed()
    return [
        _report_line(package["dir"], module, name, repo)
        for package in test.tool.inventory(repo, None, jobs=4, timeout=test._PROBE_SECONDS)
        for module, name in test.unlisted_names(package, table, allowed)
    ]


def main() -> int:
    """報告の行と、後段が最後まで走った印の数えの行を出すため。"""
    lines = report_lines()
    for line in lines:
        print(line)
    print(f"{COUNT_PREFIX}{len(lines)} 個")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
