"""Console-script entry for the Hy session host (C3) — ``doeff-sessionhost``.

Subcommand dispatch happens HERE, before the Hy host module loads:
`report-result-mcp` is the agent-facing result data channel and must boot at
oracle-comparable latency (see relaymain.py) — importing the host module
first would pay the doeff/Hy import chain (~170ms) on every relay spawn and
lose the report-vs-turn-end race on the golden path (S1).

The serve path keeps the original shape: `python -m` / console scripts cannot
target a ``.hy`` module directly — importing :mod:`hy` first registers the Hy
import hook, after which the host module loads like any other.

agora-redesign #668(削除計画 deletion-plan-b-per-step.md 節 2 の準備 1): console script の入口はここ((c) の
library の側)。serve と report-result-mcp・ready・``--help`` はここが自分で答える。旧い入口(agentd の腕 —
``sessionhost/acp/entry.py``)にしか無い物 — ``join`` / ``host-slot`` の副命令と、serve に agentd を相乗りさせる
弁(``--acp`` か env ``DOEFF_AGENTD_ACP=on``)— だけを、来た時に遅い import で旧い入口へ渡す。判定は旧い入口の
部品(acp の effects の綴り・valve の弁の判定)そのものに任せ、写しを作らない。削除 5-1 で ``acp/`` を消す
時に、``_answered_by_agentd_arm`` と渡す枝を一緒に消す。
"""

import os
import sys

from doeff_agents.sessionhost.ready_probe import READY_SUBCOMMAND
from doeff_agents.sessionhost.ready_probe import main as ready_main
from doeff_agents.sessionhost.relaymain import (
    REPORT_RESULT_MCP_SUBCOMMAND,
    run_report_result_mcp,
)
from doeff_agents.sessionhost.usage import help_topic_of, usage_text


def _answered_by_agentd_arm(argv: list[str]) -> bool:
    """旧い入口(agentd の腕)が答える argv か — join / host-slot の副命令か、agentd の弁が on の serve。"""
    from doeff_agents.sessionhost.acp.effects import JOIN_SUBCOMMAND
    from doeff_agents.sessionhost.acp.entry import HOST_SLOT_SUBCOMMAND
    from doeff_agents.sessionhost.acp.valve import acp_valve

    if argv and argv[0] in (JOIN_SUBCOMMAND, HOST_SLOT_SUBCOMMAND):
        return True
    return acp_valve(argv, os.environ).enabled


def run_host() -> None:
    """Hy の session host を起こす(sys.argv を host の引数として読む)。振り分けはしない — 旧い入口が agentd の thread を
    起こした後に器を起こす路もここを呼ぶ(``main`` を呼ぶと弁の判定で旧い入口へ戻ってしまう)。"""
    import hy  # noqa: F401  # registers the .hy importer

    from doeff_agents.sessionhost import host

    host.main()


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == REPORT_RESULT_MCP_SUBCOMMAND:
        run_report_result_mcp(argv[1:])
        return
    if argv and argv[0] == READY_SUBCOMMAND:
        # host の readiness の probe の口(ready_probe — Hy を import しない)。
        raise SystemExit(ready_main(argv[1:]))
    # `--help` は usage を stdout に出して exit 0(題目の判定 = usage.help_topic_of の 1 点)。
    topic = help_topic_of(argv)
    if topic is not None:
        sys.stdout.write(usage_text(topic))
        return
    if _answered_by_agentd_arm(argv):
        from doeff_agents.sessionhost.acp.entry import main as agentd_arm_main

        agentd_arm_main()
        return
    run_host()
