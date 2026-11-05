"""Positive control and discrimination for the physics of placing a symlink in a seat's home not leaking out of one point (decision D8).

What the rule protects is that "installation is one verb (substrate.hy ensure-symlink-outcome)", and only that verb
covers the target with a temporary symlink of a unique name in a single rename(2) step. If hand-rolled outside, one of two seats sharing a home on the same beat
drops out (measured 200/200 and 198/200), and a seat reading during the re-pointing sees a moment with no root (52-53 %).

⚠ Discrimination is needed because this rule lets the one shipped point through with a **file-level exclude** -- if the exclude's
spelling moves, the rule silently goes quiet entirely (a positive control alone cannot notice). The 2nd test below pins that
the shipped verb's file does contain the spelling and that the exclude lets it through.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

RULE = "symlink-install-has-one-home"
VERB_FILE = "packages/doeff-agents/src/doeff_agents/_sessionhost/substrate.hy"
FIXTURE = (
    "packages/doeff-agents/src/doeff_agents/_sessionhost/impls/"
    "symlink_install_outside_substrate_forbidden.hy"
)


def _hits(findings: list[dict], rule: str) -> list[int]:
    return sorted(
        finding["start"]["line"] for finding in findings if finding["check_id"].endswith(rule)
    )


def test_symlink_install_outside_the_one_verb_is_rejected() -> None:
    root = Path(__file__).resolve().parents[2]
    findings = _semgrep_results(
        root / ".semgrep.yaml", FIXTURE, cwd=root / "tests/semgrep/fixtures/python"
    )
    # 2nd line = os.symlink / 3rd line = Path.symlink_to / 4th line = shell `ln -sfn`
    # (BSD ln is not atomic -- the worst of the physics the rule's wording forbids).
    # Delegating forms and read syscalls are not matched.
    assert _hits(findings, RULE) == [2, 3, 4]


def test_the_one_verb_itself_stays_green() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / VERB_FILE).read_text(encoding="utf-8")
    # Precondition of the control: the shipped verb's file does contain the spelling (otherwise the green below is meaningless).
    assert "os.symlink" in source
    findings = _semgrep_results(root / ".semgrep.yaml", VERB_FILE, cwd=root)
    assert _hits(findings, RULE) == []
