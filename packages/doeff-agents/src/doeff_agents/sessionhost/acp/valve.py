"""agentd の弁 — `doeff-sessionhost serve --acp` / env ``DOEFF_AGENTD_ACP=on|off`` の判定(純関数)。

既定は off(今日の sessionhost のまま — 法 agentd-valve-defaults-off)。値の宣言はこの
file の 1 点: ``ACP_VALVE_ENV`` / ``ACP_VALVE_FLAG`` / ``ACP_VALVE_DEFAULT``。
語彙の外の env の値は黙って off に倒さず ``ValueError`` で名指す。
"""

# pyright: strict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

ACP_VALVE_ENV = "DOEFF_AGENTD_ACP"
ACP_VALVE_FLAG = "--acp"
ACP_VALVE_DEFAULT = False
_VALVE_WORDS: dict[str, bool] = {"on": True, "off": False}
#: host の socket の flag(oracle parse_args の綴り — 値が無い時は host と同じく既定へ)。
HOST_SOCKET_FLAG = "--socket"


@dataclass(frozen=True)
class ValveVerdict:
    """弁の答えと、host(oracle の parse_args)へ渡す argv(agentd の flag を除いたもの)。"""

    enabled: bool
    host_argv: tuple[str, ...]


def acp_valve(argv: Sequence[str], env: Mapping[str, str]) -> ValveVerdict:
    """flag ``--acp`` か env ``DOEFF_AGENTD_ACP=on`` で on。どちらも無ければ既定(off)。"""
    host_argv = tuple(arg for arg in argv if arg != ACP_VALVE_FLAG)
    flagged = len(host_argv) != len(argv)
    raw = env.get(ACP_VALVE_ENV)
    if raw is None or raw.strip() == "":
        from_env = ACP_VALVE_DEFAULT
    else:
        word = raw.strip().lower()
        if word not in _VALVE_WORDS:
            raise ValueError(f"{ACP_VALVE_ENV} must be on|off, got {raw!r}")
        from_env = _VALVE_WORDS[word]
    return ValveVerdict(enabled=flagged or from_env, host_argv=host_argv)


def socket_path_override(host_argv: Sequence[str]) -> str | None:
    """host の argv が名指す socket の path(``--socket <path>``)。無ければ None = host の既定。"""
    for index, arg in enumerate(host_argv):
        if arg == HOST_SOCKET_FLAG and index + 1 < len(host_argv):
            return host_argv[index + 1]
    return None
