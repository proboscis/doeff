"""doeff の commit の hook が呼ぶ semgrep を、repo の uv.lock が決める版に固定する入口(agora-redesign #2906)。

hook が探し道の semgrep や作業木の環境の semgrep を呼ぶと、機体の入れ方と作業木の状態で版がずれる(2026-10-02 zeus: 共有の
uv tool = 1.161.0・lock = 1.169.0。`.venv` の無い作業木では `uv run --no-sync --project <根>` が空の `.venv` をその場に作り、
探し道の物を呼んでいた — 基点の比べは版の違いで「測れない」と名乗って通り、何も止めていなかった)。ここは uv.lock の semgrep の
版を読み、uv の道具の置き場から `uv tool run --from semgrep==<版> semgrep` で呼ぶ。版は木の中の uv.lock だけで決まり、置き場は
機体で 1 つ(作業木が何本あっても 1 度だけ入る・作業木に `.venv` を作らない)。

使い方: uv run --no-project --python <版> python scripts/semgrep_locked.py <semgrep の引数…>(repo の根で。semgrep の答えと終了の値をそのまま返す)。
uv.lock に semgrep が無ければ、理由を名指して 1 で止まる(探し道の物へ黙って戻らない)。素の commit の hook が呼ぶ pre-commit も同じ
読みで版を決める(scripts/precommit_locked.py・card ki-8c0bb496bfab)。

locked_version は doeff で uv.lock の版を読む 1 か所(card ki-33d2441b7e28): Hy の版で測る検(ADR-DOE-HY-008 の
locked-hy-version・packages/doeff-hy/tests/test_bytecode_guard.py)も、この file を path で読んで呼ぶ。hook は doeff の環境の外
(`uv run --no-project`)で走るので、読み手は標準の library だけで書く。読みを変えたら、uv.lock の Hy の版で測る検が root の
pyproject.toml の [[tool.doeff.contract-tests]] で選ばれる(tests/test_contract_table_follows_packages.py)。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import tomllib

LOCK_FILE: str = "uv.lock"
PACKAGE: str = "semgrep"


def locked_version(top: Path, package: str = PACKAGE) -> str:
    """木 top の uv.lock が決める package(既定は semgrep)の版。lock が無い・package の行が 1 つでない・行が index から入れる版でない
    (git・path など)時は理由を名指して止める(既定の版にしない)— 使い手はどれも `<package>==<版>` を index から入れるので、
    index の外の行の版の名で入れると lock と違う物を測る。"""
    lock: Path = top / LOCK_FILE
    if not lock.is_file():
        raise SystemExit(f"{package} の版を決める {lock} が無い — 既定の版や探し道の {package} へは戻らない")
    parsed: dict[str, object] = tomllib.loads(lock.read_text(encoding="utf-8"))
    packages: object = parsed.get("package", [])
    rows: list[dict[str, object]] = (
        [entry for entry in packages if isinstance(entry, dict) and entry.get("name") == package]
        if isinstance(packages, list) else []
    )
    if len(rows) != 1:
        versions: list[object] = [row.get("version") for row in rows]
        raise SystemExit(f"{lock} の {package} の版が 1 つに決まらない({versions})— 既定の版や探し道の {package} へは戻らない")
    source: object = rows[0].get("source")
    if not (isinstance(source, dict) and "registry" in source):
        raise SystemExit(f"{lock} の {package} が index から入れる版でない({source})— `{package}==<版>` で lock と同じ物を入れられない")
    return str(rows[0]["version"])


def locked_command(top: Path, package: str = PACKAGE) -> list[str]:
    """uv.lock の版の package(既定は semgrep)を、uv の道具の置き場から呼ぶ命令(作業木の `.venv` にも探し道にも依らない)。道具の python は、この
    script を走らせている python の版(hook の設定が `uv run --python` で宣言した版)— 渡さないと uv は木の .python-version(3.14t)を
    選び、semgrep の依存の ruamel-yaml-clib が組めない(card acp:kanban-issue:ki-a28a482132ab)。"""
    running: str = f"{sys.version_info.major}.{sys.version_info.minor}"
    return ["uv", "tool", "run", "--python", running, "--from", f"{package}=={locked_version(top, package)}", package]


def main(argv: list[str]) -> int:
    """repo の根(今の dir)の uv.lock の版の semgrep に引数を渡して置き換わる。"""
    command: list[str] = locked_command(Path.cwd())
    os.execvp(command[0], [*command, *argv])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
