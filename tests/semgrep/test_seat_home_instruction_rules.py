"""Positive controls and discrimination for the two common-instruction rules carried to the seat's home (D10 / D11).

Two properties are guarded:

* **D10** (`host-checkout-layout-is-never-spelled-here`) matches any way of spelling the host checkout layout
  with `dotfiles` **as a path element**. A form that looks only right after a quote misses
  `(os.path.join home "dotfiles" ...)`, `Path(home) / "dotfiles"`, and single quotes,
  and conversely falsely matches `my-dotfiles/` and `acp.dotfiles/` (measured in this file's discrimination).
* The 3rd pattern of **D11** (`seat-home-instruction-names-have-one-home`) is **closed within a line**.
  `[^"]` includes newlines, so it would sweep one quote in prose and a spelling several lines below into a single match.

Warning: this control pins that the one name that is let through (`"dotfiles/cron_management"` = one item handled in a separate change)
is in fact still let through even in the widened form (pattern-not-regex applies to the range of a single match,
so widening the form may silently make it ineffective).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_vm_failfast_semgrep_rules import _semgrep_results

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests/semgrep/fixtures/python"
FIXTURE_DIR = "packages/doeff-agents/src/doeff_agents/_sessionhost"

D10 = "host-checkout-layout-is-never-spelled-here"
D11 = "seat-home-instruction-names-have-one-home"

#: The old form (the spelling that landed at L247) -- assembled here only for discrimination. The shipped form is the single spot in .semgrep.yaml.
OLD_SHAPES = """
rules:
  - id: old-d10
    languages: [generic]
    severity: ERROR
    message: old D10 (quote-adjacent only)
    patterns:
      - pattern-regex: '"[^"\\n]*dotfiles/[^"\\n]*"'
      - pattern-not-regex: '"dotfiles/cron_management"'
  - id: old-d11-third
    languages: [generic]
    severity: ERROR
    message: old D11 third shape (crosses newlines)
    pattern-regex: '"[^"]*/(CLAUDE\\.md|skills)"'
"""


def _lines(findings: list[dict], rule: str, filename: str) -> list[int]:
    return sorted(
        finding["start"]["line"]
        for finding in findings
        if finding["check_id"].endswith(rule) and finding["path"].endswith(filename)
    )


@pytest.fixture(scope="module")
def shipped() -> list[dict]:
    """Fire the shipped .semgrep.yaml once at the 3 control files."""
    return _semgrep_results(REPO_ROOT / ".semgrep.yaml", FIXTURE_DIR, cwd=FIXTURE_ROOT)


@pytest.fixture(scope="module")
def old(tmp_path_factory: pytest.TempPathFactory) -> list[dict]:
    config = tmp_path_factory.mktemp("old-shapes") / "old.yaml"
    config.write_text(OLD_SHAPES, encoding="utf-8")
    return _semgrep_results(config, FIXTURE_DIR, cwd=FIXTURE_ROOT)


def test_d10_catches_every_way_of_spelling_the_host_layout(shipped: list[dict]) -> None:
    # Lines 7-14 = right after a quote / absolute path / ~ / f-string / os.path.join / Path's / /
    # single quote / one-word element. Lines 15 onward (my-dotfiles, dotfiles-base, acp.dotfiles,
    # and the let-through "dotfiles/cron_management") are not matched.
    assert _lines(shipped, D10, "host_checkout_layout_forbidden.py") == [7, 8, 9, 10, 11, 12, 13, 14]
    assert _lines(shipped, D10, "host_checkout_layout_forbidden.hy") == [3, 4, 5, 6]


def test_d11_third_shape_closes_inside_one_line(shipped: list[dict]) -> None:
    assert _lines(shipped, D11, "seat_home_instruction_names_forbidden.hy") == [6, 7, 8]


def test_old_shapes_are_the_ones_this_bundle_replaced(old: list[dict]) -> None:
    """Discrimination: the old form actually had three problems: misses, false hits, and matching across newlines."""
    # Misses: os.path.join (11), Path's / (12), single quote (13), one-word element (14)
    py = _lines(old, "old-d10", "host_checkout_layout_forbidden.py")
    assert py == [7, 8, 9, 10, 17, 19]
    # False hits: 17 = "my-dotfiles/claude", 19 = "acp.dotfiles/claude"
    assert 17 in py
    assert 19 in py
    # Across newlines: makes one match from the quote in the prose on line 10 to the spelling on line 11
    straddle = [
        (finding["start"]["line"], finding["end"]["line"])
        for finding in old
        if finding["check_id"].endswith("old-d11-third")
        and finding["path"].endswith("seat_home_instruction_names_forbidden.hy")
    ]
    assert (10, 11) in straddle
