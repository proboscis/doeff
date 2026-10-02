"""doeff-conductor の検の走らせ方を決める環境変数の値(agora-redesign #2894)。

E2E の検を走らせるか(CONDUCTOR_E2E)と OpenCode の server の URL(CONDUCTOR_OPENCODE_URL)は、
検を集める時点で pytest の外から渡す旗(README に書いてある走らせ方)なので、検の Program の
Ask で受ける入口が無い。この module は変数の素の値を返すだけで、旗の読み方の判断は
conftest.py に残す。DOEFF004 は packages/doeff-conductor/architecture.hy の層 runner で、
この file に限って外す。
"""

import os


def e2e_flag() -> str | None:
    """CONDUCTOR_E2E の素の値(無ければ None)。"""
    return os.environ.get("CONDUCTOR_E2E")


def opencode_server_url() -> str | None:
    """CONDUCTOR_OPENCODE_URL の素の値(無ければ None)。"""
    return os.environ.get("CONDUCTOR_OPENCODE_URL")
