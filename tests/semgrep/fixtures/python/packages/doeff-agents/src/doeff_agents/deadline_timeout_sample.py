"""Fixture: banned `timeout_seconds` task field (L-K4-3 / ADR D13).

Wall-clock deadlines belong to the node spec (`deadline_seconds`); the
transport await budget is a pure keep-alive heartbeat and must never be
task-configurable. These constructions must each fire
`k4-deadline-not-transport-timeout`.
"""

from doeff_agents.effects import AgentSpec, AgentTask


def build_task_with_transport_timeout() -> AgentTask:
    return AgentTask(
        run_id="run-001",
        node_id="node-a",
        attempt=0,
        agent_type="codex",
        work_dir=".",
        prompt="return JSON",
        result_schema={},
        timeout_seconds=600.0,
    )


def build_spec_with_transport_timeout() -> AgentSpec:
    return AgentSpec(
        run_id="run-001",
        node_id="node-a",
        attempt=0,
        agent_type="codex",
        work_dir=".",
        prompt="return JSON",
        result_schema={},
        timeout_seconds=600.0,
    )
