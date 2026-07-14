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
import os
import sys
import time
from pathlib import Path


def main() -> None:
    stdin_text = sys.stdin.read()
    table_path = os.environ.get("CONFORMANCE_JUDGE_TABLE")
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
    journal_path = os.environ.get("CONFORMANCE_JUDGE_JOURNAL")
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
