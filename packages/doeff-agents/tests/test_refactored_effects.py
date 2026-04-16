"""Phase 1 TDD: LaunchEffect flattened, deprecated types removed."""

from pathlib import Path

import pytest

from doeff_agents.adapters.base import AgentType


class TestLaunchEffectFlattened:
    """LaunchEffect has flat fields — no LaunchConfig wrapper."""

    def test_launch_effect_flat_fields(self):
        from doeff_agents.effects.agent import LaunchEffect

        effect = LaunchEffect(
            session_name="test-session",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp/test"),
            prompt="hello",
            model="opus",
            ready_timeout=60.0,
        )
        assert effect.session_name == "test-session"
        assert effect.agent_type == AgentType.CLAUDE
        assert effect.work_dir == Path("/tmp/test")
        assert effect.prompt == "hello"
        assert effect.model == "opus"
        assert effect.ready_timeout == 60.0
        assert effect.mcp_tools == ()
        assert not hasattr(effect, "config"), "LaunchEffect must not have .config"

    def test_launch_effect_mcp_tools(self):
        from doeff.mcp import McpParamSchema, McpToolDef
        from doeff_agents.effects.agent import LaunchEffect

        tool = McpToolDef(
            name="test-tool",
            description="A test",
            params=(McpParamSchema(name="x", type="string", description="param"),),
            handler=lambda x: x,
        )
        effect = LaunchEffect(
            session_name="mcp-test",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            mcp_tools=(tool,),
        )
        assert len(effect.mcp_tools) == 1
        assert effect.mcp_tools[0].name == "test-tool"

    def test_launch_constructor_flat(self):
        from doeff_agents.effects.agent import Launch, LaunchEffect

        effect = Launch(
            "my-session",
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            prompt="hello",
        )
        assert isinstance(effect, LaunchEffect)
        assert effect.session_name == "my-session"
        assert effect.agent_type == AgentType.CLAUDE


class TestClaudeLaunchEffectSimplified:
    """ClaudeLaunchEffect has work_dir + prompt directly, no AgentTaskSpec."""

    def test_claude_launch_effect_flat(self):
        from doeff_agents.effects.agent import ClaudeLaunchEffect

        effect = ClaudeLaunchEffect(
            session_name="claude-test",
            work_dir=Path("/tmp/claude"),
            prompt="execute plan",
            model="opus",
            ready_timeout=120.0,
        )
        assert effect.session_name == "claude-test"
        assert effect.work_dir == Path("/tmp/claude")
        assert effect.prompt == "execute plan"
        assert not hasattr(effect, "task"), "ClaudeLaunchEffect must not have .task"
        assert not hasattr(effect, "agent_home"), "agent_home is handler-internal"
        assert not hasattr(effect, "trusted_workspaces"), "trusted_workspaces is handler-internal"

    def test_claude_launch_effect_mcp_tools(self):
        from doeff_agents.effects.agent import ClaudeLaunchEffect

        effect = ClaudeLaunchEffect(
            session_name="mcp",
            work_dir=Path("/tmp"),
            mcp_tools=("tool1",),
        )
        assert effect.mcp_tools == ("tool1",)


class TestDeprecatedTypesRemoved:
    """Deprecated types removed from effects/agent.py (still in old modules as compat)."""

    def test_launch_task_effect_is_deprecated_stub(self):
        from doeff_agents.effects.agent import LaunchTaskEffect
        # Still importable for backward compat, but is a stub
        assert hasattr(LaunchTaskEffect, "__dataclass_fields__")
        assert "session_name" in LaunchTaskEffect.__dataclass_fields__

    def test_no_agent_task_spec_in_effects(self):
        from doeff_agents.effects import agent
        assert not hasattr(agent, "AgentTaskSpec"), "AgentTaskSpec removed from effects"

    def test_no_workspace_file_in_effects(self):
        from doeff_agents.effects import agent
        assert not hasattr(agent, "WorkspaceFile"), "WorkspaceFile removed from effects"

    def test_no_expected_artifact_in_effects(self):
        from doeff_agents.effects import agent
        assert not hasattr(agent, "ExpectedArtifact"), "ExpectedArtifact removed from effects"

    def test_launch_config_kept_for_imperative_api(self):
        from doeff_agents.adapters.base import LaunchConfig, LaunchParams
        # LaunchConfig is separate (has agent_type), LaunchParams is adapter-only
        assert LaunchConfig is not LaunchParams
        assert hasattr(LaunchConfig, "__dataclass_fields__")
        assert "agent_type" in LaunchConfig.__dataclass_fields__
        assert "agent_type" not in LaunchParams.__dataclass_fields__
