"""ADR-DOE-ENFORCE-001 R3: run all .semgrep.yaml rules in the default pytest gate.

An aggregate gate that launches from pytest the same scan as `make lint-semgrep` (--config .semgrep.yaml doeff/ packages/).
Differences from the Makefile version:
1. fail-closed -- a missing semgrep binary is a hard fail, not a skip (no false green -- prevents the failure mode in ACP where
   "16 checks went red immediately on the first run (binary missing)").
2. baseline equality ratchet -- if the violation count "increases over the baseline it fails (new violations); if it decreases it
   fails (forcing the bookkeeping that lowers the baseline)". Existing violations were resolved on 2026-07-14, and the current
   docs/adr/semgrep-baseline.json stays at 0.

Turning individual rules into hit/clean fixtures (defsemgrep installed-rule form) and the batch resolving existing violations
are the remaining work of T-B2 (delegated to codex) -- in the meantime this gate guarantees that the 229 rules actually run and
do not regress.
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import cast

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "docs" / "adr" / "semgrep-baseline.json"


def test_semgrep_is_declared_as_bounded_dev_dependency() -> None:
    config: dict[str, object] = tomllib.loads((ROOT / "pyproject.toml").read_text())
    dependency_groups: dict[str, list[str]] = cast(
        dict[str, list[str]], config["dependency-groups"]
    )

    assert "semgrep>=1.161.0,<2" in dependency_groups["dev"]


def test_makefile_semgrep_targets_use_project_environment() -> None:
    makefile: str = (ROOT / "Makefile").read_text()

    assert "command -v semgrep" not in makefile
    assert makefile.count("uv run semgrep") == 3


def test_missing_semgrep_points_to_make_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_binary(_name: str) -> None:
        return None

    monkeypatch.setattr(shutil, "which", missing_binary)

    with pytest.raises(AssertionError, match="make sync"):
        test_semgrep_findings_match_baseline_ratchet(None)


# Scans about 2,400 files under doeff/ and packages/ once with all rules, the heaviest single test at the root -- the ini default budget (60 seconds) cannot be
# assumed (2026-10-16: it went red exceeding the budget at 75 seconds in a full run with congestion factor x2.3 and 204 seconds in a full run with x3.4). The budget is
# declared at this weight, and the root conftest applies the congestion factor (ADR-DOE-ENFORCE-001 R6). The child semgrep is also stopped by the same budget.
@pytest.mark.semgrep
@pytest.mark.slow
@pytest.mark.timeout(180)
def test_semgrep_findings_match_baseline_ratchet(per_test_deadline: float | None) -> None:
    binary = shutil.which("semgrep")
    assert binary is not None, (
        "semgrep binary not found -- ADR-DOE-ENFORCE-001 R3 forbids skipping. "
        "semgrep is included in the dev dependencies, so run `make sync`."
    )
    proc = subprocess.run(
        # --metrics=off --disable-version-check: do not mix network access (sending, version lookup) into
        # the check run -- cut off machine circumstances such as reachability and response time from entering the check.
        # --project-root=ROOT: do not leave the root to inference from git metadata -- in a
        # check tree without .git the root falls to the scanned dir, and rules whose
        # paths.include refers to upper segments silently drop out of the gate (measured on node-h 2026-08-17).
        [
            binary,
            "--metrics=off",
            "--disable-version-check",
            "--project-root",
            str(ROOT),
            "--config",
            ".semgrep.yaml",
            "doeff/",
            "packages/",
            "--error",
            "--quiet",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=per_test_deadline,
        check=False,  # violations are judged by the number of results, not returncode (baseline ratchet)
    )
    # Since the exit code is not used for judging, the existence of the JSON is the only evidence that "the scan ran".
    # Do not turn a startup crash (exit 1, empty stdout) into a bare JSONDecodeError (measured on node-h
    # 2026-08-17: a crash from an interpreter mismatch was reported as an unreadable red).
    try:
        results = json.loads(proc.stdout)["results"]
    except ValueError as exc:
        raise AssertionError(
            f"semgrep produced no JSON verdict (exit {proc.returncode}) — "
            f"the scan did not run; stderr:\n{proc.stderr}"
        ) from exc
    baseline = json.loads(BASELINE.read_text())["findings"]
    actual = len(results)
    if actual > baseline:
        newest = [f"{r['check_id']} {r['path']}:{r['start']['line']}" for r in results][-10:]
        raise AssertionError(
            f"semgrep violations exceed the baseline: {actual} > {baseline} -- fix the new violations "
            f"(ADR-DOE-ENFORCE-001 R3). Recent examples:\n" + "\n".join(newest)
        )
    assert actual == baseline, (
        f"semgrep violation count {actual} < baseline {baseline} -- record the improvement: "
        f"lower findings in {BASELINE} to {actual} (prevents silently loosening the standard)."
    )
