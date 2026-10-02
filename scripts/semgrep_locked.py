"""doeff の commit の hook が呼ぶ semgrep を、repo の uv.lock が決める版に固定する入口(agora-redesign #2906)。

hook が探し道の semgrep や作業木の環境の semgrep を呼ぶと、機体の入れ方と作業木の状態で版がずれる(2026-10-02 zeus: 共有の
uv tool = 1.161.0・lock = 1.169.0。`.venv` の無い作業木では `uv run --no-sync --project <根>` が空の `.venv` をその場に作り、
探し道の物を呼んでいた — 基点の比べは版の違いで「測れない」と名乗って通り、何も止めていなかった)。ここは uv.lock の semgrep の
版を読み、uv の道具の置き場から `uv tool run --from semgrep==<版> semgrep` で呼ぶ。版は木の中の uv.lock だけで決まり、置き場は
機体で 1 つ(作業木が何本あっても 1 度だけ入る・作業木に `.venv` を作らない)。

使い方: uv run --no-project python scripts/semgrep_locked.py <semgrep の引数…>(repo の根で。semgrep の答えと終了の値をそのまま返す)。
uv.lock に semgrep が無ければ、理由を名指して 1 で止まる(探し道の物へ黙って戻らない)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import tomllib

LOCK_FILE: str = "uv.lock"
PACKAGE: str = "semgrep"


def locked_version(top: Path) -> str:
    """uv.lock が決める semgrep の版。lock が無い・semgrep が 1 つに決まらない時は理由を名指して止める(既定の版にしない)。"""
    lock: Path = top / LOCK_FILE
    if not lock.is_file():
        raise SystemExit(f"semgrep の版を決める {lock} が無い — 探し道の semgrep へは戻らない")
    parsed: dict[str, object] = tomllib.loads(lock.read_text(encoding="utf-8"))
    packages: object = parsed.get("package", [])
    versions: list[str] = (
        [str(entry["version"]) for entry in packages if isinstance(entry, dict) and entry.get("name") == PACKAGE]
        if isinstance(packages, list) else []
    )
    if len(versions) != 1:
        raise SystemExit(f"{lock} の semgrep の版が 1 つに決まらない({versions})— 探し道の semgrep へは戻らない")
    return versions[0]


def locked_command(top: Path) -> list[str]:
    """uv.lock の版の semgrep を、uv の道具の置き場から呼ぶ命令(作業木の `.venv` にも探し道にも依らない)。"""
    return ["uv", "tool", "run", "--from", f"{PACKAGE}=={locked_version(top)}", PACKAGE]


def main(argv: list[str]) -> int:
    """repo の根(今の dir)の uv.lock の版の semgrep に引数を渡して置き換わる。"""
    command: list[str] = locked_command(Path.cwd())
    os.execvp(command[0], [*command, *argv])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
