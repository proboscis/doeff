"""tests/ の本物の daemon の起動が宿から隔離されていることの検(日次の門 `-m 'not e2e'` で走る)。

実弾 2026-09-24 00:50 JST(zeus): 替え玉の agent を使う e2e の検が daemon を素の argv / env で起こし、
既定の画面判定(本物の claude の一発実行)が宿の ~/.claude のまま 3 回起きて資格を空で上書きした。
e2e の検は日次の門で外れるので、隔離の口そのものの検はここ(印なし)に置く。
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest
from sessionhost_isolated_host import (
    TRIPWIRE_EXIT_CODE,
    isolated_host,
    sessionhost_serve_argv,
)

TESTS_DIR = Path(__file__).resolve().parent


def test_serve_argv_always_disables_the_prompt_judge() -> None:
    argv = sessionhost_serve_argv(
        Path("/bin/doeff-sessionhost"),
        db_path=Path("/tmp/x.sqlite"),
        socket_path=Path("/tmp/x.sock"),
        monitor_interval_ms=100,
        max_running=2,
    )
    index = argv.index("--prompt-judge-cmd")
    assert argv[index + 1] == ""
    assert argv[-1] == "serve"
    with pytest.raises(ValueError, match="never wire a prompt judge"):
        sessionhost_serve_argv(
            Path("/bin/doeff-sessionhost"),
            db_path=Path("/tmp/x.sqlite"),
            socket_path=Path("/tmp/x.sock"),
            extra_args=("--prompt-judge-cmd", "claude --model haiku"),
        )


def test_isolated_env_carries_no_host_identity(tmp_path: Path) -> None:
    host_env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/Users/operator",
        "CLAUDE_CONFIG_DIR": "/Users/operator/.claude",
        "CODEX_HOME": "/Users/operator/.codex",
        "TMUX": "/private/tmp/tmux-501/default,1,0",
        "TMUX_PANE": "%1",
        "ZDOTDIR": "/Users/operator",
        "ANTHROPIC_API_KEY": "sk-host",
        "DOEFF_AGENTD_PROMPT_JUDGE_CMD": "claude --model haiku",
        "LANG": "C.UTF-8",
    }
    host = isolated_host(tmp_path, base_env=host_env)
    env = host.env
    assert env["HOME"] == str(tmp_path / "home")
    assert env["CLAUDE_CONFIG_DIR"] == str(tmp_path / "claude-config")
    assert env["CODEX_HOME"] == str(tmp_path / "codex-home")
    assert env["TMUX_TMPDIR"] == str(tmp_path / "tmux")
    for dropped in (
        "TMUX",
        "TMUX_PANE",
        "ZDOTDIR",
        "ANTHROPIC_API_KEY",
        "DOEFF_AGENTD_PROMPT_JUDGE_CMD",
    ):
        assert dropped not in env, dropped
    assert not any("/Users/operator" in value for value in env.values()), env
    assert env["PATH"].split(":")[0] == str(host.tripwire_bin)
    assert env["LANG"] == "C.UTF-8"
    assert host.session_env() == {"HOME": str(tmp_path / "home")}


def test_tripwire_records_a_real_cli_call_and_fails_loudly(tmp_path: Path) -> None:
    host = isolated_host(tmp_path, base_env={"PATH": "/usr/bin:/bin"})
    host.assert_no_real_cli_invoked()
    completed = subprocess.run(
        ["sh", "-c", "claude --model haiku"],
        env=dict(host.env),
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == TRIPWIRE_EXIT_CODE
    assert host.real_cli_invocations() == ["claude --model haiku"]
    with pytest.raises(AssertionError, match="claude --model haiku"):
        host.assert_no_real_cli_invoked()


def _spawns_the_real_daemon(tree: ast.AST) -> bool:
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    return "resolve_sessionhost_bin" in names and bool({"Popen", "spawn_sessionhost"} & attrs)


def test_every_live_daemon_spawn_in_tests_goes_through_the_isolated_argv() -> None:
    """tests/ で本物の daemon を起こす module は、argv を sessionhost_serve_argv から作る。

    手で argv を写すと `--prompt-judge-cmd ""` が落ち、既定の判定が本物の claude を宿の上で起こす。
    """
    offenders: list[str] = []
    spawners: list[str] = []
    for path in sorted(TESTS_DIR.glob("*.py")):
        if path.name in {"sessionhost_isolated_host.py", Path(__file__).name}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if not _spawns_the_real_daemon(tree):
            continue
        spawners.append(path.name)
        called = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if "sessionhost_serve_argv" not in called:
            offenders.append(path.name)
    # 母集団が空だと恒真になる — 現に daemon を起こす 4 本(retry・real-agent retry・byte-faithful・headless)を数えられていることを確かめる。
    assert len(spawners) >= 4, spawners
    assert offenders == [], offenders
