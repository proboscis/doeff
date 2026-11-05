"""Check an installed defsemgrep with one semgrep run per set of the same configuration.

If semgrep is started twice per rule enforcement (an example that hits and an example that does not hit), the cost of one startup (about 2.3 seconds) piles up "number of rules x 2" times.
In agora-controllers' defadr_turn_boundary, 7 rules meant 14 runs and about 33 seconds, which exceeded the daily limit per file (60 seconds).
The examples of rules that read the same configuration are batched into one semgrep run, and the answer is reused by the remaining enforcements in the same process.

Examples are placed in a dir per set, and each set is made a git repo to pin the root. If the batched tree is inside an outside git repo, the root
falls to that repo, and paths.include pinned to the root (the ``/pkg/**`` shape) silently dies — an example that should hit does not hit, and an example that should not hit
silently turns green (counterexample = an enforcement run inside an outside repo).
"""

import subprocess
from collections.abc import Generator
from pathlib import Path
from unittest import mock

import doeff_adr._registry as registry_module
import pytest
from doeff_adr import AssertSemgrep, RegisterSemgrep, semgrep_enforcements
from doeff_adr._registry import isolated_registry
from doeff_adr._semgrep_verdicts import verdict_store

from doeff import run

WORDS = ("eval", "exec", "compile")


def _register(effect: RegisterSemgrep) -> None:
    """Register a Semgrep enforcement in the process registry (effect RegisterSemgrep)."""
    run(semgrep_enforcements()(effect))


def _check(enforcement_id: str) -> None:
    """Check a Semgrep enforcement in the registry (effect AssertSemgrep)."""
    run(semgrep_enforcements()(AssertSemgrep(enforcement_id)))


def _rules() -> str:
    return "rules:\n" + "".join(
        f"  - id: batch-probe-no-{word}\n"
        "    languages:\n"
        "      - python\n"
        "    severity: ERROR\n"
        "    message: probe rule for the batched installed defsemgrep\n"
        f"    pattern: {word}(...)\n"
        "    paths:\n"
        "      include:\n"
        '        - "/pkg/**/*.py"\n'
        for word in WORDS
    )


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[Path]:
    """A tree with the configuration of three rules and the examples of the three rules registered (the path of the example that hits is the same pkg/mod.py for all three rules)."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / ".semgrep.yaml").write_text(_rules(), encoding="utf-8")
    monkeypatch.chdir(root)
    with isolated_registry():
        for word in WORDS:
            _register(
                RegisterSemgrep(
                    f"batch_probe_{word}",
                    rule_id=f"batch-probe-no-{word}",
                    hit_fixtures=[{"relative-path": "pkg/mod.py", "source": f"{word}('1')\n"}],
                    clean_fixtures=[
                        {"relative-path": "pkg/mod.py", "source": "print('1')\n"},
                        # Outside the rule's include (/pkg/**) — if the root is correct it does not hit.
                        {"relative-path": "other/mod.py", "source": f"{word}('1')\n"},
                    ],
                )
            )
        yield root


def test_specs_sharing_a_config_are_checked_with_one_semgrep_run(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = mock.Mock(wraps=subprocess.run)
    monkeypatch.setattr(registry_module.subprocess, "run", spy)

    for word in WORDS:
        _check(f"batch_probe_{word}")

    semgrep_runs = [
        call
        for call in spy.call_args_list
        if any(Path(str(part)).name == "semgrep" for part in call.args[0])
    ]
    assert len(semgrep_runs) == 1, (
        f"semgrep was started {len(semgrep_runs)} times (the three rules with the same configuration should be batched into one)"
    )


def test_the_batch_keeps_root_anchored_paths_inside_an_outer_git_repo(
    tree: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    monkeypatch.setattr(registry_module.tempfile, "tempdir", str(outer))
    # An enforcement run inside a commit hook inherits GIT_DIR — semgrep's root inference must not be pulled by it.
    monkeypatch.setenv("GIT_DIR", str(outer / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(outer))

    for word in WORDS:
        _check(f"batch_probe_{word}")


def test_a_hit_fixture_under_a_tests_dir_is_read(tree: Path) -> None:
    # Failure case (#1201): when a set's dir is passed, semgrep skips tests/ inside the dir by default (a root without .semgrepignore) —
    # the rule did not hit the example that should hit under tests/ and turned red (4 enforcements of doeff's enforce_002). Example files are read by name.
    _register(
        RegisterSemgrep(
            "batch_probe_inside_tests",
            rule_id="batch-probe-no-eval",
            hit_fixtures=[{"relative-path": "pkg/tests/test_mod.py", "source": "eval('1')\n"}],
            clean_fixtures=[{"relative-path": "pkg/tests/test_mod.py", "source": "print('1')\n"}],
        )
    )

    _check("batch_probe_inside_tests")


def test_a_rule_that_does_not_fire_on_its_hit_fixture_is_still_red(
    tree: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(
        RegisterSemgrep(
            "batch_probe_outside_the_include",
            rule_id="batch-probe-no-eval",
            hit_fixtures=[{"relative-path": "other/mod.py", "source": "eval('1')\n"}],
            clean_fixtures=[{"relative-path": "pkg/mod.py", "source": "print('1')\n"}],
        )
    )

    with pytest.raises(AssertionError, match="did not fire on hit fixtures"):
        _check("batch_probe_outside_the_include")


def test_an_outer_verdict_store_does_not_reach_the_mechanism_checks() -> None:
    # Failure case (doeff daily 10-11 16:00): the daily run passes DOEFF_ADR_SEMGREP_VERDICTS to the child. The root's processing stage wrote answers to the same place,
    # and this file's enforcement in the packages stage read those answers and started semgrep only 0 times, so the "batch into one" enforcement turned red.
    # An enforcement that checks the mechanism does not read an outside place (the enforcement that checks the place names its own place in test_semgrep_verdict_store.py).
    assert verdict_store() is None
