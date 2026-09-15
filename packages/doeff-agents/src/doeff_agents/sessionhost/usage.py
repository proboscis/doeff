"""`doeff-sessionhost --help` の usage の組み立て。

flag の一覧は実装の宣言から導く — serve は :mod:`host` の ``SERVE_FLAG_SPECS`` /
``SERVE_ENV_ONLY_SPECS``、join は :mod:`acp.join` の ``JOIN_FLAG_SPECS``、agentd の弁は
:mod:`acp.valve`。この module は flag の綴りも既定値も 1 つも持たない — 手で写した一覧は
実装が動いた時に静かに古くなるので、第 2 定義点を作らない。

題目の判定(:func:`help_topic_of`)は Hy を import しない純関数で、help を求めない
golden path に Hy の import 代を足さない(表を読む :func:`usage_text` の中でだけ払う)。
Hy の import hook は親 package の ``__init__`` が登記するので、ここでは重ねない。
"""

# pyright: strict
import textwrap
from collections.abc import Sequence

from doeff_agents.sessionhost.acp.effects import HOST_SERVE_COMMAND, JOIN_SUBCOMMAND
from doeff_agents.sessionhost.relaymain import REPORT_RESULT_MCP_SUBCOMMAND

#: help を求める綴り(`doeff-agents` の click と同じ 2 つ)。
HELP_FLAGS: tuple[str, ...] = ("--help", "-h")

_PROGRAM = "doeff-sessionhost"
_WIDTH = 79
_INDENT = "      "


def help_topic_of(argv: Sequence[str]) -> str | None:
    """この argv が求めている help の題目。求めていなければ None。

    report-result-mcp は agent 向けの data channel で自分の argv を持つので、
    ここでは題目を答えない(relay の路の引数を横取りしない)。
    """
    if not argv or not any(arg in HELP_FLAGS for arg in argv):
        return None
    if argv[0] == REPORT_RESULT_MCP_SUBCOMMAND:
        return None
    if argv[0] == JOIN_SUBCOMMAND:
        return JOIN_SUBCOMMAND
    return HOST_SERVE_COMMAND


def usage_text(topic: str) -> str:
    """題目の usage(末尾に改行を含む 1 つの文字列)。"""
    if topic == JOIN_SUBCOMMAND:
        return "\n".join(_join_lines()) + "\n"
    if topic == HOST_SERVE_COMMAND:
        return "\n".join(_serve_lines()) + "\n"
    raise ValueError(f"unknown help topic: {topic!r}")


def _paragraph(text: str) -> list[str]:
    # 折り返しは語の境界だけ — command 名や path をハイフンで割らない。
    return textwrap.wrap(text, width=_WIDTH, break_on_hyphens=False, break_long_words=False)


def _entry(head: str, body: str) -> list[str]:
    return [
        f"  {head}",
        *textwrap.wrap(
            body,
            width=_WIDTH,
            initial_indent=_INDENT,
            subsequent_indent=_INDENT,
            break_on_hyphens=False,
            break_long_words=False,
        ),
    ]


def _head_of(flag: str, placeholder: str | None) -> str:
    return flag if placeholder is None else f"{flag} {placeholder}"


def _with_env(help_text: str, env: str | None) -> str:
    return help_text if env is None else f"{help_text} [env: {env}]"


def _serve_lines() -> list[str]:
    # Hy の import hook は親 package の __init__ が登記済み(この module を
    # import する経路が必ずそこを通る)。
    from doeff_agents.sessionhost import host
    from doeff_agents.sessionhost.acp.effects import ACP_VALVE_ENV
    from doeff_agents.sessionhost.acp.valve import ACP_VALVE_FLAG, ACP_VALVE_HELP

    lines = [
        f"usage: {_PROGRAM} [{HOST_SERVE_COMMAND}] [<flags>]",
        f"       {_PROGRAM} {JOIN_SUBCOMMAND} --server <URL> --token-file <file> [<flags>]",
        f"       {_PROGRAM} {REPORT_RESULT_MCP_SUBCOMMAND}",
        "",
        *_paragraph(
            "The agent session host: it owns the RPC socket, the session ledger, and"
            " the substrate that carries agent sessions."
        ),
        "",
        *_paragraph(
            f"`{HOST_SERVE_COMMAND}` is the default and only host command, and may be"
            " omitted. `{join}` derives a serve invocation from one declaration and"
            " runs it — the same command on macOS (launchd), Linux (systemd), a GCP"
            " node and a runner pod; see `{program} {join} --help`."
            " `{relay}` is the agent-facing result channel and is not run by hand.".format(
                join=JOIN_SUBCOMMAND,
                program=_PROGRAM,
                relay=REPORT_RESULT_MCP_SUBCOMMAND,
            )
        ),
        "",
        f"{HOST_SERVE_COMMAND} flags:",
    ]
    for flag, placeholder, env, help_text in host.SERVE_FLAG_SPECS:
        lines.extend(_entry(_head_of(flag, placeholder), _with_env(help_text, env)))
    lines.extend(_entry(ACP_VALVE_FLAG, _with_env(ACP_VALVE_HELP, ACP_VALVE_ENV)))
    lines.extend(["", f"{HOST_SERVE_COMMAND} environment:"])
    for env_name, help_text in host.SERVE_ENV_ONLY_SPECS:
        lines.extend(_entry(env_name, help_text))
    lines.extend(
        [
            "",
            *_paragraph(
                "Unknown arguments are rejected, and so is any command other than"
                f" `{HOST_SERVE_COMMAND}`."
            ),
        ]
    )
    return lines


def _join_lines() -> list[str]:
    from doeff_agents.sessionhost.acp import join
    from doeff_agents.sessionhost.acp.effects import JOIN_SCHEMA

    lines = [
        f"usage: {_PROGRAM} {JOIN_SUBCOMMAND} --server <URL> --token-file <file> [<flags>]",
        "",
        *_paragraph(
            "Add this machine to an agent control plane. One command on macOS"
            " (launchd), Linux (systemd), a GCP node and a runner pod: the plan is"
            " derived at one point from the declaration and the host then runs as if"
            f" started with `{HOST_SERVE_COMMAND}`."
        ),
        "",
        *_paragraph(
            f"A flag overrides the `--config` file (schema {JOIN_SCHEMA}), which"
            " overrides the defaults. A declaration that names an unknown key, or a"
            " required value that is missing anywhere, is refused before the host"
            " starts."
        ),
        "",
        f"{JOIN_SUBCOMMAND} flags:",
    ]
    for flag, placeholder, help_text in join.JOIN_FLAG_SPECS:
        lines.extend(_entry(_head_of(flag, placeholder), help_text))
    lines.extend(
        [
            "",
            *_paragraph(
                f"The host's own flags are not accepted here: `{JOIN_SUBCOMMAND}`"
                " derives the host's argv (database, socket, admission ceiling) from"
                " the state directory and the declaration above. Run"
                f" `{_PROGRAM} --help` to see that vocabulary."
            ),
        ]
    )
    return lines
