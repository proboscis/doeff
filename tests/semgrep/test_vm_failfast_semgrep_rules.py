from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

pytestmark = pytest.mark.semgrep

REPO_ROOT = Path(__file__).resolve().parents[2]


def _semgrep_results(
    config: Path, target: str, *, cwd: Path, project_root: Path = REPO_ROOT
) -> list[dict[str, Any]]:
    semgrep_bin = shutil.which("semgrep")
    if semgrep_bin is None:
        pytest.skip("semgrep is not installed")

    completed = subprocess.run(
        # --metrics=off --disable-version-check: do not mix the network (sending, querying for a new version)
        # into the scan -- cut off machine circumstances such as reachability and response time from entering the scan.
        # --project-root=project_root (default REPO_ROOT; only tests that look at the rule's paths in a temporary tree pass another root): do not leave root to inference from git metadata -- in a scan tree
        # with no .git, root falls to the scanned dir itself, and only
        # paths.include that refers to segments above the target silently dies (zeus measurement 2026-08-17). root is the tree's root, not cwd
        # (the fixture dir) -- the rule side's include of the fixtures copy
        # (/tests/semgrep/fixtures/...) is written relative to the repo root.
        [
            semgrep_bin,
            "--metrics=off",
            "--disable-version-check",
            "--project-root",
            str(project_root),
            "--no-git-ignore",
            "--config",
            str(config),
            "--json",
            target,
        ],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode not in {0, 1}:
        raise AssertionError(
            f"semgrep failed:\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )

    # exit 1 is returned by both "has findings" and "crash at startup" -- only when the JSON could be read
    # can we say the scan ran (zeus measurement 2026-08-17: a crash from an interpreter mismatch
    # returned exit 1 + empty stdout and turned into a bare JSONDecodeError traceback).
    try:
        payload = json.loads(completed.stdout)
    except ValueError as exc:
        raise AssertionError(
            f"semgrep produced no JSON verdict (exit {completed.returncode}) — "
            f"the scan did not run; stderr:\n{completed.stderr}"
        ) from exc
    return cast(list[dict[str, Any]], payload.get("results", []))


def _semgrep_rule_ids(config: Path, target: str, *, cwd: Path) -> set[str]:
    return {str(result["check_id"]) for result in _semgrep_results(config, target, cwd=cwd)}


def _has_rule(check_ids: set[str], expected_rule_id: str) -> bool:
    suffix = f".{expected_rule_id}"
    return any(check_id == expected_rule_id or check_id.endswith(suffix) for check_id in check_ids)


def _rule_start_lines(results: list[dict[str, Any]], expected_rule_id: str) -> set[int]:
    lines: set[int] = set()
    for result in results:
        check_id = str(result["check_id"])
        if not _has_rule({check_id}, expected_rule_id):
            continue
        start = cast(dict[str, int], result["start"])
        lines.add(start["line"])
    return lines


def test_vm_failfast_rust_rules_detect_known_bad_examples() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/rust"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / "packages/doeff-vm/.semgrep.yaml",
        "packages",
        cwd=fixture_root,
    )

    expected = {
        "no-bare-ok-in-handler-can-handle",
        "no-silent-if-let-current-segment",
        "no-bare-ok-in-traceback-build",
        "doeff-vm-no-dispatch-id-runtime",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected)


def test_vm_failfast_python_rules_detect_known_bad_examples() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    check_ids = _semgrep_rule_ids(
        REPO_ROOT / ".semgrep.yaml",
        "doeff",
        cwd=fixture_root,
    )

    expected = {
        "no-silent-except-in-traceback",
        "no-silent-except-return-none",
    }
    assert all(_has_rule(check_ids, rule_id) for rule_id in expected)


def test_withhandler_return_clause_rule_detects_external_legacy_calls() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "doeff/withhandler_return_clause_sample.py",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-withhandler-no-return-clause") == {20, 21, 22, 23}


def test_public_withhandler_rule_detects_legacy_hy_import_and_calls() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "doeff/public_withhandler_sample.hy",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-no-public-withhandler-shim") == {1}


def test_program_arguments_rule_detects_a_second_config_to_arguments_copy() -> None:
    # Design check 2026-09-26 (request 2, blind A): three
    # copies turned run.config into program arguments and only two of them
    # dropped the assembly field "record", so the single-process main and the
    # worker disagreed on the same declaration.
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-cluster/src/doeff_cluster/program_arguments_copy_forbidden.hy",
        cwd=fixture_root,
    )

    assert _rule_start_lines(
        results, "doeff-cluster-program-arguments-are-built-in-one-place"
    ) == {8, 12}


def test_report_tables_rule_detects_a_reader_outside_the_owner() -> None:
    # Design check 2026-10-11 (AL-D2o, blind B): a copy in
    # handoff_policy that picked "Ready" reports by worker・spec hash・placement
    # only held off a handoff abandonment with reports from processes other than
    # the placed one, and passed every presented check.
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-cluster/src/doeff_cluster/_coordinator/core/"
        "report_table_read_outside_owner_forbidden.hy",
        cwd=fixture_root,
    )

    assert _rule_start_lines(
        results, "doeff-cluster-report-tables-are-read-by-their-owner"
    ) == {9, 13}


