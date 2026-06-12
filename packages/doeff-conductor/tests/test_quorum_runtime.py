"""Quorum runtime semantics tests (ADR D2 / spec §4.3).

Tests the runtime enforcement of ``parallel :quorum k``:

- k-of-n success → QuorumResult with Try-typed entries
- Failure beyond tolerance → open gate (closure-preserving park)
- ``oks()`` projects successes from QuorumResult
- Expansion rejects direct deref of Try-typed bindings (DSL layer)
- Tolerated losses are recorded and surfaced, never silent
- Default (no quorum) keeps all-must-succeed plain binding behavior

Late-branch policy: **let-finish-and-journal**.  All branches run to
completion; the quorum wrapper catches failures as ErrValue.  This
satisfies the closure law (every node reaches a terminal state) and
provides complete journal coverage.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from doeff_conductor.dsl import (
    WorkflowExpansionError,
    agent_bang,
    artifact,
    bind,
    defworkflow,
    field,
    oks,
    parallel,
    prompt,
    ref,
    workspace_bang,
)
from doeff_conductor.handlers import mock_handlers as build_mock_handlers
from doeff_conductor.handlers import run_sync
from doeff_conductor.handlers.testing import MockConductorRuntime
from doeff_conductor.overseer import OpenGateView
from doeff_conductor.workflow_runtime import (
    ErrValue,
    OkValue,
    ParkedValue,
    QuorumResult,
    ToleratedLoss,
    WorkflowRuntimeResult,
    workflow_spec_to_program,
)

RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
    "additionalProperties": False,
}


def _quorum_workflow(
    branch_count: int,
    quorum: int,
    *,
    fail_indices: frozenset[int] = frozenset(),
) -> Any:
    """Build a workflow with ``parallel :quorum k`` and configurable failures.

    Branch prompts encode ``fail:<index>`` for branches that should fail.
    Uses a shared workspace with separate :files per branch to satisfy the
    expansion-time workspace isolation check.
    """
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
        "quorum-test",
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
        run_id="quorum-run",
        params={"base_ref": "main"},
    )
    return run_sync(program, scheduled_handlers=handlers)


class TestQuorumKOfNSuccess:
    """Quorum k-of-n: k branches succeed, remaining tolerated."""

    def test_two_of_three_success(self, tmp_path: Path) -> None:
        """2-of-3 quorum with one failure → QuorumResult."""
        workflow: Any = _quorum_workflow(3, quorum=2, fail_indices=frozenset({2}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert isinstance(runtime_result, WorkflowRuntimeResult)

        # The artifact is oks(results): tuple of successful values
        oks_value: Any = runtime_result.value
        assert isinstance(oks_value, tuple)
        assert len(oks_value) == 2
        assert oks_value[0] == {"summary": "done: ok:0"}
        assert oks_value[1] == {"summary": "done: ok:1"}

    def test_one_of_three_success(self, tmp_path: Path) -> None:
        """1-of-3 quorum with two failures → QuorumResult."""
        workflow: Any = _quorum_workflow(3, quorum=1, fail_indices=frozenset({0, 2}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        oks_value: Any = runtime_result.value
        assert isinstance(oks_value, tuple)
        assert len(oks_value) == 1
        assert oks_value[0] == {"summary": "done: ok:1"}

    def test_all_succeed_under_quorum(self, tmp_path: Path) -> None:
        """2-of-3 quorum with all branches succeeding → 3 OkValues."""
        workflow: Any = _quorum_workflow(3, quorum=2, fail_indices=frozenset())
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        oks_value: Any = runtime_result.value
        assert isinstance(oks_value, tuple)
        assert len(oks_value) == 3


class TestQuorumFailureBeyondTolerance:
    """Below-quorum → open gate (closure-preserving park)."""

    def test_two_of_three_with_two_failures(self, tmp_path: Path) -> None:
        """2-of-3 quorum with 2 failures → parks with gate."""
        workflow: Any = _quorum_workflow(3, quorum=2, fail_indices=frozenset({0, 1}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert isinstance(runtime_result, WorkflowRuntimeResult)
        assert len(runtime_result.open_gates) == 1

        gate: OpenGateView = runtime_result.open_gates[0]
        assert gate.reason == "quorum not met"
        assert gate.stakes["quorum"] == 2
        assert gate.stakes["total"] == 3
        assert gate.stakes["succeeded"] == 1
        assert gate.stakes["failed"] == 2

    def test_all_fail(self, tmp_path: Path) -> None:
        """1-of-3 quorum with all failures → parks with gate."""
        workflow: Any = _quorum_workflow(3, quorum=1, fail_indices=frozenset({0, 1, 2}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert len(runtime_result.open_gates) == 1
        gate: OpenGateView = runtime_result.open_gates[0]
        assert gate.stakes["succeeded"] == 0
        assert gate.stakes["failed"] == 3


class TestOksProjection:
    """``(oks ...)`` projects successes from QuorumResult."""

    def test_oks_extracts_only_successes(self, tmp_path: Path) -> None:
        """oks() on a QuorumResult yields only Ok values."""
        workflow: Any = _quorum_workflow(3, quorum=1, fail_indices=frozenset({0, 2}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        oks_value: Any = runtime_result.value
        assert isinstance(oks_value, tuple)
        assert len(oks_value) == 1
        assert oks_value[0]["summary"] == "done: ok:1"

    def test_oks_preserves_order(self, tmp_path: Path) -> None:
        """oks() preserves branch order of successes."""
        workflow: Any = _quorum_workflow(4, quorum=2, fail_indices=frozenset({1}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        oks_value: Any = result.value.value
        assert len(oks_value) == 3
        summaries: list[str] = [entry["summary"] for entry in oks_value]
        assert summaries == ["done: ok:0", "done: ok:2", "done: ok:3"]


class TestExpansionRejection:
    """Expansion-time check 6: direct deref of Try-typed bindings."""

    def test_direct_deref_rejected(self) -> None:
        """field(ref('x'), 'f') on a Try binding → WorkflowExpansionError."""
        shared_ws: Any = workspace_bang(from_="main")
        workflow: Any = defworkflow(
            "deref-test",
            params={"base_ref": str},
            roles={"worker": {"profile": "cheap-coder"}},
            body=[
                bind(
                    "results",
                    parallel(
                        agent_bang(
                            role="worker",
                            verification_class="test",
                            prompt="a",
                            schema=RESULT_SCHEMA,
                            workspace=shared_ws,
                            files={"a.py"},
                        ),
                        agent_bang(
                            role="worker",
                            verification_class="test",
                            prompt="b",
                            schema=RESULT_SCHEMA,
                            workspace=shared_ws,
                            files={"b.py"},
                        ),
                        quorum=1,
                    ),
                ),
                artifact(field(ref("results"), "summary")),
            ],
        )

        with pytest.raises(WorkflowExpansionError, match="Try"):
            workflow.expand()

    def test_oks_allowed(self) -> None:
        """oks(ref('x')) on a Try binding is accepted."""
        shared_ws: Any = workspace_bang(from_="main")
        workflow: Any = defworkflow(
            "oks-test",
            params={"base_ref": str},
            roles={"worker": {"profile": "cheap-coder"}},
            body=[
                bind(
                    "results",
                    parallel(
                        agent_bang(
                            role="worker",
                            verification_class="test",
                            prompt="a",
                            schema=RESULT_SCHEMA,
                            workspace=shared_ws,
                            files={"a.py"},
                        ),
                        agent_bang(
                            role="worker",
                            verification_class="test",
                            prompt="b",
                            schema=RESULT_SCHEMA,
                            workspace=shared_ws,
                            files={"b.py"},
                        ),
                        quorum=1,
                    ),
                ),
                artifact(oks(ref("results"))),
            ],
        )

        expanded: Any = workflow.expand()
        assert expanded.bindings["results"].is_try

    def test_quorum_destructuring_rejected(self) -> None:
        """Destructuring bind on quorum parallel → error."""
        shared_ws: Any = workspace_bang(from_="main")
        workflow: Any = defworkflow(
            "destructure-test",
            params={"base_ref": str},
            roles={"worker": {"profile": "cheap-coder"}},
            body=[
                bind(
                    ["a", "b"],
                    parallel(
                        agent_bang(
                            role="worker",
                            verification_class="test",
                            prompt="a",
                            schema=RESULT_SCHEMA,
                            workspace=shared_ws,
                            files={"a.py"},
                        ),
                        agent_bang(
                            role="worker",
                            verification_class="test",
                            prompt="b",
                            schema=RESULT_SCHEMA,
                            workspace=shared_ws,
                            files={"b.py"},
                        ),
                        quorum=1,
                    ),
                ),
                artifact(prompt(ref("a"), ref("b"))),
            ],
        )

        with pytest.raises(WorkflowExpansionError, match="quorum.*bind"):
            workflow.expand()


class TestToleratedLossJournal:
    """Tolerated losses are recorded in context, never silent."""

    def test_losses_recorded_in_result(self, tmp_path: Path) -> None:
        """Tolerated failures appear in WorkflowRuntimeResult.tolerated_losses."""
        workflow: Any = _quorum_workflow(3, quorum=2, fail_indices=frozenset({1}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        losses: tuple[ToleratedLoss, ...] = runtime_result.tolerated_losses
        assert len(losses) == 1

        loss: ToleratedLoss = losses[0]
        assert loss.branch_index == 1
        assert loss.quorum == 2
        assert loss.total == 3
        assert "AgentError" in loss.error_type

    def test_multiple_losses_recorded(self, tmp_path: Path) -> None:
        """Multiple tolerated failures are all recorded."""
        workflow: Any = _quorum_workflow(4, quorum=1, fail_indices=frozenset({0, 2, 3}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        losses: tuple[ToleratedLoss, ...] = runtime_result.tolerated_losses
        assert len(losses) == 3
        loss_indices: set[int] = {loss.branch_index for loss in losses}
        assert loss_indices == {0, 2, 3}

    def test_no_losses_when_all_succeed(self, tmp_path: Path) -> None:
        """No tolerated losses when all branches succeed."""
        workflow: Any = _quorum_workflow(3, quorum=2, fail_indices=frozenset())
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert len(runtime_result.tolerated_losses) == 0

    def test_no_losses_in_quorum_failure(self, tmp_path: Path) -> None:
        """quorum==n with n branches is plain all-must-succeed (not quorum form)."""
        workflow: Any = _quorum_workflow(3, quorum=3, fail_indices=frozenset({0}))
        result = _run_quorum_workflow(workflow, tmp_path=tmp_path)

        # quorum=3 with 3 branches is quorum==n, so it's NOT a quorum form.
        # It behaves as all-must-succeed. The branch failure propagates as
        # an exception (not caught by quorum wrapper since is_quorum_form=False).
        assert result.is_err


class TestDefaultBehaviorPreserved:
    """Default (no quorum) keeps all-must-succeed plain binding behavior."""

    def test_plain_parallel_returns_tuple(self, tmp_path: Path) -> None:
        """parallel() without quorum returns plain tuple of results."""
        from doeff_conductor.effects.agent import AgentEffect

        def handle_agent(effect: AgentEffect) -> dict[str, Any]:
            return {"summary": f"done: {effect.task.prompt}"}

        shared_ws: Any = workspace_bang(from_="main")
        workflow: Any = defworkflow(
            "plain-parallel-test",
            params={"base_ref": str},
            roles={"worker": {"profile": "cheap-coder", "retry": 0}},
            body=[
                bind(
                    ["a", "b"],
                    parallel(
                        agent_bang(
                            role="worker",
                            verification_class="test-verifiable",
                            prompt="branch-a",
                            schema=RESULT_SCHEMA,
                            workspace=shared_ws,
                            files={"a.py"},
                            label="branch-a",
                        ),
                        agent_bang(
                            role="worker",
                            verification_class="test-verifiable",
                            prompt="branch-b",
                            schema=RESULT_SCHEMA,
                            workspace=shared_ws,
                            files={"b.py"},
                            label="branch-b",
                        ),
                    ),
                ),
                artifact(prompt(ref("a"), ref("b"))),
            ],
        )

        runtime = MockConductorRuntime(tmp_path)
        handlers: Any = build_mock_handlers(
            runtime=runtime,
            overrides={AgentEffect: handle_agent},
        )
        program: Any = workflow_spec_to_program(
            workflow,
            run_id="plain-run",
            params={"base_ref": "main"},
        )
        result = run_sync(program, scheduled_handlers=handlers)

        assert result.is_ok
        runtime_result: WorkflowRuntimeResult = result.value
        assert isinstance(runtime_result, WorkflowRuntimeResult)
        assert len(runtime_result.tolerated_losses) == 0

    def test_quorum_equals_n_is_plain(self) -> None:
        """quorum=n with n branches → is_try=False at expansion."""
        shared_ws: Any = workspace_bang(from_="main")
        workflow: Any = defworkflow(
            "quorum-n-test",
            params={"base_ref": str},
            roles={"worker": {"profile": "cheap-coder"}},
            body=[
                bind(
                    ["a", "b"],
                    parallel(
                        agent_bang(
                            role="worker",
                            verification_class="test",
                            prompt="a",
                            schema=RESULT_SCHEMA,
                            workspace=shared_ws,
                            files={"a.py"},
                        ),
                        agent_bang(
                            role="worker",
                            verification_class="test",
                            prompt="b",
                            schema=RESULT_SCHEMA,
                            workspace=shared_ws,
                            files={"b.py"},
                        ),
                        quorum=2,
                    ),
                ),
                artifact(prompt(ref("a"), ref("b"))),
            ],
        )

        expanded: Any = workflow.expand()
        assert not expanded.bindings["a"].is_try
        assert not expanded.bindings["b"].is_try


class TestQuorumResultDataTypes:
    """Verify OkValue, ErrValue, QuorumResult, ToleratedLoss structure."""

    def test_ok_value_frozen(self) -> None:
        ok_val: OkValue = OkValue(value={"x": 1}, branch_index=0)
        assert ok_val.value == {"x": 1}
        assert ok_val.branch_index == 0
        with pytest.raises(AttributeError):
            ok_val.value = {"y": 2}  # type: ignore[misc]

    def test_err_value_frozen(self) -> None:
        err_val: ErrValue = ErrValue(error="boom", error_type="RuntimeError", branch_index=1)
        assert err_val.error == "boom"
        assert err_val.error_type == "RuntimeError"
        assert err_val.branch_index == 1
        with pytest.raises(AttributeError):
            err_val.error = "other"  # type: ignore[misc]

    def test_quorum_result_frozen(self) -> None:
        qr: QuorumResult = QuorumResult(
            entries=(
                OkValue(value="a", branch_index=0),
                ErrValue(error="fail", error_type="E", branch_index=1),
            ),
            quorum=1,
            total=2,
        )
        assert qr.quorum == 1
        assert qr.total == 2
        assert len(qr.entries) == 2
        with pytest.raises(AttributeError):
            qr.quorum = 2  # type: ignore[misc]

    def test_tolerated_loss_frozen(self) -> None:
        loss: ToleratedLoss = ToleratedLoss(
            path="0/parallel",
            branch_index=2,
            error="timeout",
            error_type="TimeoutError",
            quorum=2,
            total=3,
        )
        assert loss.branch_index == 2
        assert loss.quorum == 2
        with pytest.raises(AttributeError):
            loss.path = "other"  # type: ignore[misc]

    def test_parked_value_frozen(self) -> None:
        gate: OpenGateView = OpenGateView(
            gate_id="test:gate",
            workflow_id="test",
            node_id="test/node",
            phase=None,
            reason="test",
            stakes={},
            options=(),
        )
        parked: ParkedValue = ParkedValue(gates=(gate,), halt_run=False)
        assert len(parked.gates) == 1
        assert not parked.halt_run
        with pytest.raises(AttributeError):
            parked.halt_run = True  # type: ignore[misc]
