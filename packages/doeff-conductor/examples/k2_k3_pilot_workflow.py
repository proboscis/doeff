"""C7 k2-k3 pilot workflow ported to the conductor DSL shape."""

from __future__ import annotations

import json
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doeff_conductor.dsl import (
    agent_bang,
    artifact,
    bind,
    defphase,
    defworkflow,
    gate_bang,
    loop,
    merge_bang,
    parallel,
    prompt,
    ref,
    workspace_bang,
)
from doeff_conductor.effects import (
    CALIBRATION_SAMPLE_BUDGET_KEY,
    REVIEW_VERDICT_RESULT_SCHEMA,
    TIER1_REVIEW_BUDGET_KEY,
    TIER2_ESCALATION_BUDGET_KEY,
    Agent,
    AgentTask,
    Commit,
    CreateWorkspace,
    DurableReviewBudget,
    Exec,
    MergeWorkspaces,
    ReviewItem,
    ReviewRoutingResult,
    ReviewStakes,
    ReviewStakesLevel,
    ReviewVerdictArtifact,
    route_review_item,
)
from doeff_conductor.types import ExecResult, MergeWorkspacesResult, Workspace

from doeff import EffectGenerator, Gather, Spawn, do

IMPLEMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary", "changedFiles"],
    "properties": {
        "summary": {"type": "string"},
        "changedFiles": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
        "openQuestions": {"type": "string"},
    },
    "additionalProperties": False,
}

GATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["buildOk", "testOk", "lintOk", "summary"],
    "properties": {
        "buildOk": {"type": "boolean"},
        "testOk": {"type": "boolean"},
        "lintOk": {"type": "boolean"},
        "summary": {"type": "string"},
        "failures": {"type": "string"},
        "changedFiles": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


@dataclass(frozen=True)
class PilotTask:
    """Static task descriptor shared by the DSL shape and production main."""

    node_id: str
    label: str
    suffix: str
    files: frozenset[str]
    prompt_text: str


IMPLEMENTER_TASKS: tuple[PilotTask, ...] = (
    PilotTask(
        node_id="impl-a-failure-kind",
        label="impl:A-failure-kind",
        suffix="impl-a",
        files=frozenset({"src/pilot_pkg/failure_kind.py"}),
        prompt_text=(
            "Task A: improve src/pilot_pkg/failure_kind.py only. Keep the conservative "
            "unknown-kind behavior, add a short module-level note that this mirrors the "
            "k2-k3 validation_failed lane, and return the required JSON artifact."
        ),
    ),
    PilotTask(
        node_id="impl-b-router",
        label="impl:B-router",
        suffix="impl-b",
        files=frozenset({"src/pilot_pkg/router.py"}),
        prompt_text=(
            "Task B: improve src/pilot_pkg/router.py only. Ensure both "
            "merge-agent-not-merged:* and merge-agent-validation-failed:* reasons route "
            "validation_failed to investigate and transient/stale kinds to retry."
        ),
    ),
    PilotTask(
        node_id="impl-c-gates",
        label="impl:C-gates",
        suffix="impl-c",
        files=frozenset({"src/pilot_pkg/gates.py"}),
        prompt_text=(
            "Task C: improve src/pilot_pkg/gates.py only. Keep the option ids exactly "
            "['rebase', 'fresh', 're-observe', 'cancel'] and document that each option "
            "closes over both the PR and the owning issue."
        ),
    ),
    PilotTask(
        node_id="impl-d-investigation",
        label="impl:D-investigation",
        suffix="impl-d",
        files=frozenset({"src/pilot_pkg/investigation.py"}),
        prompt_text=(
            "Task D: improve src/pilot_pkg/investigation.py only. Keep pr-code, mainline, "
            "control-plane-core, and transient-observation classifications explicit."
        ),
    ),
    PilotTask(
        node_id="impl-e-ownership",
        label="impl:E-ownership",
        suffix="impl-e",
        files=frozenset({"src/pilot_pkg/ownership.py"}),
        prompt_text=(
            "Task E: improve src/pilot_pkg/ownership.py only. Keep "
            "MergeValidationInvestigated owned by review-reconciler and add a small helper "
            "if useful. Do not edit any other file."
        ),
    ),
)

TEST_WRITER_TASKS: tuple[PilotTask, ...] = (
    PilotTask(
        node_id="test-routing",
        label="test:routing",
        suffix="tests-routing",
        files=frozenset({"tests/test_routing.py"}),
        prompt_text=(
            "Write tests/test_routing.py only. Cover validation_failed routing to "
            "investigate, agent_error routing to retry, and unknown legacy suffixes routing "
            "to investigate. Use unittest.TestCase so python -m unittest discover runs them."
        ),
    ),
    PilotTask(
        node_id="test-ownership",
        label="test:ownership",
        suffix="tests-ownership",
        files=frozenset({"tests/test_ownership.py"}),
        prompt_text=(
            "Write tests/test_ownership.py only. Cover the MergeValidationInvestigated "
            "owner and the merge exhausted gate option ids. Use unittest or "
            "unittest.TestCase so python -m unittest discover runs them."
        ),
    ),
)

REVIEW_AXES: tuple[tuple[str, str], ...] = (
    (
        "review-routing-closure",
        "Review route_failure and gate options for closure-law violations. PASS if no issue.",
    ),
    (
        "review-tests",
        "Review tests for coverage of validation_failed, agent_error, and ownership.",
    ),
    (
        "review-ownership",
        "Review CONDITION_OWNER consistency and single-writer assumptions.",
    ),
    (
        "review-known-blocker",
        "If docs/known_blocker.md exists, report one BLOCKER finding for that file.",
    ),
)

BUILD_COMMAND = "PYTHONPATH=src python3 tools/build_check.py"
TEST_COMMAND = "PYTHONPATH=src python3 -m unittest discover -s tests"
LINT_COMMAND = "PYTHONPATH=src python3 tools/lint_check.py"
FULL_GATE_COMMAND = f"{BUILD_COMMAND} && {TEST_COMMAND} && {LINT_COMMAND}"


def build_workflow():
    """Return the C7 DSL workflow shape used by plan/validate."""
    base_workspace = workspace_bang(from_="main")
    impl_workspaces = tuple(workspace_bang(from_=f"main-{task.suffix}") for task in IMPLEMENTER_TASKS)
    test_workspace = workspace_bang(from_="main-tests")
    gate_workspace = workspace_bang(from_="main-gate")

    return defworkflow(
        "k2_k3_pilot",
        params={"base_ref": str, "run_id": str},
        roles={
            "implementer": {"profile": "cheap-coder", "retry": 2},
            "fixer": {"profile": "cheap-coder", "retry": 2},
            "test-writer": {"profile": "cheap-coder", "retry": 2},
            "reviewer": {"profile": "cheap-reviewer", "retry": 1},
        },
        body=[
            defphase(
                "Implement",
                stakes="normal",
                body=[
                    bind(
                        "impls",
                        parallel(
                            *[
                                agent_bang(
                                    role="implementer",
                                    verification_class="test-verifiable",
                                    prompt=prompt(task.prompt_text),
                                    schema=IMPLEMENT_SCHEMA,
                                    workspace=impl_workspaces[index],
                                    files=task.files,
                                    label=task.label,
                                )
                                for index, task in enumerate(IMPLEMENTER_TASKS)
                            ]
                        ),
                    )
                ],
            ),
            defphase(
                "Reconcile",
                stakes="high",
                body=[
                    bind(
                        "reconciled",
                        loop(
                            max=3,
                            until="build_gate_passed",
                            body=[
                                bind("build_gate", gate_bang(cmd=BUILD_COMMAND, workspace=base_workspace)),
                                agent_bang(
                                    role="fixer",
                                    verification_class="test-verifiable",
                                    prompt=prompt(
                                        "fix compile/build failures after ",
                                        ref("impls"),
                                        " gate=",
                                        ref("build_gate"),
                                    ),
                                    schema=GATE_SCHEMA,
                                    workspace=base_workspace,
                                    files=frozenset({"src/pilot_pkg"}),
                                    label="fix:compile",
                                ),
                            ],
                        ),
                    )
                ],
            ),
            defphase(
                "Tests",
                stakes="normal",
                body=[
                    bind(
                        "tests",
                        parallel(
                            *[
                                agent_bang(
                                    role="test-writer",
                                    verification_class="test-verifiable",
                                    prompt=prompt(task.prompt_text, " after ", ref("reconciled")),
                                    schema=IMPLEMENT_SCHEMA,
                                    workspace=test_workspace,
                                    files=task.files,
                                    label=task.label,
                                )
                                for task in TEST_WRITER_TASKS
                            ]
                        ),
                    )
                ],
            ),
            defphase(
                "Gate",
                stakes="high",
                body=[
                    bind(
                        "gated",
                        loop(
                            max=3,
                            until="full_gate_passed",
                            body=[
                                bind(
                                    "full_gate",
                                    gate_bang(cmd=FULL_GATE_COMMAND, workspace=gate_workspace),
                                ),
                                agent_bang(
                                    role="fixer",
                                    verification_class="test-verifiable",
                                    prompt=prompt(
                                        "fix gate failures after ",
                                        ref("tests"),
                                        " gate=",
                                        ref("full_gate"),
                                    ),
                                    schema=GATE_SCHEMA,
                                    workspace=gate_workspace,
                                    files=frozenset({"src/pilot_pkg/lint_sentinel.py"}),
                                    label="fix:gate",
                                ),
                            ],
                        ),
                    )
                ],
            ),
            defphase(
                "Review",
                stakes="high",
                body=[
                    bind(
                        "reviews",
                        parallel(
                            *[
                                agent_bang(
                                    role="reviewer",
                                    verification_class="semantic",
                                    prompt=prompt(axis_prompt, " gated=", ref("gated")),
                                    schema=REVIEW_VERDICT_RESULT_SCHEMA,
                                    workspace=gate_workspace,
                                    files=frozenset({f"review/{axis}.md"}),
                                    label=axis,
                                )
                                for axis, axis_prompt in REVIEW_AXES
                            ]
                        ),
                    ),
                    bind(
                        "merged",
                        merge_bang(
                            workspaces=[
                                base_workspace,
                                test_workspace,
                                gate_workspace,
                                *impl_workspaces,
                            ],
                            strategy="merge",
                        ),
                    ),
                    artifact(prompt(ref("reviews"), ref("merged"))),
                ],
            ),
        ],
    )


WORKFLOW = build_workflow()


@do
def _create_workspace(*, from_ref: str, suffix: str) -> EffectGenerator[Workspace]:
    return (yield CreateWorkspace(from_ref=from_ref, suffix=suffix))


@do
def _run_agent(
    *,
    run_id: str,
    node_id: str,
    workspace: Workspace,
    prompt_text: str,
    schema: dict[str, Any],
    verification_class: str,
    model: str | None,
    effort: str | None,
    max_retries: int,
) -> EffectGenerator[dict[str, Any]]:
    result = yield Agent(
        AgentTask(
            run_id=run_id,
            node_id=node_id,
            attempt=0,
            env=workspace,
            prompt=prompt_text,
            result_schema=schema,
            verification_class=verification_class,
            agent_type="codex",
            model=model,
            effort=effort,
            max_retries=max_retries,
        )
    )
    if not isinstance(result, dict):
        raise TypeError(f"agent {node_id} returned non-object result")
    return result


@do
def _commit_agent_result(
    *,
    workspace: Workspace,
    node_id: str,
    message: str,
) -> EffectGenerator[str]:
    safe_node_id: str = node_id.replace("/", "-")
    marker_path: str = f".conductor/{safe_node_id}.txt"
    marker_text: str = shlex.quote(f"{node_id} completed\n")
    cmd: str = f"mkdir -p .conductor && printf %s {marker_text} > {shlex.quote(marker_path)}"
    marker_result: ExecResult = yield Exec(cmd=cmd, workspace=workspace)
    if not marker_result.passed:
        raise RuntimeError(f"failed to write conductor marker for {node_id}")
    return (yield Commit(workspace=workspace, message=message))


@do
def _run_gate(
    *,
    workspace: Workspace,
    cmd: str,
    timeout: float | None = 120.0,
) -> EffectGenerator[ExecResult]:
    return (yield Exec(cmd=cmd, workspace=workspace, timeout=timeout))


@do
def _merge_or_fail(
    *,
    workspaces: tuple[Workspace, ...],
    name: str,
) -> EffectGenerator[Workspace]:
    merge_result: MergeWorkspacesResult = yield MergeWorkspaces(workspaces=workspaces, name=name)
    if not merge_result.merged or merge_result.workspace is None:
        raise RuntimeError(f"workspace merge failed: {merge_result.message}")
    return merge_result.workspace


def _review_prompt(axis: str, axis_prompt: str) -> str:
    return (
        f"{axis_prompt}\n"
        "Read only the scratch repository. Return JSON matching the review verdict schema. "
        "Use verdict PASS with findings=[] when the axis is clean. "
        "For review-known-blocker, report CHANGES_REQUESTED with exactly one BLOCKER finding "
        "for docs/known_blocker.md when that file exists."
    )


def _review_artifacts_to_routing(
    review_artifacts: tuple[dict[str, Any], ...],
) -> tuple[dict[str, Any], ...]:
    budget: DurableReviewBudget = DurableReviewBudget.from_limits(
        {
            TIER1_REVIEW_BUDGET_KEY: len(review_artifacts),
            TIER2_ESCALATION_BUDGET_KEY: len(review_artifacts),
            CALIBRATION_SAMPLE_BUDGET_KEY: 0,
        }
    )
    routed: list[dict[str, Any]] = []
    tier2_requests: list[object] = []
    for index, artifact_payload in enumerate(review_artifacts):
        artifact = ReviewVerdictArtifact.from_dict(artifact_payload)
        item = ReviewItem(
            item_id=f"review-{index + 1}",
            lane="k2-k3-pilot",
            stakes=ReviewStakes(
                verification_class="semantic",
                blast_radius="scratch-repository",
                reversibility="recreate-scratch-repo",
                level=ReviewStakesLevel.HIGH,
            ),
        )
        result: ReviewRoutingResult = route_review_item(
            item=item,
            tier1_results=(artifact,),
            budget=budget,
            tier2_callback=tier2_requests.append,
        )
        budget = result.budget
        routed.append(
            {
                "item": item.item_id,
                "terminal": type(result.terminal).__name__,
                "verdict": artifact.verdict.value,
                "findingSeverities": [
                    finding.severity.value for finding in artifact.findings
                ],
            }
        )
    tier1_terminal_count: int = sum(
        1 for item in routed if item["terminal"] == "ReviewVerdictTerminal"
    )
    routed.append(
        {
            "summary": "tier-routing",
            "tier1Terminated": tier1_terminal_count,
            "totalReviews": len(review_artifacts),
            "tier2Callbacks": len(tier2_requests),
            "tier1Fraction": tier1_terminal_count / len(review_artifacts)
            if review_artifacts
            else 0.0,
        }
    )
    return tuple(routed)


@do
def main(  # noqa: PLR0912, PLR0915
    *,
    run_id: str = "c7-k2-k3-pilot",
    base_ref: str = "main",
    model: str | None = None,
    effort: str | None = "low",
    crash_after_phase: str | None = None,
    report_path: str | None = None,
) -> EffectGenerator[dict[str, Any]]:
    phase_seconds: dict[str, float] = {}
    phase_started_at: float = time.perf_counter()

    workspace_tasks: list[object] = []
    for task in IMPLEMENTER_TASKS:
        workspace_tasks.append(
            (yield Spawn(_create_workspace(from_ref=base_ref, suffix=task.suffix)))
        )
    impl_workspaces = yield Gather(*workspace_tasks)

    agent_tasks: list[object] = []
    for index, task in enumerate(IMPLEMENTER_TASKS):
        workspace: Workspace = impl_workspaces[index]
        agent_tasks.append(
            (
                yield Spawn(
                    _run_agent(
                        run_id=run_id,
                        node_id=task.node_id,
                        workspace=workspace,
                        prompt_text=task.prompt_text,
                        schema=IMPLEMENT_SCHEMA,
                        verification_class="test-verifiable",
                        model=model,
                        effort=effort,
                        max_retries=2,
                    )
                )
            )
        )
    implementer_results = yield Gather(*agent_tasks)
    for index, task in enumerate(IMPLEMENTER_TASKS):
        workspace = impl_workspaces[index]
        yield _commit_agent_result(
            workspace=workspace,
            node_id=task.node_id,
            message=f"impl: {task.label}",
        )
    phase_seconds["Implement"] = time.perf_counter() - phase_started_at
    if crash_after_phase == "Implement":
        raise RuntimeError("simulated C7 crash after Implement")

    phase_started_at = time.perf_counter()
    reconciled_workspace = yield _merge_or_fail(
        workspaces=tuple(impl_workspaces),
        name=f"{run_id}-reconciled",
    )
    build_gate_logs: list[str] = []
    for round_number in range(1, 4):
        build_gate: ExecResult = yield _run_gate(workspace=reconciled_workspace, cmd=BUILD_COMMAND)
        build_gate_logs.append(build_gate.log_path)
        if build_gate.passed:
            break
        fixer_result = yield _run_agent(
            run_id=run_id,
            node_id="fix-compile",
            workspace=reconciled_workspace,
            prompt_text=(
                f"Build gate round {round_number} failed. Run {BUILD_COMMAND}, inspect the "
                "full log, and fix only mechanical Python build issues. Return JSON with "
                "buildOk/testOk/lintOk booleans."
            ),
            schema=GATE_SCHEMA,
            verification_class="test-verifiable",
            model=model,
            effort=effort,
            max_retries=2,
        )
        _ = fixer_result
        yield _commit_agent_result(
            workspace=reconciled_workspace,
            node_id=f"fix-compile-round-{round_number}",
            message=f"fix: compile round {round_number}",
        )
    else:
        raise RuntimeError("build gate did not pass within 3 rounds")
    phase_seconds["Reconcile"] = time.perf_counter() - phase_started_at

    phase_started_at = time.perf_counter()
    test_workspace_tasks: list[object] = []
    for task in TEST_WRITER_TASKS:
        test_workspace_tasks.append(
            (
                yield Spawn(
                    _create_workspace(
                        from_ref=reconciled_workspace.ref,
                        suffix=task.suffix,
                    )
                )
            )
        )
    test_workspaces = yield Gather(*test_workspace_tasks)
    test_agent_tasks: list[object] = []
    for index, task in enumerate(TEST_WRITER_TASKS):
        workspace = test_workspaces[index]
        test_agent_tasks.append(
            (
                yield Spawn(
                    _run_agent(
                        run_id=run_id,
                        node_id=task.node_id,
                        workspace=workspace,
                        prompt_text=task.prompt_text,
                        schema=IMPLEMENT_SCHEMA,
                        verification_class="test-verifiable",
                        model=model,
                        effort=effort,
                        max_retries=2,
                    )
                )
            )
        )
    test_writer_results = yield Gather(*test_agent_tasks)
    for index, task in enumerate(TEST_WRITER_TASKS):
        workspace = test_workspaces[index]
        yield _commit_agent_result(
            workspace=workspace,
            node_id=task.node_id,
            message=f"test: {task.label}",
        )
    phase_seconds["Tests"] = time.perf_counter() - phase_started_at

    phase_started_at = time.perf_counter()
    gated_workspace = yield _merge_or_fail(
        workspaces=(reconciled_workspace, *test_workspaces),
        name=f"{run_id}-gated",
    )
    full_gate_logs: list[str] = []
    gate_rounds: int = 0
    for round_number in range(1, 4):
        gate_rounds = round_number
        full_gate: ExecResult = yield _run_gate(workspace=gated_workspace, cmd=FULL_GATE_COMMAND)
        full_gate_logs.append(full_gate.log_path)
        if full_gate.passed:
            break
        gate_fixer_result = yield _run_agent(
            run_id=run_id,
            node_id="fix-gate",
            workspace=gated_workspace,
            prompt_text=(
                f"Full gate round {round_number} failed. Run {FULL_GATE_COMMAND}. "
                "The lint gate may report C7_BLOCKER_LINT_SENTINEL in "
                "src/pilot_pkg/lint_sentinel.py; remove that sentinel or delete the file, "
                "then rerun the gate. Return JSON with buildOk/testOk/lintOk booleans."
            ),
            schema=GATE_SCHEMA,
            verification_class="test-verifiable",
            model=model,
            effort=effort,
            max_retries=2,
        )
        _ = gate_fixer_result
        yield _commit_agent_result(
            workspace=gated_workspace,
            node_id=f"fix-gate-round-{round_number}",
            message=f"fix: gate round {round_number}",
        )
    else:
        raise RuntimeError("full gate did not pass within 3 rounds")
    phase_seconds["Gate"] = time.perf_counter() - phase_started_at

    phase_started_at = time.perf_counter()
    review_tasks: list[object] = []
    for axis, axis_prompt in REVIEW_AXES:
        review_tasks.append(
            (
                yield Spawn(
                    _run_agent(
                        run_id=run_id,
                        node_id=axis,
                        workspace=gated_workspace,
                        prompt_text=_review_prompt(axis, axis_prompt),
                        schema=REVIEW_VERDICT_RESULT_SCHEMA,
                        verification_class="semantic",
                        model=model,
                        effort=effort,
                        max_retries=1,
                    )
                )
            )
        )
    review_results = yield Gather(*review_tasks)
    review_routing = _review_artifacts_to_routing(tuple(review_results))
    phase_seconds["Review"] = time.perf_counter() - phase_started_at

    result_payload: dict[str, Any] = {
        "runId": run_id,
        "baseRef": base_ref,
        "workspaceRef": gated_workspace.ref,
        "implementers": list(implementer_results),
        "testWriters": list(test_writer_results),
        "gateRounds": gate_rounds,
        "buildGateLogs": build_gate_logs,
        "fullGateLogs": full_gate_logs,
        "reviewRouting": list(review_routing),
        "phaseSeconds": phase_seconds,
    }
    if report_path is not None:
        destination_path: Path = Path(report_path)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_text(
            json.dumps(result_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    return result_payload
