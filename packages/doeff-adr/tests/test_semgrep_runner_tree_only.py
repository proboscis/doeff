"""Machine independence of the defsemgrep runner -- the meaning of a check is decided by the tree alone.

Measured 2026-08-17 (node-h full check, route-3da438e018): the remote check tree
does not carry git metadata (.git) (remote_check copies only the git ls-files listing).
On that tree defsemgrep failed completely due to two machine dependencies:

1. Repo root detection depended on the existence of .git -- RuntimeError on a tree without .git.
   The meaning of a check tree must be decided by the tree itself (existence of the config file),
   and must not change with the presence or absence of git metadata.
2. When semgrep itself crashed at startup (exit 1, empty stdout), the runner
   silently read it as "no fire" -- a broken scanner and "the rule does not fire" were
   not distinguished, and the root cause (interpreter mismatch) was reported as the false diagnosis
   "the installed rule does not fire on the hit fixture". The fact that the scan did not run
   must fail loudly.
"""

import json
import re
import stat
import textwrap
from pathlib import Path

import pytest
from doeff_adr import AssertSemgrep, RegisterSemgrep, semgrep_enforcements
from doeff_adr._registry import (
    _resolve_config_path,
    _run_semgrep,
    isolated_registry,
)

from doeff import run


@pytest.fixture
def registry():
    with isolated_registry():
        yield


def _register(effect: RegisterSemgrep) -> None:
    """Register a Semgrep check in the process registry (effect RegisterSemgrep)."""
    run(semgrep_enforcements()(effect))


def _check(enforcement_id: str) -> None:
    """Verify a Semgrep check in the registry (effect AssertSemgrep)."""
    run(semgrep_enforcements()(AssertSemgrep(enforcement_id)))


def _make_tree_without_git(tmp_path: Path) -> Path:
    """A mock of a check tree -- it has the config but no git metadata."""
    root = tmp_path / "tree"
    (root / "packages" / "sample").mkdir(parents=True)
    (root / ".semgrep.yaml").write_text(
        textwrap.dedent(
            """\
            rules:
              - id: no-future-annotations-tree-only-probe
                languages:
                  - python
                severity: ERROR
                message: probe rule for tree-only config resolution
                pattern: from __future__ import annotations
            """
        ),
        encoding="utf-8",
    )
    return root


def test_resolve_config_path_does_not_require_git_metadata(tmp_path, monkeypatch):
    root = _make_tree_without_git(tmp_path)
    assert not (root / ".git").exists()
    monkeypatch.chdir(root / "packages" / "sample")

    resolved = _resolve_config_path(".semgrep.yaml")

    assert resolved == root / ".semgrep.yaml"


def test_resolve_config_path_fails_when_no_tree_carries_the_config(tmp_path, monkeypatch):
    lonely = tmp_path / "no-config-anywhere"
    lonely.mkdir()
    monkeypatch.chdir(lonely)

    with pytest.raises(RuntimeError, match=re.escape("doeff-adr-missing-config-probe.yaml")):
        _resolve_config_path("doeff-adr-missing-config-probe.yaml")


def test_installed_rule_enforcement_runs_on_tree_without_git(tmp_path, monkeypatch, registry):
    """The whole installed-rule path (config resolution -> fixture scan) works without .git."""
    root = _make_tree_without_git(tmp_path)
    monkeypatch.chdir(root)
    _register(
        RegisterSemgrep(
            "tree_only_installed_probe",
            rule_id="no-future-annotations-tree-only-probe",
            hit_fixtures=[
                {
                    "relative-path": "packages/sample/bad.py",
                    "source": "from __future__ import annotations\n",
                }
            ],
            clean_fixtures=[
                {
                    "relative-path": "packages/sample/clean.py",
                    "source": "import json\n",
                }
            ],
        )
    )

    _check("tree_only_installed_probe")


def _fake_semgrep(tmp_path: Path, script_body: str) -> Path:
    path = tmp_path / "fake-semgrep"
    path.write_text("#!/bin/sh\n" + script_body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def test_run_semgrep_crash_with_exit_1_is_loud_not_empty(tmp_path):
    """If the scanner crashes with exit 1 (stdout is not JSON) -> AssertionError, not an empty result.

    Exit 1 also overlaps with semgrep's "findings present", so only the existence of JSON
    is evidence that "the scan ran" -- a scan that did not run is never used as green-side material.
    """
    fake = _fake_semgrep(
        tmp_path,
        'echo "Traceback (most recent call last): interpreter mismatch" >&2\nexit 1\n',
    )
    config = tmp_path / "rule.json"
    config.write_text(json.dumps({"rules": []}), encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("anything\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="interpreter mismatch"):
        _run_semgrep(str(fake), config, [target], project_root=tmp_path)


def test_run_semgrep_empty_stdout_with_exit_0_is_loud(tmp_path):
    fake = _fake_semgrep(tmp_path, "exit 0\n")
    config = tmp_path / "rule.json"
    config.write_text(json.dumps({"rules": []}), encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("anything\n", encoding="utf-8")

    with pytest.raises(AssertionError, match="JSON"):
        _run_semgrep(str(fake), config, [target], project_root=tmp_path)


def test_run_semgrep_failure_names_the_json_errors_not_only_stderr(tmp_path: Path) -> None:
    """With --quiet --json, semgrep puts the reason for a failure (exit 2) only in the stdout JSON errors, and stderr is empty.

    Counterexample: the daily check left only "semgrep failed with exit 2:" and could not name whether it was a rule-loading failure
    or the semgrep version. The failure message names the error kind, rule and wording from errors.
    """
    verdict = {
        "results": [],
        "errors": [
            {
                "code": 7,
                "level": "error",
                "type": "InvalidRuleSchemaError",
                "rule_id": "adr0003-alive-o1-guard",
                "message": "Invalid rule schema: pattern-not-inside is not a valid key",
            }
        ],
    }
    fake = _fake_semgrep(tmp_path, f"cat <<'JSON'\n{json.dumps(verdict)}\nJSON\nexit 2\n")
    config = tmp_path / "rule.json"
    config.write_text(json.dumps({"rules": []}), encoding="utf-8")
    target = tmp_path / "target.txt"
    target.write_text("anything\n", encoding="utf-8")

    with pytest.raises(AssertionError) as caught:
        _run_semgrep(str(fake), config, [target], project_root=tmp_path)
    text = str(caught.value)
    assert "exit 2" in text
    assert "InvalidRuleSchemaError" in text
    assert "adr0003-alive-o1-guard" in text
    assert "pattern-not-inside is not a valid key" in text


def test_crashing_scanner_is_not_reported_as_rule_not_firing(tmp_path, monkeypatch, registry):
    """Replay of the node-h measurement: a crashing semgrep is not reported as "mismatch on the bad fixture"."""
    fake = _fake_semgrep(
        tmp_path,
        'echo "Traceback (most recent call last): scanner is broken" >&2\nexit 1\n',
    )
    _register(
        RegisterSemgrep(
            "crash_is_not_a_clean_scan",
            pattern="forbidden-token",
            bad=["forbidden-token"],
            good=["allowed-token"],
        )
    )
    import doeff_adr._registry as registry_module

    monkeypatch.setattr(registry_module.shutil, "which", lambda _name: str(fake))

    with pytest.raises(AssertionError) as excinfo:
        _check("crash_is_not_a_clean_scan")

    assert "did not match any bad fixture" not in str(excinfo.value)
    assert "scanner is broken" in str(excinfo.value)
