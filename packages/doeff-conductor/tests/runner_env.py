"""doeff-conductor の検の走らせ方を決める環境変数の値(agora-redesign #2894)。

E2E の検を走らせるか(CONDUCTOR_E2E)と OpenCode の server の URL(CONDUCTOR_OPENCODE_URL)は、
検を集める時点で pytest の外から渡す旗(README に書いてある走らせ方)。この module は変数の素の値を
返すだけで、旗の読み方の判断は conftest.py に残す。読みは os.environ を直に読まず、ReadEnvironment の
効果を本物の答え手 subprocess_handler の下で問う(agora-redesign #3012 — 環境変数の読みは foundation の
handler の中だけ)。
"""

from doeff import run, with_handlers
from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import ReadEnvironment


def _setting(name: str) -> str | None:
    """環境変数 name の素の値(無ければ None)を、本物の答え手の下の ReadEnvironment で読むため。"""
    entries = run(with_handlers([subprocess_handler], ReadEnvironment((name,))))
    return next((entry.value for entry in entries if entry.name == name), None)


def e2e_flag() -> str | None:
    """CONDUCTOR_E2E の素の値(無ければ None)。"""
    return _setting("CONDUCTOR_E2E")


def opencode_server_url() -> str | None:
    """CONDUCTOR_OPENCODE_URL の素の値(無ければ None)。"""
    return _setting("CONDUCTOR_OPENCODE_URL")
