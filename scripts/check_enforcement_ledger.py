#!/usr/bin/env python3
"""enforcement 台帳の突合 — 勘定の単一の家(ADR-DOE-ENFORCE-001 R5 / R7)。

enforcement 資産(defadr ファイル数・.semgrep.yaml ルール数・ADR 内
deftest / defsemgrep / law 数)の実数を docs/adr/enforcement-ledger.json と
厳密一致で突合する。stdlib 単独(venv・依存不要)— git pre-commit hook
(scripts/git-hooks/pre-commit)からも既定 pytest
(tests/test_enforcement_ledger.py)からも、この同じ勘定を使う。
第 2 の定義点を作らない: regex が乖離した日から hook 緑 = verify 緑が
成立しなくなる。

modes:
  (既定)    working tree を勘定する
  --staged   git index(commit に入る断面)を勘定する — hook 用。
             working tree 突合は「台帳も直したが stage し忘れた」を素通しする。
  --root=P   repo root の明示(既定 = cwd の git toplevel)

exit code: 0 = 一致 / 1 = 不一致 / 2 = 実行環境の失敗(git が引けない等)。
"""

import json
import re
import subprocess
import sys
from pathlib import Path

LEDGER_PATH = "docs/adr/enforcement-ledger.json"
SEMGREP_PATH = ".semgrep.yaml"
ADR_GLOB = "docs/adr/defadr_*.hy"


def counts_from_texts(adr_texts: list[str], semgrep_text: str) -> dict:
    adr_text = "".join(adr_texts)
    return {
        "defadr_files": len(adr_texts),
        "semgrep_rules": len(re.findall(r"^  - id:", semgrep_text, re.MULTILINE)),
        "adr_deftest_enforcements": adr_text.count("(deftest "),
        "adr_defsemgrep_enforcements": adr_text.count("(defsemgrep "),
        "adr_laws": adr_text.count("(law "),
    }


def ledger_from_text(text: str) -> dict:
    return {k: v for k, v in json.loads(text).items() if not k.startswith("_")}


def worktree_counts(root: Path) -> dict:
    adr_files = sorted(root.glob(ADR_GLOB))
    return counts_from_texts(
        [p.read_text(encoding="utf-8") for p in adr_files],
        (root / SEMGREP_PATH).read_text(encoding="utf-8"),
    )


def worktree_ledger(root: Path) -> dict:
    return ledger_from_text((root / LEDGER_PATH).read_text(encoding="utf-8"))


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def staged_counts(root: Path) -> dict:
    names = sorted(
        n for n in _git(root, "ls-files", "--cached", "--", ADR_GLOB).splitlines() if n
    )
    return counts_from_texts(
        [_git(root, "show", f":{n}") for n in names],
        _git(root, "show", f":{SEMGREP_PATH}"),
    )


def staged_ledger(root: Path) -> dict:
    return ledger_from_text(_git(root, "show", f":{LEDGER_PATH}"))


def _mismatch_message(face: str, ledger: dict, actual: dict, root: Path) -> str:
    return (
        f"enforcement 台帳と実数({face})が不一致 — ADR-DOE-ENFORCE-001 R5/R7。\n"
        f"  台帳: {ledger}\n  実数: {actual}\n"
        f"減少 = 侵食(意図的なら台帳と ADR を同時更新)、増加 = 記帳漏れ(台帳を上げる)。\n"
        f"台帳と enforcement 資産は同じ commit で動かす(台帳の stage し忘れも不一致)。"
        f" 台帳: {root / LEDGER_PATH}"
    )


def main(argv: list[str]) -> int:
    staged = "--staged" in argv
    roots = [a.split("=", 1)[1] for a in argv if a.startswith("--root=")]
    try:
        if roots:
            root = Path(roots[-1]).resolve()
        else:
            root = Path(_git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
        if staged:
            face = "staged 断面"
            actual, ledger = staged_counts(root), staged_ledger(root)
        else:
            face = "working tree"
            actual, ledger = worktree_counts(root), worktree_ledger(root)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"check_enforcement_ledger: 突合を実行できない — {exc}", file=sys.stderr)
        return 2
    if actual == ledger:
        return 0
    print(_mismatch_message(face, ledger, actual, root), file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
