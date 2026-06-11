"""K4 integration tests — parallel agent branches must overlap (L-K4-2).

These tests verify that the ``make_offloaded_scheduled_handler`` bridge
enables true concurrency for ``parallel`` branches by asserting:

1. Session lifetimes of sibling branches **intersect** (overlap).
2. ``wall_clock(parallel(a, b)) < wall_clock(a) + wall_clock(b)``
   (sub-additive wall time — parallelism is real, not serialised).

The tests use the real cooperative scheduler (``scheduled``) with a
time-delayed mock agent handler to simulate agentd sessions that take
measurable wall time, verifying that the ``ExternalPromise`` + daemon-
thread bridge yields to the scheduler between branches.
"""

import threading
import time
from pathlib import Path
from typing import Any

from doeff import Effect, Pass, Resume, Spawn, WithHandler, do, run
from doeff_core_effects.scheduler import Gather, scheduled
from doeff_conductor.effects.agent import AgentEffect, AgentTask
from doeff_conductor.effects.workspace import CreateWorkspace
from doeff_conductor.handlers.testing import MockConductorRuntime
from doeff_conductor.handlers.utils import (
    make_offloaded_scheduled_handler,
    make_scheduled_handler,
)


SIMPLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
    "additionalProperties": False,
}


def _make_agent_task(
    run_id: str,
    node_id: str,
    workspace: Any,
    *,
    attempt: int = 0,
) -> AgentTask:
    """Build a minimal AgentTask for testing."""
    return AgentTask(
        run_id=run_id,
        node_id=node_id,
        attempt=attempt,
        env=workspace,
        prompt=f"test prompt for {node_id}",
        result_schema=SIMPLE_SCHEMA,
        verification_class="test-verifiable",
        agent_type="codex",
    )


class _TimedAgentHandler:
    """Mock agent handler that records start/end times per session.

    Each ``handle_agent`` call sleeps for ``delay`` seconds to simulate
    a real agentd ``await_result`` RPC, then delegates to the underlying
    ``MockConductorRuntime.handle_agent`` for deterministic scripting.
    """

    def __init__(
        self,
        runtime: MockConductorRuntime,
        delay: float,
    ) -> None:
        self._runtime = runtime
        self._delay = delay
        self._lock = threading.Lock()
        self.events: dict[str, dict[str, float]] = {}

    def handle_agent(self, effect: AgentEffect) -> object:
        session_id: str = effect.task.session_id
        with self._lock:
            self.events[session_id] = {"start": time.monotonic()}
        time.sleep(self._delay)
        result = self._runtime.handle_agent(effect)
        with self._lock:
            self.events[session_id]["end"] = time.monotonic()
        return result


def _build_handler_with_real_scheduler(
    runtime: MockConductorRuntime,
    timed_handler: _TimedAgentHandler,
) -> Any:
    """Build a handler-protocol callable using the real scheduler.

    Uses ``make_offloaded_scheduled_handler`` for ``AgentEffect`` (the
    bridge under test) and ``make_scheduled_handler`` for all other
    conductor effects.
    """
    from doeff_conductor.effects.dsl import RandomCall, TimeCall
    from doeff_conductor.effects.exec import Exec
    from doeff_conductor.effects.git import Commit, CreatePR, MergePR, Push
    from doeff_conductor.effects.issue import (
        CreateIssue,
        GetIssue,
        ListIssues,
        ResolveIssue,
    )
    from doeff_conductor.effects.workspace import (
        DeleteWorkspace,
        MergeWorkspaces,
    )
    from doeff_conductor.workflow_effect_journal import JournaledWorkflowEffectHandler

    workflow_effect = JournaledWorkflowEffectHandler(state_dir=runtime.root)

    handlers: tuple[tuple[type[Any], Any], ...] = (
        (CreateWorkspace, make_scheduled_handler(runtime.handle_create_workspace)),
        (MergeWorkspaces, make_scheduled_handler(runtime.handle_merge_workspaces)),
        (DeleteWorkspace, make_scheduled_handler(runtime.handle_delete_workspace)),
        (Exec, make_scheduled_handler(runtime.handle_exec)),
        (CreateIssue, make_scheduled_handler(runtime.handle_create_issue)),
        (ListIssues, make_scheduled_handler(runtime.handle_list_issues)),
        (GetIssue, make_scheduled_handler(runtime.handle_get_issue)),
        (ResolveIssue, make_scheduled_handler(runtime.handle_resolve_issue)),
        # --- THE BRIDGE UNDER TEST ---
        (AgentEffect, make_offloaded_scheduled_handler(timed_handler.handle_agent)),
        (TimeCall, make_scheduled_handler(workflow_effect.handle_time)),
        (RandomCall, make_scheduled_handler(workflow_effect.handle_random)),
        (Commit, make_scheduled_handler(runtime.handle_commit)),
        (Push, make_scheduled_handler(runtime.handle_push)),
        (CreatePR, make_scheduled_handler(runtime.handle_create_pr)),
        (MergePR, make_scheduled_handler(runtime.handle_merge_pr)),
    )

    @do
    def handler(effect: Effect, k: Any):
        for effect_type, effect_handler in handlers:
            if isinstance(effect, effect_type):
                return (yield effect_handler(effect, k))
        yield Pass(effect, k)

    return handler