def test_report_tables_are_read_only_by_their_owners_in_the_tree() -> None:
    # The same rule over the real package: only resource_policy.hy and
    # metrics_policy.hy read ClusterState.observations.readiness / .metrics.
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-cluster/src",
        cwd=REPO_ROOT,
    )

    readers = sorted(
        f"{result['path']}:{cast(dict[str, int], result['start'])['line']}"
        for result in results
        if _has_rule({str(result["check_id"])}, "doeff-cluster-report-tables-are-read-by-their-owner")
    )
    assert readers == []


def test_silence_anchor_rule_detects_reads_outside_the_one_measure() -> None:
    # The worker's fence against a lost coordinator measured
    # silence by the wall-clock difference alone, so a wall clock stepped back
    # during the cut delayed the self-stop past the coordinator's reassignment.
    # The anchors are read only as the 3rd and 4th argument of
    # heartbeat_rules.silent-ms (same owner, wall then monotonic) and written as a pair.
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/"
        "silence_anchor_read_outside_measure_forbidden.hy",
        cwd=fixture_root,
    )

    assert _rule_start_lines(
        results, "doeff-cluster-worker-silence-anchors-are-read-by-the-one-measure"
    ) == {9, 13, 17, 20, 23, 26, 29, 32, 35, 38, 41}


def test_silence_anchors_are_read_only_by_the_one_measure_in_the_tree() -> None:
    # The same rule over the real package: the production link and the sim host
    # hand their anchors to silent-ms and write them as a pair.
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-cluster/src",
        cwd=REPO_ROOT,
    )

    readers = sorted(
        f"{result['path']}:{cast(dict[str, int], result['start'])['line']}"
        for result in results
        if _has_rule(
            {str(result["check_id"])},
            "doeff-cluster-worker-silence-anchors-are-read-by-the-one-measure",
        )
    )
    assert readers == []


def test_clock_effect_rule_detects_answers_outside_the_time_handlers() -> None:
    # Design check (blind B): a
    # handler at the worker entry that rebuilt the wall clock from the monotonic
    # clock passed every presented check, and the worker no longer decided its
    # self-stop after a 30 second suspend (the wall difference stopped counting sleep).
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/"
        "clock_effect_answered_outside_time_handlers_forbidden.hy",
        cwd=fixture_root,
    )

    assert _rule_start_lines(
        results, "doeff-cluster-clock-effects-are-answered-by-the-default-time-handlers"
    ) == {10, 15, 20, 21}


def test_clock_effects_are_answered_only_by_the_time_handlers_in_the_tree() -> None:
    # The same rule over the real package: only doeff-time's handlers (called
    # without clock sources) and the sim's machine wall clock answer GetTime / GetMonotonic.
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-cluster/src",
        cwd=REPO_ROOT,
    )

    answers = sorted(
        f"{result['path']}:{cast(dict[str, int], result['start'])['line']}"
        for result in results
        if _has_rule(
            {str(result["check_id"])},
            "doeff-cluster-clock-effects-are-answered-by-the-default-time-handlers",
        )
    )
    assert answers == []


def test_defhandler_must_be_top_level_rule_detects_nested_handler() -> None:
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-handler-sample/src/doeff_handler_sample/nested_defhandler_forbidden.hy",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-hy-defhandler-must-be-top-level") == {4}


def test_gc_traverse_null_guard_rule_reaches_nested_src_directories() -> None:
    """The traverse null-guard ban must cover every .rs under both VM crates' src/.

    doeff-vm-core keeps most of the VM under src/vm/, so a `paths.include` written
    as `src/*.rs` reads as if the crate were covered while a `visit.call(` added one
    directory down passes lint silently. The defect this rule guards
    (agora-1 pod `ai usage` rc=-11, 2026-08-26) is a SIGSEGV, so a silent hole in
    the population is the whole failure mode.
    """
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/rust"
    results = _semgrep_results(REPO_ROOT / ".semgrep.yaml", "packages", cwd=fixture_root)
    rule_id = "doeff-vm-traverse-must-null-guard-py-fields"
    hit = {
        str(result["path"]) for result in results if _has_rule({str(result["check_id"])}, rule_id)
    }
    assert hit == {
        "packages/doeff-vm/src/gc_traverse_unguarded.rs",
        "packages/doeff-vm-core/src/vm/gc_traverse_unguarded.rs",
    }


def test_application_vocabulary_rule_fires_on_the_words_and_spares_the_records_field() -> None:
    # Turned the copies of tests/test_no_application_vocabulary.hy of the 3 packages into a single rule. Words match case-
    # insensitively, and only when not preceded by a letter (_acp matches, xacp does not). size-budget (the name of a declaration field of doeff-records) is excluded.
    # The shape that turned doeff's main red twice on 2026-10-02 (a repository name written in a note) also matches.
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-records/vocabulary/vocabulary_forbidden.md",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-packages-have-no-application-vocabulary") == {3, 4, 5, 6, 7}


def test_application_vocabulary_rule_reads_type_stubs_too() -> None:
    # Of the copies, doeff-cluster's test did not look at .pyi -- the rule also looks at the .pyi of the 3 packages (names in type declarations also carry no business words).
    fixture_root = REPO_ROOT / "tests/semgrep/fixtures/python"
    results = _semgrep_results(
        REPO_ROOT / ".semgrep.yaml",
        "packages/doeff-cluster/vocabulary/vocabulary_forbidden.pyi",
        cwd=fixture_root,
    )

    assert _rule_start_lines(results, "doeff-packages-have-no-application-vocabulary") == {4}
