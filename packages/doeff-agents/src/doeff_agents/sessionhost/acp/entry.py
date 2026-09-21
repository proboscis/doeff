"""console script ``doeff-sessionhost`` の入口 — 段 2 の弁と段 6 の 1 命令を持つ薄い殻。

``serve --acp``(か env ``DOEFF_AGENTD_ACP=on``)の時だけ、host の起動の前に agentd の
thread(ACP の cluster への参加・agent-job の受け — ``doeff_agents.sessionhost.acp``)を
起こし、残りは今日の入口 ``hostmain.main``(report-result-mcp の分岐と Hy の host の起動)へ
そのまま渡す。既定は off で、host の argv からは ``--acp`` だけを除く(oracle の parse_args は
未知の flag を断る)。agentd は host の socket の client なので host.hy も hostmain.py も
変わらない。

``join --server <URL> --token-file <札> [--config <agentd.toml>] …``(段 6 lane 6f・決定 23)は
機体を足す 1 命令: 宣言(flag > toml > 既定)から env の束と host の argv を導き(判断は
join.hy の 1 点・runtime.join_plan)、process の env に据えてから上と同じ経路(agentd の
thread + host)を走る。Mac(launchd)・Linux(systemd)・GCP node・runner pod のどれでも同じ命令。

``--help`` / ``join --help`` は usage を stdout に出して exit 0 する。flag の一覧は実装の
宣言(host.hy の ``SERVE-FLAG-SPECS`` / join.hy の ``JOIN-FLAG-SPECS``)から
``sessionhost/usage.py`` が組む — 手で写した一覧を作らない。

report-result-mcp の路(agent が結果を報告する data channel)は Hy も agentd も import せずに
hostmain へ直行する — relay の boot の遅れは report-vs-turn-end の race の凍結物理(S1)。
"""

# pyright: strict
import os
import sys

from doeff_agents.sessionhost.acp.effects import JOIN_SUBCOMMAND
from doeff_agents.sessionhost.acp.valve import acp_valve
from doeff_agents.sessionhost.hostmain import main as host_main
from doeff_agents.sessionhost.relaymain import REPORT_RESULT_MCP_SUBCOMMAND
from doeff_agents.sessionhost.usage import help_topic_of, usage_text


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == REPORT_RESULT_MCP_SUBCOMMAND:
        host_main()
        return
    # `--help` は usage を stdout に出して exit 0(題目の判定 = usage.help_topic_of の
    # 1 点)。一覧は実装の宣言から導くので、手で写した flag の表はどこにも無い。
    topic = help_topic_of(argv)
    if topic is not None:
        sys.stdout.write(usage_text(topic))
        return
    if argv and argv[0] == JOIN_SUBCOMMAND:
        # 1 命令の参加: 宣言 → env の束 + host の argv(join_plan の 1 点)→ 下の弁の経路と同じ。
        from doeff_agents.sessionhost.acp.runtime import (
            AgentdPreflightError,
            apply_join_env,
            join_plan,
        )

        try:
            plan = join_plan(argv[1:], os.environ)
        except AgentdPreflightError as error:
            sys.stderr.write(f"doeff-sessionhost: {error}\n")
            raise SystemExit(2) from error
        apply_join_env(plan, os.environ.update)
        argv = list(plan.host_argv)
    verdict = acp_valve(argv, os.environ)
    if verdict.enabled:
        # agentd の runtime は Hy の program を import する — 弁が on の時だけ払う。
        from doeff_agents.sessionhost.acp.runtime import AgentdPreflightError, start_agentd_thread
        from doeff_agents.sessionhost.host import register_shutdown_hook

        try:
            run = start_agentd_thread(verdict.host_argv, os.environ)
        except AgentdPreflightError as error:
            sys.stderr.write(f"doeff-sessionhost: {error}\n")
            raise SystemExit(2) from error
        # 段 10 lane 10h 便 2(agora-redesign #84): host の停止(TERM)の前に agentd が走っている手番を閉じる
        # (turn-record ended・job Ended = AgentdRestart)— host の accept loop が生きている間に走る hook。
        register_shutdown_hook(lambda: run.close_for_stop("SIGTERM"))
    sys.argv = [sys.argv[0], *verdict.host_argv]
    host_main()