class TestK4ParallelOverlap:
    """L-K4-2: parallel agent branches must show session overlap."""

    BRANCH_DELAY: float = 0.4  # seconds each branch "works"

    def test_parallel_branches_overlap(self, tmp_path: Path) -> None:
        """Two parallel agent branches run concurrently, not serially.

        Asserts:
        - Session lifetimes intersect (overlap).
        - Wall clock of parallel < sum of branch times.
        """
        runtime = MockConductorRuntime(tmp_path)

        # Configure deterministic scripts for both branches
        runtime.configure_agent_script(
            "run-k4-branch-a-0",
            [{"summary": "branch a done"}],
        )
        runtime.configure_agent_script(
            "run-k4-branch-b-0",
            [{"summary": "branch b done"}],
        )

        timed = _TimedAgentHandler(runtime, delay=self.BRANCH_DELAY)
        conductor_handler = _build_handler_with_real_scheduler(runtime, timed)

        @do
        def parallel_workflow():
            workspace = yield CreateWorkspace(workspace_id="ws-k4")
            task_a = _make_agent_task("run-k4", "branch-a", workspace)
            task_b = _make_agent_task("run-k4", "branch-b", workspace)

            t1 = yield Spawn(AgentEffect(task=task_a))
            t2 = yield Spawn(AgentEffect(task=task_b))
            results = yield Gather(t1, t2)
            return results

        wall_start = time.monotonic()
        result = run(scheduled(WithHandler(conductor_handler, parallel_workflow())))
        wall_elapsed = time.monotonic() - wall_start

        # Unwrap RunResult if needed
        result_value = result.value if hasattr(result, "value") else result

        # Both branches completed successfully (Gather returns a list)
        assert list(result_value) == [{"summary": "branch a done"}, {"summary": "branch b done"}]

        # Retrieve timing events
        assert "run-k4-branch-a-0" in timed.events, "branch-a never ran"
        assert "run-k4-branch-b-0" in timed.events, "branch-b never ran"
        ev_a = timed.events["run-k4-branch-a-0"]
        ev_b = timed.events["run-k4-branch-b-0"]

        # L-K4-2 overlap: a.start < b.end AND b.start < a.end
        assert ev_a["start"] < ev_b["end"], "branch-a must start before branch-b ends"
        assert ev_b["start"] < ev_a["end"], "branch-b must start before branch-a ends"

        # L-K4-2 sub-additive wall time
        sum_of_branches = self.BRANCH_DELAY * 2
        assert wall_elapsed < sum_of_branches, (
            f"wall_clock(parallel)={wall_elapsed:.3f}s must be < "
            f"sum_of_branches={sum_of_branches:.3f}s"
        )

    def test_parallel_branches_overlap_repeated(self, tmp_path: Path) -> None:
        """Run the overlap test 3x to guard against scheduling flakes."""
        for iteration in range(3):
            iteration_dir = tmp_path / f"iter-{iteration}"
            iteration_dir.mkdir()
            runtime = MockConductorRuntime(iteration_dir)
            runtime.configure_agent_script(
                f"run-k4r{iteration}-branch-a-0",
                [{"summary": f"a-{iteration}"}],
            )
            runtime.configure_agent_script(
                f"run-k4r{iteration}-branch-b-0",
                [{"summary": f"b-{iteration}"}],
            )

            timed = _TimedAgentHandler(runtime, delay=self.BRANCH_DELAY)
            conductor_handler = _build_handler_with_real_scheduler(runtime, timed)

            @do
            def workflow():
                workspace = yield CreateWorkspace(workspace_id=f"ws-k4r{iteration}")
                task_a = _make_agent_task(
                    f"run-k4r{iteration}", "branch-a", workspace
                )
                task_b = _make_agent_task(
                    f"run-k4r{iteration}", "branch-b", workspace
                )
                t1 = yield Spawn(AgentEffect(task=task_a))
                t2 = yield Spawn(AgentEffect(task=task_b))
                results = yield Gather(t1, t2)
                return results

            wall_start = time.monotonic()
            result = run(scheduled(WithHandler(conductor_handler, workflow())))
            wall_elapsed = time.monotonic() - wall_start

            result_value = result.value if hasattr(result, "value") else result
            assert list(result_value) == [
                {"summary": f"a-{iteration}"},
                {"summary": f"b-{iteration}"},
            ], f"iteration {iteration}: unexpected result"

            sum_of_branches = self.BRANCH_DELAY * 2
            assert wall_elapsed < sum_of_branches, (
                f"iteration {iteration}: "
                f"wall_clock={wall_elapsed:.3f}s >= sum={sum_of_branches:.3f}s"
            )

    def test_serial_handler_is_slower(self, tmp_path: Path) -> None:
        """Prove the old blocking handler serialises branches.

        Uses ``make_scheduled_handler`` (the blocking path) and verifies
        that wall time is at least the sum of branch delays — confirming
        the fix is necessary.
        """
        runtime = MockConductorRuntime(tmp_path)
        runtime.configure_agent_script(
            "run-serial-branch-a-0",
            [{"summary": "serial a"}],
        )
        runtime.configure_agent_script(
            "run-serial-branch-b-0",
            [{"summary": "serial b"}],
        )

        timed = _TimedAgentHandler(runtime, delay=self.BRANCH_DELAY)

        # Use the OLD blocking handler (pre-fix behaviour)
        from doeff_conductor.effects.agent import AgentEffect as AE
        from doeff_conductor.effects.workspace import CreateWorkspace as CW

        @do
        def blocking_handler(effect: Effect, k: Any):
            if isinstance(effect, AE):
                return (yield Resume(k, timed.handle_agent(effect)))
            if isinstance(effect, CW):
                return (yield Resume(k, runtime.handle_create_workspace(effect)))
            yield Pass(effect, k)

        @do
        def workflow():
            workspace = yield CreateWorkspace(workspace_id="ws-serial")
            task_a = _make_agent_task("run-serial", "branch-a", workspace)
            task_b = _make_agent_task("run-serial", "branch-b", workspace)
            t1 = yield Spawn(AgentEffect(task=task_a))
            t2 = yield Spawn(AgentEffect(task=task_b))
            results = yield Gather(t1, t2)
            return results

        wall_start = time.monotonic()
        result = run(scheduled(WithHandler(blocking_handler, workflow())))
        wall_elapsed = time.monotonic() - wall_start

        # The blocking handler should take >= sum of branch delays
        sum_of_branches = self.BRANCH_DELAY * 2
        assert wall_elapsed >= sum_of_branches * 0.9, (
            f"blocking handler should serialise: "
            f"wall_clock={wall_elapsed:.3f}s < expected_min={sum_of_branches * 0.9:.3f}s"
        )
