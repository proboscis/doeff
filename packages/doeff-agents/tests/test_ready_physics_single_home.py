"""ADR-DOE-AGENTS-008: readiness physics has exactly one defining module per fact.

ADR-DOE-AGENTS-004's law ``protocol-physics-has-one-home`` bans duplicating a
kind's protocol physics across modules — the 2026-07-05 incident class where
the same CLI's physics lived in two implementations and only the one being
looked at got fixed (claude trust-dialog permanent hang). This suite freezes
the readiness / idle-detection instance of that law:

1. Gate-form text physics (ready pattern strings, the screen-reader trust
   prompt predicate) are DEFINED in the doeff-free physics leaf
   ``doeff_agents/sessionhost/impls/ready_physics.hy`` and only IMPORTED by
   the adapters / ``session.py`` consumers. The leaf stays free of doeff
   imports so the package root's documented property — the imperative
   session transport API runs without importing the doeff VM — survives.
2. Observation-form physics (``has-idle-prompt`` etc.) stays in
   ``impls/markers.hy``. The codex gate regex and the markers predicates are
   two formalisms over the same facts; on every fixture frame where both
   gates are defined they must agree (parity — silent drift between the two
   formalisms is exactly the disease the law exists to prevent).
3. The repl-idle budget default (120s) has exactly one literal definition:
   ``sessionhost/effects.hy`` (MonitorKnobs vocabulary). ``launch.hy`` and
   ``host.hy`` import it.

Known divergence registered while writing this suite (deliberately NOT
pinned as expected behavior here — it is a defect tracked in its own issue,
see the ADR problem facts): the codex trust-dialog frame is rejected by the
gate regex (``(?!\\d+\\.)`` menu lookahead) but ``has-idle-prompt`` alone
accepts its ``› 1. Yes, continue`` selection marker. sessionhost normally
never reaches that frame because per-kind pre-launch trusts the workspace
first (impls/codex.hy), but the ``skip_trust_setup`` launch path has no
fail-closed guard for it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import hy  # noqa: F401  # .hy import hook — the physics home is a Hy module

from doeff_agents.adapters.claude import ClaudeAdapter
from doeff_agents.adapters.codex import CodexAdapter
from doeff_agents.sessionhost.impls import markers, ready_physics

PKG_ROOT = Path(__file__).resolve().parents[1] / "src" / "doeff_agents"
READY_SCREENS = Path(__file__).parent / "data" / "ready_screens"


def _screen(name: str) -> str:
    return (READY_SCREENS / name).read_text(encoding="utf-8")


# =============================================================================
# 1. One home: adapters return the canonical constants, never re-define them
# =============================================================================


def test_claude_ready_pattern_is_the_physics_home_constant() -> None:
    assert ClaudeAdapter().ready_pattern is ready_physics.CLAUDE_SCREEN_READER_READY_PATTERN


def test_codex_ready_pattern_is_the_physics_home_constant() -> None:
    assert CodexAdapter().ready_pattern is ready_physics.CODEX_READY_PATTERN


@pytest.mark.parametrize("module", ["claude.py", "codex.py"])
def test_adapters_do_not_define_ready_pattern_literals(module: str) -> None:
    source = (PKG_ROOT / "adapters" / module).read_text(encoding="utf-8")
    assert not re.search(r"READY_PATTERN\s*=\s*r?[\"']", source), (
        f"adapters/{module} defines a ready-pattern literal — the physics home "
        "is sessionhost/impls/ready_physics.hy (ADR-DOE-AGENTS-008 R1)"
    )


def test_physics_leaf_is_doeff_free() -> None:
    # The leaf must not pull the doeff VM into the imperative transport's
    # import graph (package-root lazy-import property).
    source = (
        PKG_ROOT / "sessionhost" / "impls" / "ready_physics.hy"
    ).read_text(encoding="utf-8")
    assert "(import doeff" not in source, (
        "ready_physics.hy must stay a doeff-free leaf: adapters import it "
        "without dragging the doeff VM into the transport import graph"
    )


# =============================================================================
# 2. Screen-reader trust prompt predicate lives in the home
# =============================================================================


def test_screen_reader_trust_prompt_predicate_matches_fixture() -> None:
    assert ready_physics.has_claude_screen_reader_trust_prompt(
        _screen("claude_screen_reader_trust_dialog.txt")
    )
    assert not ready_physics.has_claude_screen_reader_trust_prompt(
        _screen("claude_screen_reader_ready.txt")
    )


def test_session_py_does_not_redefine_trust_prompt_physics() -> None:
    source = (PKG_ROOT / "session.py").read_text(encoding="utf-8")
    assert "def _screen_reader_trust_prompt_visible" not in source, (
        "session.py re-defines the screen-reader trust prompt physics — the "
        "home is sessionhost/impls/ready_physics.hy (ADR-DOE-AGENTS-008 R1)"
    )
    assert "has_claude_screen_reader_trust_prompt" in source


# =============================================================================
# 3. Codex parity: gate regex and observation predicates agree on every frame
#    where both are defined (trust dialog excluded — known divergence, see
#    module docstring)
# =============================================================================

PARITY_FRAMES: list[tuple[str, bool]] = [
    ("codex_ready.txt", True),
    ("codex_mcp_boot.txt", False),
    ("codex_login.txt", False),
    ("codex_update_dialog.txt", False),
]


def _predicate_gate_verdict(screen: str) -> bool:
    """Sessionhost's static verdict for 'this frame is a ready composer'."""
    dialog, _keys = markers.detect_dialog(screen)
    return bool(
        markers.has_idle_prompt(screen)
        and not markers.is_starting_mcp_servers(screen)
        and dialog is None
    )


@pytest.mark.parametrize(("fixture", "expected"), PARITY_FRAMES)
def test_codex_gate_regex_and_predicates_agree(fixture: str, expected: bool) -> None:
    screen = _screen(fixture)
    regex_verdict = re.search(ready_physics.CODEX_READY_PATTERN, screen) is not None
    predicate_verdict = _predicate_gate_verdict(screen)
    assert regex_verdict is expected
    assert predicate_verdict is expected


# =============================================================================
# 4. repl-idle budget: one literal home (effects.hy MonitorKnobs vocabulary)
# =============================================================================


def test_repl_idle_budget_is_not_redefined_in_launch_hy() -> None:
    source = (PKG_ROOT / "sessionhost" / "launch.hy").read_text(encoding="utf-8")
    assert not re.search(r"\(setv\s+REPL-IDLE-MAX-WAIT-SECONDS\s", source), (
        "launch.hy re-defines the repl-idle budget literal — the home is "
        "sessionhost/effects.hy (ADR-DOE-AGENTS-008 R2)"
    )


def test_host_hy_takes_budget_from_effects_not_launch() -> None:
    source = (PKG_ROOT / "sessionhost" / "host.hy").read_text(encoding="utf-8")
    launch_import = re.search(
        r"\(import doeff_agents\.sessionhost\.launch \[[^]]*\]", source
    )
    assert launch_import is not None
    assert "REPL-IDLE-MAX-WAIT-SECONDS" not in launch_import.group(0), (
        "host.hy imports the budget via launch.hy — import it from the home "
        "(sessionhost/effects.hy) instead"
    )


def test_repl_idle_budget_values_cohere() -> None:
    from doeff_agents.sessionhost import effects, launch

    assert effects.REPL_IDLE_MAX_WAIT_SECONDS == 120
    assert launch.REPL_IDLE_MAX_WAIT_SECONDS == 120
    assert effects.MonitorKnobs().repl_idle_max_wait_seconds == 120
