"""`doeff-sessionhost --help` の usage(#57)。

穴は「最初に撃つ命令が Python の traceback を返す」だった。だからこの検は 2 段で
撃つ: 純関数(題目の判定と本文の組み立て)と、console script の実射(stdout に出て
exit 0 になる)。本文の網羅は実装の宣言と突き合わせる — 期待する行を手で書くと
usage と同じ第 2 定義点を test に作ってしまう。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import hy  # noqa: F401  # registers the .hy importer
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from doeff_agents.sessionhost import host, usage
from doeff_agents.sessionhost.acp import join
from doeff_agents.sessionhost.acp.effects import (
    ACP_VALVE_ENV,
    HOST_SERVE_COMMAND,
    JOIN_SUBCOMMAND,
)
from doeff_agents.sessionhost.acp.valve import ACP_VALVE_FLAG
from doeff_agents.sessionhost.relaymain import REPORT_RESULT_MCP_SUBCOMMAND

_ENTRY_SCRIPT = (
    "import sys;"
    "from doeff_agents.sessionhost.acp.entry import main;"
    "sys.argv = ['doeff-sessionhost', *sys.argv[1:]];"
    "main()"
)


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    """console script と同じ入口を別 process で撃つ(host は起こさない経路)。"""
    return subprocess.run(
        [sys.executable, "-c", _ENTRY_SCRIPT, *args],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        cwd=str(Path(__file__).resolve().parents[1]),
    )


@pytest.mark.parametrize("help_flag", usage.HELP_FLAGS)
def test_help_topic_is_serve_for_the_bare_command(help_flag: str) -> None:
    assert usage.help_topic_of([help_flag]) == HOST_SERVE_COMMAND
    assert usage.help_topic_of([HOST_SERVE_COMMAND, help_flag]) == HOST_SERVE_COMMAND
    assert usage.help_topic_of([JOIN_SUBCOMMAND, help_flag]) == JOIN_SUBCOMMAND


def test_help_topic_is_absent_without_a_help_flag() -> None:
    assert usage.help_topic_of([]) is None
    assert usage.help_topic_of([HOST_SERVE_COMMAND]) is None
    assert usage.help_topic_of(["--db", "/tmp/x", HOST_SERVE_COMMAND]) is None
    # relay の路は自分の argv を持つ — help の判定は横取りしない。
    assert usage.help_topic_of([REPORT_RESULT_MCP_SUBCOMMAND, "--help"]) is None


def test_unknown_topic_is_loud() -> None:
    with pytest.raises(ValueError, match="unknown help topic"):
        usage.usage_text("frobnicate")


def test_serve_usage_lists_every_flag_the_host_accepts() -> None:
    text = usage.usage_text(HOST_SERVE_COMMAND)
    for flag, placeholder, env, help_text in host.SERVE_FLAG_SPECS:
        assert flag in text, flag
        if placeholder is not None:
            assert f"{flag} {placeholder}" in text, flag
        if env is not None:
            assert env in text, env
        # 説明は折り返されるので、先頭の語で在ることを見る。
        assert help_text.split()[0] in text, flag
    for env_name, _help_text in host.SERVE_ENV_ONLY_SPECS:
        assert env_name in text, env_name
    # 弁の旗は host の flag ではないが、利用者から見た serve の面なので載る。
    assert ACP_VALVE_FLAG in text
    assert ACP_VALVE_ENV in text
    # 3 つの入口が使い方に出る。
    assert f"doeff-sessionhost [{HOST_SERVE_COMMAND}]" in text
    assert JOIN_SUBCOMMAND in text
    assert REPORT_RESULT_MCP_SUBCOMMAND in text


def test_join_usage_lists_every_flag_join_accepts() -> None:
    text = usage.usage_text(JOIN_SUBCOMMAND)
    for flag, placeholder, help_text in join.JOIN_FLAG_SPECS:
        assert f"{flag} {placeholder}" in text, flag
        assert help_text.split()[0] in text, flag
    # host の flag は join の面ではない(ここに出したら受付と食い違う)。
    assert "--db <path>" not in text
    assert "--socket <path>" not in text


@pytest.mark.parametrize(
    "argv",
    [("--help",), ("-h",), (HOST_SERVE_COMMAND, "--help"), (JOIN_SUBCOMMAND, "--help")],
)
def test_help_prints_usage_to_stdout_and_exits_zero(argv: tuple[str, ...]) -> None:
    result = _run(*argv)
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("usage: doeff-sessionhost")
    assert "Traceback" not in result.stderr
    assert result.stderr == ""


def test_join_help_does_not_need_a_declaration() -> None:
    """`join --help` は宣言の必須値を求める前に usage を出す。

    穴の実体は「最初に撃つ命令が何を宣言すべきか読めない」ことだったので、
    宣言なしの `join --help` が help を出すことが受入の条件。
    """
    result = _run(JOIN_SUBCOMMAND, "--help")
    assert result.returncode == 0, result.stderr
    assert "--server <URL>" in result.stdout
    assert "--token-file <path>" in result.stdout
