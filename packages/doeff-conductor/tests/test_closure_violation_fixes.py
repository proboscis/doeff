"""Tests for closure-law violation fixes: merge conflict, loop exhaustion, quorum-not-met.

All three raw ``raise`` terminals are converted to open gates carrying
structured context.  The K5 answer machinery (gate-answer-journal.jsonl)
drives the gate lifecycle; no new mechanism.

Covers:
- Merge conflict parks (not raises); gate carries conflicted file list
- Loop predicate exhaustion parks; ``proceed`` accepts last-state
- Quorum-not-met parks; ``proceed`` accepts partial results
- Gate answer ``abort`` terminates cleanly
- Validation scenarios cover merge-conflict and loop-exhaustion branches
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from doeff_conductor.dsl import (
    agent_bang,
    artifact,
    bind,
    defphase,
    defworkflow,
    loop,
    merge_bang,
    oks,
    parallel,
    prompt,
    ref,
    workspace_bang,
)
from doeff_conductor.effects import (
    AgentAttemptExhaustedError,
    AgentEffect,
    AgentValidationErrorKind,
    AgentValidationFailure,
)
from doeff_conductor.effects.workspace import MergeWorkspaces
from doeff_conductor.handlers import mock_handlers as build_mock_handlers
from doeff_conductor.handlers import run_sync
from doeff_conductor.handlers.testing import MockConductorRuntime
from doeff_conductor.overseer import GateOption, OpenGateView
from doeff_conductor.types import (
    MergeConflict,
    MergeStatus,
    MergeWorkspacesResult,
    Workspace,
)
from doeff_conductor.verbs import (
    BUILT_IN_VALIDATION_SCENARIOS,
    validate_workflow,
)
from doeff_conductor.workflow_runtime import (
    ParkedValue,
    WorkflowRuntimeResult,
    workflow_spec_to_program,
)

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
    "additionalProperties": False,
}


class TestAgentResultValidationGate:
    """Agent schema failures park behind a clear validation gate."""

    def test_agent_attempt_exhaustion_names_validation_not_budget(
        self,
        tmp_path: Path,
    ) -> None:
        workflow = defworkflow(
            "agent-result-validation-test",
            params={"base_ref": str},
            roles={"worker": {"profile": "cheap-coder", "retry": 0}},
            body=[
                bind(
                    "result",
                    agent_bang(
                        role="worker",
                        verification_class="test-verifiable",
                        prompt="return schema-valid json",
                        schema=RESULT_SCHEMA,
                    ),
                ),
                artifact(ref("result")),
            ],
        )

        def fail_agent(effect: AgentEffect) -> object:
            raise AgentAttemptExhaustedError(
                session_id=effect.task.session_id,
                attempts=1,
                last_error=AgentValidationFailure(
                    kind=AgentValidationErrorKind.INVALID,
                    message="'summary' is a required property",
                ),
            )

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(
            runtime=runtime,
            overrides={AgentEffect: fail_agent},
        )
        program = workflow_spec_to_program(
            workflow,
            run_id="agent-validation-run",
            params={"base_ref": "main"},
        )

        result = run_sync(program, scheduled_handlers=handlers)

        assert result.is_ok
        gate: OpenGateView = result.value.open_gates[0]
        assert gate.reason == "agent result validation failed"
        assert gate.gate_id.endswith(":agent-result-validation-failed")
        assert gate.stakes["last_error_kind"] == "invalid"
        assert gate.stakes["last_error_message"] == "'summary' is a required property"
        assert {option.name for option in gate.options} == {
            "retry-agent",
            "redirect",
            "abort",
        }

    def test_retry_agent_answer_starts_new_attempt_with_error_context(
        self,
        tmp_path: Path,
    ) -> None:
        workflow = defworkflow(
            "agent-validation-retry",
            params={"base_ref": str},
            roles={"worker": {"profile": "cheap-coder", "retry": 0}},
            body=[
                bind(
                    "result",
                    agent_bang(
                        role="worker",
                        verification_class="test-verifiable",
                        prompt="return schema-valid json",
                        schema=RESULT_SCHEMA,
                    ),
                ),
                artifact(ref("result")),
            ],
        )
        gate_id = (
            "agent-validation-retry-run:"
            "agent-validation-retry/0/agent:"
            "agent-result-validation-failed"
        )
        seen_attempts: list[int] = []
        seen_prompts: list[str] = []
        seen_session_ids: list[str] = []

        def fail_agent(effect: AgentEffect) -> object:
            seen_attempts.append(effect.task.attempt)
            seen_prompts.append(effect.task.worker_prompt)
            seen_session_ids.append(effect.task.session_id)
            raise AgentAttemptExhaustedError(
                session_id=effect.task.session_id,
                attempts=1,
                last_error=AgentValidationFailure(
                    kind=AgentValidationErrorKind.INVALID,
                    message="'result.tests[0]' must be of type string",
                ),
            )

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(
            runtime=runtime,
            overrides={AgentEffect: fail_agent},
        )
        program = workflow_spec_to_program(
            workflow,
            run_id="agent-validation-retry-run",
            params={"base_ref": "main"},
            answered_gate_options={gate_id: "retry-agent"},
            answered_retry_agent_counts={gate_id: 1},
            answered_gate_stakes={
                gate_id: {
                    "last_error_kind": "invalid",
                    "last_error_message": "'tests' is a required property",
                    "session_id": "previous-session-0",
                }
            },
        )

        result = run_sync(program, scheduled_handlers=handlers)

        assert result.is_ok
        assert seen_attempts == [1]
        assert seen_session_ids[0].endswith("-1")
        assert "Previous structured result failure" in seen_prompts[0]
        assert "previous-session-0" in seen_prompts[0]
        assert "'tests' is a required property" in seen_prompts[0]


# =========================================================================
# Merge conflict gate tests
# =========================================================================


def _merge_workflow() -> Any:
    """Build a minimal workflow: two agents on separate workspaces → merge! → artifact."""
    ws_a: Any = workspace_bang(from_="main-branch-a")
    ws_b: Any = workspace_bang(from_="main-branch-b")
    return defworkflow(
        "merge-conflict-test",
        params={"base_ref": str},
        roles={"worker": {"profile": "cheap-coder", "retry": 0}},
        body=[
            bind(
                ["a", "b"],
                parallel(
                    agent_bang(
                        role="worker",
                        verification_class="test-verifiable",
                        prompt="branch a",
                        schema=RESULT_SCHEMA,
                        workspace=ws_a,
                        label="branch-a",
                    ),
                    agent_bang(
                        role="worker",
                        verification_class="test-verifiable",
                        prompt="branch b",
                        schema=RESULT_SCHEMA,
                        workspace=ws_b,
                        label="branch-b",
                    ),
                ),
            ),
            bind("merged", merge_bang(workspaces=[ws_a, ws_b])),
            artifact(prompt(ref("a"), ref("b"), ref("merged"))),
        ],
    )


def _conflict_merge_result(effect: MergeWorkspaces) -> MergeWorkspacesResult:
    """Return a structured CONFLICT result with conflicted files."""
    conflicts: list[MergeConflict] = []
    for workspace in effect.workspaces:
        conflicts.append(
            MergeConflict(
                workspace=workspace,
                files=("src/overlap.py",),
            )
        )
    return MergeWorkspacesResult(
        status=MergeStatus.CONFLICT,
        workspace=None,
        conflicts=tuple(conflicts),
        message="conflicting changes in src/overlap.py",
    )


class TestMergeConflictParks:
    """Merge conflict parks (not raises) with gate carrying conflict details."""

    def test_conflict_parks_with_gate(self, tmp_path: Path) -> None:
        """Merge conflict returns ParkedValue with structured gate."""
        workflow: Any = _merge_workflow()

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(
            runtime=runtime,
            overrides={MergeWorkspaces: _conflict_merge_result},
        )
        program: Any = workflow_spec_to_program(
            workflow,
            run_id="merge-run",
            params={"base_ref": "main"},
        )
        result = run_sync(program, scheduled_handlers=handlers)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert isinstance(runtime_result, WorkflowRuntimeResult)
        assert len(runtime_result.open_gates) == 1

        gate: OpenGateView = runtime_result.open_gates[0]
        assert gate.reason == "merge conflict"
        assert "merge-conflict" in gate.gate_id

    def test_gate_carries_conflicted_files(self, tmp_path: Path) -> None:
        """Gate stakes include conflicted file list and source workspaces."""
        workflow: Any = _merge_workflow()

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(
            runtime=runtime,
            overrides={MergeWorkspaces: _conflict_merge_result},
        )
        program: Any = workflow_spec_to_program(
            workflow,
            run_id="merge-files-run",
            params={"base_ref": "main"},
        )
        result = run_sync(program, scheduled_handlers=handlers)

        assert result.is_ok
        gate: OpenGateView = result.value.open_gates[0]
        assert "src/overlap.py" in gate.stakes["conflicted_files"]
        assert len(gate.stakes["source_workspaces"]) == 2
        assert gate.stakes["verification_class"] == "merge"
        assert gate.stakes["blast_radius"] == "dependent-subtree"
        assert gate.stakes["reversibility"] == "retryable"

    def test_gate_options_are_retry_merge_and_abort(self, tmp_path: Path) -> None:
        """Gate options: retry-merge (resume) and abort."""
        workflow: Any = _merge_workflow()

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(
            runtime=runtime,
            overrides={MergeWorkspaces: _conflict_merge_result},
        )
        program: Any = workflow_spec_to_program(
            workflow,
            run_id="merge-options-run",
            params={"base_ref": "main"},
        )
        result = run_sync(program, scheduled_handlers=handlers)

        gate: OpenGateView = result.value.open_gates[0]
        option_names: set[str] = {option.name for option in gate.options}
        assert option_names == {"retry-merge", "abort"}
        retry_option: GateOption = next(
            option for option in gate.options if option.name == "retry-merge"
        )
        assert retry_option.outcome == "resume"
        abort_option: GateOption = next(
            option for option in gate.options if option.name == "abort"
        )
        assert abort_option.outcome == "abort"

    def test_retry_merge_after_resolving_completes(self, tmp_path: Path) -> None:
        """Answer retry-merge after resolving → run completes on resume."""
        workflow: Any = _merge_workflow()
        merge_call_count: list[int] = [0]

        def handle_merge(effect: MergeWorkspaces) -> MergeWorkspacesResult:
            merge_call_count[0] += 1
            if merge_call_count[0] == 1:
                return _conflict_merge_result(effect)
            runtime_for_merge = MockConductorRuntime(tmp_path / "merge-resolve")
            merged_ws: Workspace = runtime_for_merge._ensure_workspace(
                effect.workspace_id,
                repo=effect.workspaces[0].repo,
                base_ref=effect.workspaces[0].ref,
            )
            return MergeWorkspacesResult(
                status=MergeStatus.MERGED,
                workspace=merged_ws,
            )

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(
            runtime=runtime,
            overrides={MergeWorkspaces: handle_merge},
        )

        # First run: conflict
        program: Any = workflow_spec_to_program(
            workflow,
            run_id="merge-retry-run",
            params={"base_ref": "main"},
        )
        first_result = run_sync(program, scheduled_handlers=handlers)
        assert first_result.is_ok
        assert len(first_result.value.open_gates) == 1
        gate_id: str = first_result.value.open_gates[0].gate_id

        # Second run (resume with answered gate): succeeds
        program_resumed: Any = workflow_spec_to_program(
            workflow,
            run_id="merge-retry-run",
            params={"base_ref": "main"},
            answered_gate_options={gate_id: "retry-merge"},
        )
        second_result = run_sync(program_resumed, scheduled_handlers=handlers)
        assert second_result.is_ok
        runtime_result: WorkflowRuntimeResult = second_result.value
        assert len(runtime_result.open_gates) == 0
        # Artifact is prompt(a, b, merged) — a string; confirm it completed
        assert runtime_result.value is not None
        assert not isinstance(runtime_result.value, ParkedValue)


# =========================================================================
# Loop predicate exhaustion gate tests
# =========================================================================


def _loop_workflow(max_iterations: int = 3) -> Any:
    """Build a workflow with a loop that never satisfies its predicate.

    The loop body is an unbound agent expression (last value = loop result).
    The ``until`` predicate always returns False so the loop exhausts.
    """
    ws: Any = workspace_bang(from_="main")
    return defworkflow(
        "loop-exhaust-test",
        params={"base_ref": str},
        roles={"worker": {"profile": "cheap-coder", "retry": 0}},
        body=[
            bind(
                "result",
                loop(
                    max_iterations=max_iterations,
                    until="never_true",
                    body=[
                        agent_bang(
                            role="worker",
                            verification_class="test-verifiable",
                            prompt="fix it",
                            schema=RESULT_SCHEMA,
                            workspace=ws,
                            label="fixer",
                        ),
                    ],
                ),
            ),
            artifact(ref("result")),
        ],
    )


class TestLoopExhaustionParks:
    """Loop predicate exhaustion parks (not raises) with gate."""

    def test_exhaustion_parks_with_gate(self, tmp_path: Path) -> None:
        """Loop exhaustion returns ParkedValue with structured gate."""
        workflow: Any = _loop_workflow(max_iterations=2)

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(runtime=runtime)
        program: Any = workflow_spec_to_program(
            workflow,
            run_id="loop-run",
            params={"base_ref": "main"},
        )
        result = run_sync(program, scheduled_handlers=handlers)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert len(runtime_result.open_gates) == 1

        gate: OpenGateView = runtime_result.open_gates[0]
        assert gate.reason == "loop predicate exhaustion"
        assert "loop-exhaustion" in gate.gate_id
        assert gate.stakes["max_iterations"] == 2
        assert gate.stakes["verification_class"] == "loop"

    def test_gate_options_are_proceed_and_abort(self, tmp_path: Path) -> None:
        """Gate options: proceed (accept last state) and abort."""
        workflow: Any = _loop_workflow(max_iterations=1)

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(runtime=runtime)
        program: Any = workflow_spec_to_program(
            workflow,
            run_id="loop-options-run",
            params={"base_ref": "main"},
        )
        result = run_sync(program, scheduled_handlers=handlers)

        gate: OpenGateView = result.value.open_gates[0]
        option_names: set[str] = {option.name for option in gate.options}
        assert option_names == {"proceed", "abort"}

    def test_proceed_accepts_last_state(self, tmp_path: Path) -> None:
        """Answer proceed on resume → accepts last iteration value."""
        workflow: Any = _loop_workflow(max_iterations=2)

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(runtime=runtime)

        # First run: exhaustion → park
        program: Any = workflow_spec_to_program(
            workflow,
            run_id="loop-proceed-run",
            params={"base_ref": "main"},
        )
        first_result = run_sync(program, scheduled_handlers=handlers)
        assert first_result.is_ok
        assert len(first_result.value.open_gates) == 1
        gate_id: str = first_result.value.open_gates[0].gate_id

        # Resume with proceed answered
        program_resumed: Any = workflow_spec_to_program(
            workflow,
            run_id="loop-proceed-run",
            params={"base_ref": "main"},
            answered_gate_options={gate_id: "proceed"},
        )
        second_result = run_sync(program_resumed, scheduled_handlers=handlers)
        assert second_result.is_ok
        runtime_result: WorkflowRuntimeResult = second_result.value
        assert len(runtime_result.open_gates) == 0
        # The artifact is the last loop iteration's agent result
        assert runtime_result.value == {"summary": "mock artifact"}

    def test_open_loop_gate_blocks_downstream_phases(self, tmp_path: Path) -> None:
        """A parked loop gate stops downstream phases until it is answered."""
        ws: Any = workspace_bang(from_="main")
        workflow = defworkflow(
            "loop-blocks-downstream",
            params={"base_ref": str},
            roles={"worker": {"profile": "cheap-coder", "retry": 0}},
            body=[
                defphase(
                    "Blocker",
                    body=[
                        bind(
                            "blocked",
                            loop(
                                max_iterations=1,
                                until="never_true",
                                body=[
                                    agent_bang(
                                        role="worker",
                                        verification_class="test-verifiable",
                                        prompt="loop body",
                                        schema=RESULT_SCHEMA,
                                        workspace=ws,
                                        label="loop-agent",
                                    )
                                ],
                            ),
                        )
                    ],
                ),
                defphase(
                    "Downstream",
                    body=[
                        bind(
                            "after",
                            agent_bang(
                                role="worker",
                                verification_class="test-verifiable",
                                prompt="must not run before gate answer",
                                schema=RESULT_SCHEMA,
                                workspace=ws,
                                label="downstream-agent",
                            ),
                        ),
                        artifact(prompt(ref("blocked"), ref("after"))),
                    ],
                ),
            ],
        )
        seen_prompts: list[str] = []

        def handle_agent(effect: AgentEffect) -> dict[str, Any]:
            seen_prompts.append(effect.task.prompt)
            return {"summary": effect.task.prompt}

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(
            runtime=runtime,
            overrides={AgentEffect: handle_agent},
        )
        program: Any = workflow_spec_to_program(
            workflow,
            run_id="loop-blocks-downstream",
            params={"base_ref": "main"},
        )

        result = run_sync(program, scheduled_handlers=handlers)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert len(runtime_result.open_gates) == 1
        assert seen_prompts == ["loop body"]


# =========================================================================
# Quorum-not-met gate tests
# =========================================================================


def _quorum_workflow(
    branch_count: int,
    quorum: int,
    *,
    fail_indices: frozenset[int] = frozenset(),
) -> Any:
    """Build a workflow with ``parallel :quorum k`` and configurable failures."""
    shared_workspace: Any = workspace_bang(from_="main")
    branches: list[Any] = []
    for index in range(branch_count):
        label: str = f"branch-{index}"
        branch_prompt: str = f"fail:{index}" if index in fail_indices else f"ok:{index}"
        branches.append(
            agent_bang(
                role="worker",
                verification_class="test-verifiable",
                prompt=branch_prompt,
                schema=RESULT_SCHEMA,
                workspace=shared_workspace,
                files={f"branch_{index}.py"},
                label=label,
            )
        )
    return defworkflow(
        "quorum-gate-test",
        params={"base_ref": str},
        roles={"worker": {"profile": "cheap-coder", "retry": 0}},
        body=[
            bind("results", parallel(*branches, quorum=quorum)),
            artifact(oks(ref("results"))),
        ],
    )


def _run_quorum_workflow(
    workflow: Any,
    *,
    tmp_path: Path,
    fail_prompts: frozenset[str] = frozenset(),
    answered_gate_options: dict[str, str] | None = None,
    run_id: str = "quorum-gate-run",
) -> Any:
    """Run a quorum workflow with a selective-failure agent handler."""
    from doeff_conductor.effects.agent import AgentEffect
    from doeff_conductor.exceptions import AgentError

    def handle_agent(effect: AgentEffect) -> dict[str, Any]:
        agent_prompt: str = effect.task.prompt
        if agent_prompt.startswith("fail:"):
            raise AgentError(
                agent_id=effect.task.node_id,
                operation="execute",
                message=f"branch failed: {agent_prompt}",
            )
        return {"summary": f"done: {agent_prompt}"}

    runtime = MockConductorRuntime(tmp_path)
    handlers: Any = build_mock_handlers(
        runtime=runtime,
        overrides={AgentEffect: handle_agent},
    )
    program: Any = workflow_spec_to_program(
        workflow,
        run_id=run_id,
        params={"base_ref": "main"},
        answered_gate_options=answered_gate_options or {},
    )
    return run_sync(program, scheduled_handlers=handlers)


class TestQuorumNotMetParks:
    """Quorum shortfall parks (not raises) with gate."""

    def test_quorum_not_met_parks_with_gate(self, tmp_path: Path) -> None:
        """Below-quorum parks with structured gate."""
        workflow: Any = _quorum_workflow(3, quorum=2, fail_indices=frozenset({0, 1}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert len(runtime_result.open_gates) == 1

        gate: OpenGateView = runtime_result.open_gates[0]
        assert gate.reason == "quorum not met"
        assert "quorum-not-met" in gate.gate_id
        assert gate.stakes["quorum"] == 2
        assert gate.stakes["total"] == 3
        assert gate.stakes["succeeded"] == 1
        assert gate.stakes["failed"] == 2
        assert gate.stakes["verification_class"] == "quorum"

    def test_all_fail_parks_with_gate(self, tmp_path: Path) -> None:
        """All branches fail → parks with gate."""
        workflow: Any = _quorum_workflow(3, quorum=1, fail_indices=frozenset({0, 1, 2}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert len(runtime_result.open_gates) == 1
        gate: OpenGateView = runtime_result.open_gates[0]
        assert gate.stakes["succeeded"] == 0
        assert gate.stakes["failed"] == 3

    def test_gate_options_are_proceed_and_abort(self, tmp_path: Path) -> None:
        """Gate options: proceed (accept partial) and abort."""
        workflow: Any = _quorum_workflow(3, quorum=2, fail_indices=frozenset({0, 1}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        gate: OpenGateView = result.value.open_gates[0]
        option_names: set[str] = {option.name for option in gate.options}
        assert option_names == {"proceed", "abort"}

    def test_proceed_accepts_partial_results(self, tmp_path: Path) -> None:
        """Answer proceed → accepts partial results and completes."""
        workflow: Any = _quorum_workflow(3, quorum=2, fail_indices=frozenset({0, 1}))

        # First run: quorum not met → park
        first_result = _run_quorum_workflow(workflow, tmp_path=tmp_path)
        assert first_result.is_ok
        assert len(first_result.value.open_gates) == 1
        gate_id: str = first_result.value.open_gates[0].gate_id

        # Resume with proceed answered
        second_result = _run_quorum_workflow(
            workflow,
            tmp_path=tmp_path / "resume",
            answered_gate_options={gate_id: "proceed"},
            run_id="quorum-gate-run",
        )
        assert second_result.is_ok
        runtime_result: WorkflowRuntimeResult = second_result.value
        assert len(runtime_result.open_gates) == 0
        # The artifact is oks(results): only the one success
        oks_value: Any = runtime_result.value
        assert isinstance(oks_value, tuple)
        assert len(oks_value) == 1
        assert oks_value[0] == {"summary": "done: ok:2"}

    def test_quorum_met_still_records_tolerated_losses(self, tmp_path: Path) -> None:
        """Quorum met with some failures still records tolerated losses."""
        workflow: Any = _quorum_workflow(3, quorum=1, fail_indices=frozenset({0, 2}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert len(runtime_result.open_gates) == 0
        assert len(runtime_result.tolerated_losses) == 2


# =========================================================================
# Validation scenario tests
# =========================================================================


class TestValidationScenarios:
    """``conductor validate`` scenarios cover merge-conflict and loop-exhaustion."""

    def test_merge_conflict_scenario_in_built_in(self) -> None:
        """merge-conflict is a built-in validation scenario."""
        assert "merge-conflict" in BUILT_IN_VALIDATION_SCENARIOS

    def test_loop_exhaustion_scenario_in_built_in(self) -> None:
        """loop-exhaustion is a built-in validation scenario."""
        assert "loop-exhaustion" in BUILT_IN_VALIDATION_SCENARIOS

    def test_merge_conflict_scenario_closure_ok(self) -> None:
        """merge-conflict scenario produces gate terminal → closure passes."""
        workflow: Any = _merge_workflow()
        report = validate_workflow(
            workflow,
            scenarios=("merge-conflict",),
        )
        assert report.scenarios[0].to_dict()["closure_ok"]
        gate_reasons: set[str] = {
            gate.reason for gate in report.scenarios[0].open_gates
        }
        # The merge node appears as a gate in this scenario
        if gate_reasons:
            assert "merge conflict" in gate_reasons

    def test_loop_exhaustion_scenario_closure_ok(self) -> None:
        """loop-exhaustion scenario produces gate terminal → closure passes."""
        workflow: Any = _loop_workflow(max_iterations=3)
        report = validate_workflow(
            workflow,
            scenarios=("loop-exhaustion",),
        )
        assert report.scenarios[0].to_dict()["closure_ok"]
        gate_reasons: set[str] = {
            gate.reason for gate in report.scenarios[0].open_gates
        }
        if gate_reasons:
            assert "loop predicate exhaustion" in gate_reasons

    def test_all_built_in_scenarios_pass_for_merge_workflow(self) -> None:
        """All built-in validation scenarios pass closure for a merge workflow."""
        workflow: Any = _merge_workflow()
        report = validate_workflow(workflow)
        assert all(
            scenario.to_dict()["closure_ok"]
            for scenario in report.scenarios
        )

    def test_all_built_in_scenarios_pass_for_loop_workflow(self) -> None:
        """All built-in validation scenarios pass closure for a loop workflow."""
        workflow: Any = _loop_workflow()
        report = validate_workflow(workflow)
        assert all(
            scenario.to_dict()["closure_ok"]
            for scenario in report.scenarios
        )
