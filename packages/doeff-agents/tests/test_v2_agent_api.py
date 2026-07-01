"""V2 agent effect API keeps v1 compatibility while making outcomes explicit."""

from __future__ import annotations

from pathlib import Path

import pytest
from doeff_agents import AgentType

RESULT_SCHEMA = {
    "type": "object",
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
}


def test_v1_effect_imports_remain_available() -> None:
    from doeff_agents.effects.agent import AgentEffect, AgentSpec, LaunchEffect

    assert LaunchEffect.__name__ == "LaunchEffect"
    assert AgentSpec.__name__ == "AgentSpec"
    assert AgentEffect.__name__ == "AgentEffect"


def test_v2_exports_outcome_explicit_names() -> None:
    from doeff_agents.v2 import (
        AgentInvocationSpec,
        AgentSessionSpec,
        InvokeAgentEffect,
        StartAgentSessionEffect,
    )

    assert AgentSessionSpec.__name__ == "AgentSessionSpec"
    assert AgentInvocationSpec.__name__ == "AgentInvocationSpec"
    assert StartAgentSessionEffect.__name__ == "LaunchEffect"
    assert InvokeAgentEffect.__name__ == "AgentEffect"


def test_agent_session_spec_has_no_result_schema(tmp_path: Path) -> None:
    from doeff_agents.v2 import AgentSessionSpec

    spec = AgentSessionSpec(
        session_name="interactive",
        agent_type=AgentType.CODEX,
        work_dir=tmp_path,
        prompt="stay alive",
    )

    assert not hasattr(spec, "result_schema")
    with pytest.raises(TypeError):
        AgentSessionSpec(
            session_name="bad",
            agent_type=AgentType.CODEX,
            work_dir=tmp_path,
            result_schema=RESULT_SCHEMA,  # type: ignore[call-arg]
        )


def test_agent_invocation_spec_requires_result_schema(tmp_path: Path) -> None:
    from doeff_agents.v2 import AgentInvocationSpec

    with pytest.raises(TypeError):
        AgentInvocationSpec(
            run_id="run-001",
            node_id="triage",
            attempt=0,
            agent_type=AgentType.CODEX,
            work_dir=tmp_path,
            prompt="return data",
        )

    spec = AgentInvocationSpec(
        run_id="run-001",
        node_id="triage",
        attempt=0,
        agent_type=AgentType.CODEX,
        work_dir=tmp_path,
        prompt="return data",
        result_schema=RESULT_SCHEMA,
    )

    assert spec.result_schema is RESULT_SCHEMA
    assert spec.session_id == "run-001-triage-0"


def test_start_agent_session_lowers_to_no_result_launch_effect(tmp_path: Path) -> None:
    from doeff_agents.effects.agent import LaunchEffect
    from doeff_agents.v2 import AgentSessionSpec, StartAgentSession

    effect = StartAgentSession(
        AgentSessionSpec(
            session_name="manual",
            agent_type=AgentType.CLAUDE,
            work_dir=tmp_path,
            prompt="hello",
        )
    )

    assert isinstance(effect, LaunchEffect)
    assert effect.session_name == "manual"
    assert not hasattr(effect, "result_schema")


def test_invoke_agent_lowers_to_schemaful_agent_effect(tmp_path: Path) -> None:
    from doeff_agents.effects.agent import AgentEffect
    from doeff_agents.v2 import AgentInvocationSpec, InvokeAgent

    effect = InvokeAgent(
        AgentInvocationSpec(
            run_id="run-001",
            node_id="triage",
            attempt=0,
            agent_type=AgentType.CODEX,
            work_dir=tmp_path,
            prompt="return data",
            result_schema=RESULT_SCHEMA,
            max_retries=3,
        )
    )

    assert isinstance(effect, AgentEffect)
    assert effect.task.result_schema is RESULT_SCHEMA
    assert effect.task.max_retries == 3
    assert effect.task.session_id == "run-001-triage-0"


def test_start_agent_invocation_lowers_to_schemaful_launch_session(tmp_path: Path) -> None:
    from doeff_agents.effects.agent import LaunchSessionEffect
    from doeff_agents.v2 import AgentInvocationSpec, StartAgentInvocation

    effect = StartAgentInvocation(
        AgentInvocationSpec(
            run_id="run-001",
            node_id="eval",
            attempt=1,
            agent_type=AgentType.CODEX,
            work_dir=tmp_path,
            prompt="return data",
            result_schema=RESULT_SCHEMA,
        )
    )

    assert isinstance(effect, LaunchSessionEffect)
    assert effect.spec.result_schema is RESULT_SCHEMA
    assert effect.spec.session_id == "run-001-eval-1"
