"""tests/ の中で本物の daemon(doeff-sessionhost)を起こす検の、宿からの隔離の単一実装。

なぜ要るか(2026-09-24 00:50 JST・zeus の実測): `test_agentd_deterministic_result_failure_is_not_retried`
は替え玉の agent(fake_interactive_agent.py)を tmux で動かす検なのに、daemon を素の argv と素の env で
起こしていた。daemon の既定の画面判定(prompt judge)は **本物の** claude の一発実行(`--model haiku`)
(host.hy DEFAULT-PROMPT-JUDGE-CMD — 本番の意図した既定)なので、turn の終わりの判定点ごとに宿の
`~/.claude` のまま本物の claude が 3 回起き、認証に失敗した CLI が宿の `~/.claude/.credentials.json`
を空の token で上書きした。同じ形の漏れがあと 3 つあった:

- daemon の env が宿の CLAUDE_CONFIG_DIR / CODEX_HOME を持ち越す → 起動前の trust の書き込み
  (trust_claude_workspace / trust_codex_workspace の daemon env への fallback)と
  effective_identity が宿の本物の profile を指す。
- tmux が宿の既定の server(または $TMUX の server)に session を作る → pane の shell が宿の HOME の
  rc(conda・opencode の env 等)を読み、Mac では conda の対話の問いが替え玉の起動行を食って検が赤になる。
- PATH に宿の本物の claude / codex が在る → 何かが誤って呼んでも誰も気づかない。

conformance/harness.py の AgentdHarness は同じ理由で `--prompt-judge-cmd ""` と trust の置き場を
既に隔離している(conformance/README.md ハザード 1)。tests/ 側にはその 1 点が無く、起動の場所ごとに
argv と env を手で写していた。ここを tests/ の唯一の起動口にする。

既定の judge(本物の claude)は本番の supervisor の意図した既定なので変えない — 直すのは「検が本番の
既定のまま宿の上で daemon を起こしていた」ことで、それは検の側の責務。
"""

from __future__ import annotations

import os
import stat
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

#: 宿の本物の CLI の名前。隔離した PATH の先頭に同名の「罠」を置き、呼ばれたら記録して非 0 で落ちる。
REAL_AGENT_CLIS: tuple[str, ...] = ("claude", "codex")

#: 罠が落ちる時の終了コード(本物の CLI の失敗と見分けるため固有の値)。
TRIPWIRE_EXIT_CODE = 97

#: daemon の env から外す宿の変数。TMUX / TMUX_PANE が残ると tmux は TMUX_TMPDIR より $TMUX の
#: socket を優先し、宿の server に session を作る。ZDOTDIR が残ると zsh は隔離した HOME でなく宿の rc を読む。
_HOST_ENV_TO_DROP: tuple[str, ...] = (
    "TMUX",
    "TMUX_PANE",
    "ZDOTDIR",
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "DOEFF_AGENTD_PROMPT_JUDGE_CMD",
)


def sessionhost_serve_argv(
    agentd_bin: Path,
    *,
    db_path: Path,
    socket_path: Path,
    monitor_interval_ms: int | None = None,
    max_running: int | None = None,
    extra_args: Sequence[str] = (),
) -> list[str]:
    """検で daemon を起こす argv。画面判定は必ず無効(`--prompt-judge-cmd ""`)。

    判定の挙動そのものを検るのは conformance の S5 / S6 / S6b で、そこは台本の judge を明示で差す。
    tests/ の検は判定を検の対象にしていないので、本物の claude を呼ぶ既定を持ち込まない。
    """
    argv = [str(agentd_bin), "--db", str(db_path), "--socket", str(socket_path)]
    if monitor_interval_ms is not None:
        argv += ["--monitor-interval-ms", str(monitor_interval_ms)]
    if max_running is not None:
        argv += ["--max-running", str(max_running)]
    if "--prompt-judge-cmd" in extra_args:
        raise ValueError(
            "tests/ never wire a prompt judge into a live daemon — judge behaviour is"
            " conformance's S5 / S6 / S6b (scripted judge)"
        )
    return [*argv, "--prompt-judge-cmd", "", *extra_args, "serve"]


@dataclass(frozen=True)
class IsolatedHost:
    """1 回の検のための、宿から切り離した HOME・資格の置き場・tmux server・PATH。"""

    root: Path
    home: Path
    claude_config_dir: Path
    codex_home: Path
    tmux_tmpdir: Path
    tripwire_bin: Path
    tripwire_log: Path
    env: Mapping[str, str]

    def session_env(self) -> dict[str, str]:
        """pane の env に重ねる値。HOME を隔離して、pane の shell が宿の rc を読まないようにする。"""
        return {"HOME": str(self.home)}

    def tmux(self, *args: str) -> subprocess.CompletedProcess[str]:
        """検の側から daemon と同じ私設の tmux server を操作する(掃除・画面の取得)。"""
        return subprocess.run(
            ["tmux", *args],
            capture_output=True,
            text=True,
            check=False,
            env=dict(self.env),
        )

    def kill_tmux_server(self) -> None:
        self.tmux("kill-server")

    def real_cli_invocations(self) -> list[str]:
        """罠に掛かった呼び出し(本物の claude / codex が呼ばれかけた記録)。"""
        if not self.tripwire_log.exists():
            return []
        return [
            line
            for line in self.tripwire_log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def assert_no_real_cli_invoked(self) -> None:
        hits = self.real_cli_invocations()
        if hits:
            raise AssertionError(
                "the test reached for a real agent CLI (it must never touch the host's"
                f" credentials or the real API): {hits}"
            )

    def spawn_sessionhost(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        log: IO[str],
    ) -> subprocess.Popen[str]:
        return subprocess.Popen(
            list(argv),
            cwd=cwd,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            env=dict(self.env),
        )


def isolated_host(root: Path, *, base_env: Mapping[str, str] | None = None) -> IsolatedHost:
    """`root` の下に隔離した宿を作る。daemon とその tmux server・pane・判定の子はこの env を継ぐ。"""
    source = dict(os.environ if base_env is None else base_env)
    home = root / "home"
    claude_config_dir = root / "claude-config"
    codex_home = root / "codex-home"
    tmux_tmpdir = root / "tmux"
    tripwire_bin = root / "tripwire-bin"
    tripwire_log = root / "tripwire.log"
    for path in (home, claude_config_dir, codex_home, tmux_tmpdir, tripwire_bin):
        path.mkdir(parents=True, exist_ok=True)
    for name in REAL_AGENT_CLIS:
        _write_tripwire(tripwire_bin / name, name=name, log_path=tripwire_log)

    env = {key: value for key, value in source.items() if key not in _HOST_ENV_TO_DROP}
    env.update(
        {
            "HOME": str(home),
            "CLAUDE_CONFIG_DIR": str(claude_config_dir),
            "CODEX_HOME": str(codex_home),
            "TMUX_TMPDIR": str(tmux_tmpdir),
            "PATH": os.pathsep.join(
                part for part in (str(tripwire_bin), source.get("PATH", "")) if part
            ),
        }
    )
    return IsolatedHost(
        root=root,
        home=home,
        claude_config_dir=claude_config_dir,
        codex_home=codex_home,
        tmux_tmpdir=tmux_tmpdir,
        tripwire_bin=tripwire_bin,
        tripwire_log=tripwire_log,
        env=env,
    )


def _write_tripwire(path: Path, *, name: str, log_path: Path) -> None:
    path.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "{name} $*" >> "{log_path}"\n'
        f'echo "tripwire: the test invoked the real {name} CLI" >&2\n'
        f"exit {TRIPWIRE_EXIT_CODE}\n",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
