"""daemon under test の起動の argv の頭 — e2e 群の単一実装(issue #575 M2)。

ensure_agentd の起動(effect StartSessionHost の handler)をミラーする:
DOEFF_AGENTD_BIN(明示 seam、契約語彙)→ 無ければ実行中 interpreter で host の入口の module
(`python -m doeff_agents._sessionhost.host`)。CLI コマンドも PATH の探索も無い — 検の
interpreter がそのまま host を起こすので、PATH の古い道具(#556)が紛れ込む道が無い。

退役 Rust crate のビルド(cargo)経路は存在しない
(retired-impl-lives-only-in-git-history law、rollback = git tag
agentd-rust-final)。
"""

from __future__ import annotations

import sys

from runner_env import agentd_bin_setting

#: host の入口の module(python -m の的)。
SESSIONHOST_MODULE = "doeff_agents._sessionhost.host"


def sessionhost_command() -> tuple[str, ...]:
    """検が host を起こす argv の頭(この後ろに host の旗を並べる)。"""
    if env_bin := agentd_bin_setting():
        return (env_bin,)
    return (sys.executable, "-m", SESSIONHOST_MODULE)
