"""doeff-conductor が環境変数から読む値(agora-redesign #2894)。

conductor の CLI と状態の記録は、どの Program・handler よりも前に、状態の置き場の dir と
profile の設定を決めるので、設定を Ask で受ける入口が無い。この module は変数の素の値を
返すだけで、既定の値・読み方・どれを使うかの判断は呼び手(journal・
workflow_effect_journal・environment)に残す。DOEFF004 は
packages/doeff-conductor/architecture.hy の層 environment で、この module に限って外す。
"""

import os


def xdg_state_home() -> str | None:
    """XDG_STATE_HOME の素の値(無ければ None)。"""
    return os.environ.get("XDG_STATE_HOME")


def profiles_json() -> str | None:
    """CONDUCTOR_PROFILES_JSON の素の値(profile の宣言の JSON・無ければ None)。"""
    return os.environ.get("CONDUCTOR_PROFILES_JSON")


def profile_config_path() -> str | None:
    """CONDUCTOR_PROFILE_CONFIG の素の値(profile の宣言の file の path・無ければ None)。"""
    return os.environ.get("CONDUCTOR_PROFILE_CONFIG")


def default_profile_name() -> str | None:
    """CONDUCTOR_DEFAULT_PROFILE の素の値(既定の profile の名・無ければ None)。"""
    return os.environ.get("CONDUCTOR_DEFAULT_PROFILE")
