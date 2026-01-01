"""Tests for agent adapters."""


from doeff_agents.adapters import (
    AgentType,
    ClaudeAdapter,
    CodexAdapter,
    GeminiAdapter,
    InjectionMethod,
    LaunchConfig,
)


class TestAgentType:
    """Tests for AgentType enum."""

    def test_agent_types(self) -> None:
        """Test all agent types exist."""
        assert AgentType.CLAUDE.value == "claude"
        assert AgentType.CODEX.value == "codex"
        assert AgentType.GEMINI.value == "gemini"
        assert AgentType.CUSTOM.value == "custom"


class TestInjectionMethod:
    """Tests for InjectionMethod enum."""

    def test_injection_methods(self) -> None:
        """Test all injection methods."""
        assert InjectionMethod.ARG.value == "arg"
        assert InjectionMethod.TMUX.value == "tmux"


class TestLaunchConfig:
    """Tests for LaunchConfig."""

    def test_launch_config_minimal(self) -> None:
        """Test minimal LaunchConfig."""
        from pathlib import Path

        config = LaunchConfig(agent_type=AgentType.CLAUDE, work_dir=Path("/tmp"))
        assert config.agent_type == AgentType.CLAUDE
        assert config.work_dir == Path("/tmp")
        assert config.prompt is None
        assert config.resume is False
        assert config.session_name is None
        assert config.profile is None

    def test_launch_config_full(self) -> None:
        """Test full LaunchConfig."""
        from pathlib import Path

        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            prompt="Fix the bug",
            resume=True,
            session_name="my-session",
            profile="default",
        )
        assert config.prompt == "Fix the bug"
        assert config.resume is True
        assert config.session_name == "my-session"
        assert config.profile == "default"


class TestClaudeAdapter:
    """Tests for Claude adapter."""

    def test_agent_type(self) -> None:
        """Test agent type is CLAUDE."""
        adapter = ClaudeAdapter()
        assert adapter.agent_type == AgentType.CLAUDE

    def test_injection_method_is_arg(self) -> None:
        """Test injection method is ARG."""
        adapter = ClaudeAdapter()
        assert adapter.injection_method == InjectionMethod.ARG

    def test_ready_pattern_is_none(self) -> None:
        """Test ready pattern is None."""
        adapter = ClaudeAdapter()
        assert adapter.ready_pattern is None

    def test_status_bar_lines(self) -> None:
        """Test status bar lines."""
        adapter = ClaudeAdapter()
        assert adapter.status_bar_lines == 5

    def test_launch_command_basic(self) -> None:
        """Test basic launch command."""
        from pathlib import Path

        adapter = ClaudeAdapter()
        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            prompt="Hello",
        )
        cmd = adapter.launch_command(config)
        assert cmd == ["claude", "--dangerously-skip-permissions", "Hello"]

    def test_launch_command_with_profile(self) -> None:
        """Test launch command with profile."""
        from pathlib import Path

        adapter = ClaudeAdapter()
        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            prompt="Hello",
            profile="dev",
        )
        cmd = adapter.launch_command(config)
        assert "--profile" in cmd
        assert "dev" in cmd

    def test_launch_command_resume(self) -> None:
        """Test launch command with resume."""
        from pathlib import Path

        adapter = ClaudeAdapter()
        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
            resume=True,
            session_name="old-session",
        )
        cmd = adapter.launch_command(config)
        assert "--resume" in cmd
        assert "old-session" in cmd

    def test_launch_command_no_prompt(self) -> None:
        """Test launch command without prompt."""
        from pathlib import Path

        adapter = ClaudeAdapter()
        config = LaunchConfig(
            agent_type=AgentType.CLAUDE,
            work_dir=Path("/tmp"),
        )
        cmd = adapter.launch_command(config)
        assert cmd == ["claude", "--dangerously-skip-permissions"]


class TestCodexAdapter:
    """Tests for Codex adapter."""

    def test_agent_type(self) -> None:
        """Test agent type is CODEX."""
        adapter = CodexAdapter()
        assert adapter.agent_type == AgentType.CODEX

    def test_injection_method_is_arg(self) -> None:
        """Test injection method is ARG."""
        adapter = CodexAdapter()
        assert adapter.injection_method == InjectionMethod.ARG

    def test_status_bar_lines(self) -> None:
        """Test status bar lines."""
        adapter = CodexAdapter()
        assert adapter.status_bar_lines == 3

    def test_launch_command_basic(self) -> None:
        """Test basic launch command."""
        from pathlib import Path

        adapter = CodexAdapter()
        config = LaunchConfig(
            agent_type=AgentType.CODEX,
            work_dir=Path("/tmp"),
            prompt="Hello",
        )
        cmd = adapter.launch_command(config)
        assert cmd == ["codex", "--full-auto", "Hello"]


class TestGeminiAdapter:
    """Tests for Gemini adapter."""

    def test_agent_type(self) -> None:
        """Test agent type is GEMINI."""
        adapter = GeminiAdapter()
        assert adapter.agent_type == AgentType.GEMINI

    def test_injection_method_is_tmux(self) -> None:
        """Test injection method is TMUX."""
        adapter = GeminiAdapter()
        assert adapter.injection_method == InjectionMethod.TMUX

    def test_ready_pattern_exists(self) -> None:
        """Test ready pattern is set."""
        adapter = GeminiAdapter()
        assert adapter.ready_pattern is not None
        assert "Type your message" in adapter.ready_pattern

    def test_status_bar_lines(self) -> None:
        """Test status bar lines."""
        adapter = GeminiAdapter()
        assert adapter.status_bar_lines == 3

    def test_launch_command_no_prompt_in_args(self) -> None:
        """Test prompt is not in launch command (sent via tmux)."""
        from pathlib import Path

        adapter = GeminiAdapter()
        config = LaunchConfig(
            agent_type=AgentType.GEMINI,
            work_dir=Path("/tmp"),
            prompt="Hello",
        )
        cmd = adapter.launch_command(config)
        # Prompt should NOT be in command - sent via tmux
        assert "Hello" not in cmd
        assert cmd == ["gemini"]
