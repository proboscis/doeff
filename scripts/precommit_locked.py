"""doeff の素の commit の hook(scripts/git-hooks/pre-commit)が呼ぶ pre-commit を、repo の uv.lock が決める版に固定する入口
。

素の hook は作業木の `.venv` に依らない(make sync をしていない作業木・agora の担当の Pod の作業木でも走る)。pre-commit は dev の
依存なので、semgrep と同じく uv.lock の版を読み、uv の道具の置き場から `uv tool run --from pre-commit==<版> pre-commit` で呼ぶ
(版の読みは scripts/semgrep_locked.py の 1 か所)。

使い方: uv run --no-project --python <版> python scripts/precommit_locked.py <pre-commit の引数…>(repo の根で。pre-commit の答えと
終了の値をそのまま返す)。uv.lock に pre-commit が無ければ、理由を名指して 1 で止まる。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# 隣の module(semgrep_locked)は、この script の dir を置き場として明示で読む(PYTHONSAFEPATH=1 の下でも読める — hook_finding_baseline と同じ)。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from semgrep_locked import locked_command

PACKAGE: str = "pre-commit"


def main(argv: list[str]) -> int:
    """repo の根(今の dir)の uv.lock の版の pre-commit に引数を渡して置き換わる。"""
    command: list[str] = locked_command(Path.cwd(), PACKAGE)
    os.execvp(command[0], [*command, *argv])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
