"""Polarity test for doeff-agents-no-sleep-effect-hy.

doeff-agents has no wait effect of its own -- programs yield doeff_time's Delay (ADR-DOE-TIME-001 R7). Python definitions and imports are
covered by doeff-agents-no-sleep-effect, but Hy definitions (the Sleep in io_effects.hy and the ClockSleep form in sessionhost/effects.hy) were not hit.
Only lines with the "fires" marker comment in the sample must be hit, checked against the rule's hit lines. Shapes that should fire = a defclass of an EffectBase whose name contains Sleep / Delay,
and a constructor definition whose name is sleep or ends in -sleep. Shapes that should not fire = effects that are not waits, types that do not extend EffectBase, programs that wait via Delay,
and the DelayEffect clause of a fake handler.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG = REPO_ROOT / ".semgrep.yaml"
RULE = "doeff-agents-no-sleep-effect-hy"
FIXTURE_ROOT = REPO_ROOT / "tests" / "semgrep" / "fixtures" / "python"
SAMPLE = Path("packages/doeff-agents/src/doeff_agents/own_wait_effect_forbidden.hy")
FIRES = "; fires"


def test_only_the_marked_lines_fire() -> None:
    findings = _semgrep_results(CONFIG, str(SAMPLE), cwd=FIXTURE_ROOT)
    hits = sorted(finding["start"]["line"] for finding in findings if finding["check_id"].endswith(RULE))
    expected = sorted(
        number
        for number, line in enumerate((FIXTURE_ROOT / SAMPLE).read_text(encoding="utf-8").splitlines(), start=1)
        if line.rstrip().endswith(FIRES)
    )
    assert len(expected) == 5, "the sample's marked lines changed"
    assert hits == expected
