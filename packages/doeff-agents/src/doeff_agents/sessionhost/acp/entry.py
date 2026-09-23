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

``join --role <both|agentd|host>``(card acp:kanban-issue:ki-567f2dd6140f)は、その 1 枚の宣言の
どちらの半分をこの process が担うかの宣言。既知の形 = kubelet と container runtime の分離:

* ``both``(既定)— 今日どおり 1 process で両方。``--role`` を付けない起動は env の束も host の argv も
  今日と 1 byte 差なく同じで、停止の hook(走っている手番を AgentdRestart で閉じる)も今日のまま立つ。
* ``agentd`` — ACP 側だけ。host は起こさず、socket の client として別 process の host に繋ぐ
  (socket の出現を上限なしで待つ)。SIGTERM は宣言 drain_seconds の分だけ排水してから降りる(宣言 0 = 待たない)。
  **SIGTERM で走っている手番を 1 つも閉じない** —— 器は host が
  親として持ち続けるので、ACP 側の規則を変えるのに node を配置から外さなくてよい。
* ``host`` — 器だけ。agentd の thread を起こさない(join の env の束は弁を on にするが、役が切る)。

役は **JoinPlan に入らない**(env にも host の argv にも現れない): 宣言 file は機体のもの、役は起こす側
(launchd の unit / pod の container)のもので、1 枚の宣言を 2 つの unit が読むため。

``--help`` / ``join --help`` は usage を stdout に出して exit 0 する。flag の一覧は実装の
宣言(host.hy の ``SERVE-FLAG-SPECS`` / join.hy の ``JOIN-FLAG-SPECS``)から
``sessionhost/usage.py`` が組む — 手で写した一覧を作らない。

report-result-mcp の路(agent が結果を報告する data channel)は Hy も agentd も import せずに
hostmain へ直行する — relay の boot の遅れは report-vs-turn-end の race の凍結物理(S1)。
"""

# pyright: strict
import os
import sys

from doeff_agents.sessionhost.acp.effects import (
    JOIN_ROLE_AGENTD,
    JOIN_ROLE_BOTH,
    JOIN_ROLE_HOST,
    JOIN_SUBCOMMAND,
)
from doeff_agents.sessionhost.acp.valve import acp_valve
from doeff_agents.sessionhost.hostmain import main as host_main
from doeff_agents.sessionhost.relaymain import REPORT_RESULT_MCP_SUBCOMMAND
from doeff_agents.sessionhost.usage import help_topic_of, usage_text

#: 器の入れ替えの口の subcommand(綴りの定義点は host_slot_cli — ここは Hy を読まずに分岐するための写し)。
HOST_SLOT_SUBCOMMAND = "host-slot"


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
    if argv and argv[0] == HOST_SLOT_SUBCOMMAND:
        # 器の入れ替えの blue/green の口(host_slot_cli — 据え付けの道具が撃つ)。
        from doeff_agents.sessionhost.acp.host_slot_cli import main as host_slot_main

        raise SystemExit(host_slot_main(argv[1:]))
    role = JOIN_ROLE_BOTH
    if argv and argv[0] == JOIN_SUBCOMMAND:
        # 1 命令の参加: 宣言 → env の束 + host の argv(join_plan の 1 点)→ 下の弁の経路と同じ。
        from doeff_agents.sessionhost.acp.runtime import (
            AgentdPreflightError,
            apply_join_env,
            join_host_slot,
            join_plan,
            join_role,
        )

        try:
            plan = join_plan(argv[1:], os.environ)
            role = join_role(argv[1:])
            # 器の区画(``--host-slot``・器の入れ替えの blue/green)— 役 host の時だけ db と socket を区画へ移す。
            host_argv = join_host_slot(argv[1:], plan.host_argv)
        except AgentdPreflightError as error:
            sys.stderr.write(f"doeff-sessionhost: {error}\n")
            raise SystemExit(2) from error
        except ValueError as error:
            sys.stderr.write(f"doeff-sessionhost: {error}\n")
            raise SystemExit(2) from error
        apply_join_env(plan, os.environ.update)
        argv = list(host_argv)
    verdict = acp_valve(argv, os.environ)
    # card acp:kanban-issue:ki-567f2dd6140f: 役が host なら agentd の thread を起こさない(弁が on でも —
    # join の env の束は 1 枚の宣言から両 unit へ同じものが渡るので、切るのは役の 1 点)。
    if verdict.enabled and role != JOIN_ROLE_HOST:
        # agentd の runtime は Hy の program を import する — 弁が on の時だけ払う。
        from doeff_agents.sessionhost.acp.runtime import (
            AgentdPreflightError,
            run_agentd_only,
            start_agentd_thread,
        )

        if role == JOIN_ROLE_AGENTD:
            # ACP 側だけの process: host は別 process なので、この process は器を 1 つも持たない。
            # 停止の hook も登録しない(登録する相手の host の accept loop がこの process に無い)—
            # 走っている手番を閉じないのはそのため(run_agentd_only の docstring)。
            try:
                run_agentd_only(verdict.host_argv, os.environ)
            except AgentdPreflightError as error:
                sys.stderr.write(f"doeff-sessionhost: {error}\n")
                raise SystemExit(2) from error
            return

        from doeff_agents.sessionhost.host import register_shutdown_hook

        try:
            run = start_agentd_thread(verdict.host_argv, os.environ, role=role)
        except AgentdPreflightError as error:
            sys.stderr.write(f"doeff-sessionhost: {error}\n")
            raise SystemExit(2) from error
        # 段 10 lane 10h 便 2(agora-redesign #84): host の停止(TERM)の前に agentd が走っている手番を閉じる
        # (turn-record ended・job Ended = AgentdRestart)— host の accept loop が生きている間に走る hook。
        # ⚠ この hook は役が both(1 process で両方)の時だけ立つ: 器と ACP の腕が同じ process で一緒に死ぬ
        #   からこそ「走っている手番を閉じる」が正しい。役を分けた後の ACP 側の停止は手番を閉じない。
        register_shutdown_hook(lambda: run.close_for_stop("SIGTERM"))
    sys.argv = [sys.argv[0], *verdict.host_argv]
    host_main()
