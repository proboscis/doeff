"""Console-script entry for the Hy session host (C3) — ``doeff-sessionhost``.

Subcommand dispatch happens HERE, before the Hy host module loads:
`report-result-mcp` is the agent-facing result data channel and must boot at
oracle-comparable latency (see relaymain.py) — importing the host module
first would pay the doeff/Hy import chain (~170ms) on every relay spawn and
lose the report-vs-turn-end race on the golden path (S1).

The serve path keeps the original shape: `python -m` / console scripts cannot
target a ``.hy`` module directly — importing :mod:`hy` first registers the Hy
import hook, after which the host module loads like any other. ``--help`` is
answered by the host module itself (host.serve-usage-text).
"""

import sys

from doeff_agents.sessionhost.relaymain import (
    REPORT_RESULT_MCP_SUBCOMMAND,
    run_report_result_mcp,
)


def run_host() -> None:
    """Hy の session host を起こす(sys.argv を host の引数として読む)。"""
    import hy  # noqa: F401  # registers the .hy importer

    from doeff_agents.sessionhost import host

    host.main()


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == REPORT_RESULT_MCP_SUBCOMMAND:
        run_report_result_mcp(argv[1:])
        return
    run_host()
