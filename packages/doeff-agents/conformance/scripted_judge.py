"""scripted judge: the deterministic stand-in for agentd's LLM prompt judge
(ADR-DOE-AGENTS-002 R5; contract: conformance/README.md).

Wired via `--prompt-judge-cmd "<python> <this file>"`. agentd pipes the
judge instructions (which embed the pane capture) to stdin and expects
strict JSON {"blocked": bool, "keys": [...], "reason": str} on stdout
(main.rs:3282/3331). Verdicts come from a lookup table so the suite is
fully deterministic:

  CONFORMANCE_JUDGE_TABLE = path to JSON:
      [{"contains": "<pane substring>",
        "verdict": {"blocked": true, "keys": ["Enter"], "reason": "..."}}]

First matching entry wins; no match => not blocked. Every invocation is
journaled to CONFORMANCE_JUDGE_JOURNAL (if set) so drivers can assert the
judge-before-solicitation ordering (R6).
"""

import json
import sys
import time
from pathlib import Path

from doeff import run, with_handlers
from doeff_core_effects.os_process import subprocess_handler
from doeff_core_effects.process_effects import ReadEnvironment

TABLE_VARIABLE = "CONFORMANCE_JUDGE_TABLE"
JOURNAL_VARIABLE = "CONFORMANCE_JUDGE_JOURNAL"


def _received_environment() -> dict[str, str]:
    """The two contract variables agentd launched this judge with, asked once
    through doeff's foundation handler (subprocess_handler answers
    ReadEnvironment from the process environment) instead of read from the
    process environment directly. The env contract above is unchanged
    (agora-redesign #3012)."""
    entries = run(
        with_handlers(
            [subprocess_handler], ReadEnvironment((TABLE_VARIABLE, JOURNAL_VARIABLE))
        )
    )
    return {entry.name: entry.value for entry in entries}


def main() -> None:
    stdin_text = sys.stdin.read()
    env = _received_environment()
    table_path = env.get(TABLE_VARIABLE)
    entries = (
        json.loads(Path(table_path).read_text(encoding="utf-8")) if table_path else []
    )
    verdict: dict[str, object] = {
        "blocked": False,
        "keys": [],
        "reason": "no scripted verdict matched",
    }
    for entry in entries:
        if str(entry["contains"]) in stdin_text:
            verdict = entry["verdict"]
            break
    journal_path = env.get(JOURNAL_VARIABLE)
    if journal_path:
        with Path(journal_path).open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {"event": "judged", "at": time.time(), "verdict": verdict},
                    sort_keys=True,
                )
                + "\n"
            )
    print(json.dumps(verdict))


if __name__ == "__main__":
    main()
