"""Positive control and discrimination: doeff-claude-judge must not start a Claude CLI process itself.

The judgement handler only emits a one-shot print effect (ClaudePrintOnce of doeff_claude_code); the process (argv, credential location,
wait limit, how to stop it) belongs to the doeff-claude-code handler. The rule doeff-claude-judge-starts-no-process rejects, in the src of doeff-claude-judge,
shapes that start a process (importing subprocess, os.system, asyncio child processes) -- so that a second place for the CLI spelling and credential passing is not created.

- Test 1: the fixture's failing shapes (the subprocess imports on lines 1 and 2, os.system on line 8, the asyncio child process on line 9) are rejected, and
  a function that only returns an effect is not rejected.
- Test 2: the real package's src is not rejected.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

RULE = "doeff-claude-judge-starts-no-process"
FIXTURE = "packages/doeff-claude-judge/src/doeff_claude_judge/process_forbidden.py"
PACKAGE_SRC = "packages/doeff-claude-judge/src"


def _hits(findings: list[dict], rule: str) -> list[int]:
    return sorted(finding["start"]["line"] for finding in findings if finding["check_id"].endswith(rule))


def test_starting_a_process_in_the_judge_package_is_rejected() -> None:
    root = Path(__file__).resolve().parents[2]
    findings = _semgrep_results(root / ".semgrep.yaml", FIXTURE, cwd=root / "tests/semgrep/fixtures/python")
    assert _hits(findings, RULE) == [1, 2, 8, 9]


def test_the_judge_package_leaves_the_process_to_the_print_effect() -> None:
    root = Path(__file__).resolve().parents[2]
    findings = _semgrep_results(root / ".semgrep.yaml", PACKAGE_SRC, cwd=root)
    assert _hits(findings, RULE) == []
