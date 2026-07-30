"""daemon under test の解決 — e2e 群の単一実装(issue #575 M2)。

R7 の spawn 解決順(ADR-DOE-AGENTS-004)をミラーする:
DOEFF_AGENTD_BIN(明示 seam、契約語彙)→ 実行中 interpreter 隣接の
doeff-sessionhost console script(venv install — stale PATH tool #556 への
免疫)→ PATH → 解決不能は loud AssertionError(silent fallback なし)。

退役 Rust crate のビルド(cargo)経路は存在しない
(retired-impl-lives-only-in-git-history law、rollback = git tag
agentd-rust-final)。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def resolve_sessionhost_bin() -> Path:
    if env_bin := os.environ.get("DOEFF_AGENTD_BIN"):
        return Path(env_bin)
    sibling = Path(sys.executable).parent / "doeff-sessionhost"
    if sibling.exists() and os.access(sibling, os.X_OK):
        return sibling
    if path_bin := shutil.which("doeff-sessionhost"):
        return Path(path_bin)
    raise AssertionError(
        "doeff-sessionhost console script not found (interpreter-adjacent or"
        " PATH) — run `make sync`, or set DOEFF_AGENTD_BIN"
    )
