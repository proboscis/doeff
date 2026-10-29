"""doeff-conductor が環境変数から読む値(agora-redesign #2894)。

conductor の CLI と状態の記録は、どの Program・handler よりも前に、状態の置き場の dir と
profile の設定を決める。この module は変数の素の値を返すだけで、既定の値・読み方・どれを使うかの
判断は呼び手(journal・workflow_effect_journal・environment)に残す。読みは os.environ を直に読まず、
ReadEnvironment の効果を本物の答え手 subprocess_handler の下で問う(agora-redesign #3012 — 環境変数の
読みは foundation の handler の中だけ)。
"""

from doeff import run, with_handlers
from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import ReadEnvironment


def _setting(name: str) -> str | None:
    """環境変数 name の素の値(無ければ None)を、本物の答え手の下の ReadEnvironment で読むため。"""
    entries = run(with_handlers([subprocess_handler], ReadEnvironment((name,))))
    return next((entry.value for entry in entries if entry.name == name), None)


def xdg_state_home() -> str | None:
    """XDG_STATE_HOME の素の値(無ければ None)。"""
    return _setting("XDG_STATE_HOME")


def profiles_json() -> str | None:
    """CONDUCTOR_PROFILES_JSON の素の値(profile の宣言の JSON・無ければ None)。"""
    return _setting("CONDUCTOR_PROFILES_JSON")


def profile_config_path() -> str | None:
    """CONDUCTOR_PROFILE_CONFIG の素の値(profile の宣言の file の path・無ければ None)。"""
    return _setting("CONDUCTOR_PROFILE_CONFIG")


def default_profile_name() -> str | None:
    """CONDUCTOR_DEFAULT_PROFILE の素の値(既定の profile の名・無ければ None)。"""
    return _setting("CONDUCTOR_DEFAULT_PROFILE")
