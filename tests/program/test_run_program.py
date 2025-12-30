"""Tests for the run_program Python API."""

from __future__ import annotations

import pytest

from doeff import ExecutionContext, Program, ProgramInterpreter, ProgramRunResult, do, run_program
from doeff.effects import Ask


class TestRunProgramBasic:
    """Basic tests for run_program function."""

    def test_run_with_explicit_interpreter(self) -> None:
        """Test running a program with explicit interpreter."""
        result = run_program(
            "tests.cli_assets.sample_program",
            interpreter="tests.cli_assets.sync_interpreter",
        )

        assert isinstance(result, ProgramRunResult)
        assert result.value == 5
        assert result.interpreter_path == "tests.cli_assets.sync_interpreter"

    def test_run_with_program_instance(self) -> None:
        """Test running a Program instance directly."""
        my_program: Program[int] = Program.pure(42)

        result = run_program(
            my_program,
            interpreter="tests.cli_assets.sync_interpreter",
        )

        assert result.value == 42

    def test_run_with_env_string(self) -> None:
        """Test running a program with environment as string path."""
        result = run_program(
            "tests.cli_assets.ask_program",
            interpreter="tests.cli_assets.runresult_interpreter",
            envs=["tests.cli_assets.sample_env"],
        )

        assert result.value == 5
        assert "tests.cli_assets.sample_env" in result.env_sources

    def test_run_with_apply(self) -> None:
        """Test running a program with apply (Kleisli transformation)."""
        result = run_program(
            "tests.cli_assets.sample_program",
            interpreter="tests.cli_assets.sync_interpreter",
            apply="tests.cli_assets.double_program",
        )

        assert result.value == 10
        assert result.applied_kleisli == "tests.cli_assets.double_program"

    def test_run_with_transform(self) -> None:
        """Test running a program with transform."""
        result = run_program(
            "tests.cli_assets.sample_program",
            interpreter="tests.cli_assets.sync_interpreter",
            transform=["tests.cli_assets.add_three"],
        )

        assert result.value == 8
        assert "tests.cli_assets.add_three" in result.applied_transforms

    def test_run_with_apply_and_transform(self) -> None:
        """Test running a program with both apply and transform."""
        result = run_program(
            "tests.cli_assets.sample_program",
            interpreter="tests.cli_assets.sync_interpreter",
            apply="tests.cli_assets.double_program",
            transform=["tests.cli_assets.add_three"],
        )

        # apply first (5 * 2 = 10), then transform (10 + 3 = 13)
        assert result.value == 13

    def test_run_quiet_mode(self) -> None:
        """Test running a program with quiet mode to suppress output."""
        result = run_program(
            "tests.cli_assets.sample_program",
            interpreter="tests.cli_assets.sync_interpreter",
            quiet=True,
        )

        assert result.value == 5


class TestRunProgramWithObjects:
    """Tests for run_program with actual objects instead of string paths."""

    def test_run_with_program_dict_env(self) -> None:
        """Test running a program with Program[dict] environment."""

        @do
        def ask_program():
            value = yield Ask("my_key")
            return value

        my_env: Program[dict] = Program.pure({"my_key": "hello"})

        result = run_program(
            ask_program(),
            envs=[my_env],
        )

        assert result.value == "hello"
        assert "<Program[dict]>" in result.env_sources

    def test_run_with_dict_env(self) -> None:
        """Test running a program with direct dict environment."""

        @do
        def ask_program():
            value = yield Ask("my_key")
            return value

        result = run_program(
            ask_program(),
            envs=[{"my_key": 42}],
        )

        assert result.value == 42
        assert "<dict>" in result.env_sources

    def test_run_with_mixed_envs(self) -> None:
        """Test running a program with mixed environment types."""

        @do
        def ask_program():
            a = yield Ask("a")
            b = yield Ask("b")
            return a + b

        env_program: Program[dict] = Program.pure({"a": 10})
        env_dict = {"b": 20}

        result = run_program(
            ask_program(),
            envs=[env_program, env_dict],
        )

        assert result.value == 30
        assert "<Program[dict]>" in result.env_sources
        assert "<dict>" in result.env_sources

    def test_run_with_callable_kleisli(self) -> None:
        """Test running a program with a callable as kleisli."""

        @do
        def double_kleisli(prog: Program[int]) -> int:
            value = yield prog
            return value * 2

        my_program: Program[int] = Program.pure(5)

        result = run_program(
            my_program,
            apply=double_kleisli,
        )

        assert result.value == 10
        assert "double_kleisli" in result.applied_kleisli

    def test_run_with_callable_transform(self) -> None:
        """Test running a program with a callable as transform."""

        def add_ten(prog: Program[int]) -> Program[int]:
            return prog.map(lambda x: x + 10)

        my_program: Program[int] = Program.pure(5)

        result = run_program(
            my_program,
            transform=[add_ten],
        )

        assert result.value == 15
        assert "add_ten" in result.applied_transforms[0]

    def test_run_with_interpreter_instance(self) -> None:
        """Test running a program with a ProgramInterpreter instance."""
        my_program: Program[int] = Program.pure(42)
        my_interpreter = ProgramInterpreter()

        result = run_program(
            my_program,
            interpreter=my_interpreter,
        )

        assert result.value == 42
        assert "ProgramInterpreter" in result.interpreter_path

    def test_run_with_callable_interpreter(self) -> None:
        """Test running a program with a callable interpreter."""

        def my_interpreter(prog: Program[int]) -> int:
            return ProgramInterpreter().run(prog).value * 2

        my_program: Program[int] = Program.pure(5)

        result = run_program(
            my_program,
            interpreter=my_interpreter,
        )

        assert result.value == 10
        assert "my_interpreter" in result.interpreter_path


class TestRunProgramAutoDiscovery:
    """Tests for auto-discovery functionality."""

    def test_auto_discover_interpreter_and_envs(self) -> None:
        """Test auto-discovery of interpreter and environments."""
        result = run_program(
            "tests.fixtures_discovery.myapp.features.auth.login.login_program",
            quiet=True,
        )

        assert result.value == "Login via oauth2 (timeout: 10s)"
        # Should discover auth_interpreter (closest to login module)
        assert "auth_interpreter" in result.interpreter_path
        # Should discover all envs in hierarchy
        assert len(result.env_sources) == 3

    def test_manual_interpreter_overrides_discovery(self) -> None:
        """Test that explicit interpreter overrides auto-discovery."""
        result = run_program(
            "tests.fixtures_discovery.myapp.features.auth.login.login_program",
            interpreter="tests.fixtures_discovery.myapp.base_interpreter",
            quiet=True,
        )

        assert result.interpreter_path == "tests.fixtures_discovery.myapp.base_interpreter"


class TestRunProgramDefaultEnv:
    """Tests for default environment loading from ~/.doeff.py."""

    def test_load_default_env_enabled_by_default(self) -> None:
        """Test that default env loading is enabled by default."""
        # This test verifies that ~/.doeff.py is loaded when running a program
        # The actual loading happens and we can verify by checking env_sources
        result = run_program(
            "tests.cli_assets.sample_program",
            interpreter="tests.cli_assets.sync_interpreter",
            quiet=True,
        )
        # If ~/.doeff.py exists and has __default_env__, it should be in sources
        # Otherwise, this just confirms the parameter works
        assert result.value == 5

    def test_load_default_env_disabled(self) -> None:
        """Test that default env loading can be disabled."""
        result = run_program(
            "tests.cli_assets.sample_program",
            interpreter="tests.cli_assets.sync_interpreter",
            quiet=True,
            load_default_env=False,
        )
        # With load_default_env=False, ~/.doeff.py should not be in sources
        assert "~/.doeff.py" not in str(result.env_sources)
        assert result.value == 5


class TestRunProgramErrors:
    """Tests for error handling."""

    def test_no_default_interpreter_raises(self) -> None:
        """Test that missing interpreter raises RuntimeError."""
        with pytest.raises(RuntimeError, match="No default interpreter found"):
            run_program(
                "tests.cli_assets.sample_program",
                quiet=True,
            )


class TestRunProgramResult:
    """Tests for ProgramRunResult structure."""

    def test_run_result_contains_full_run_result(self) -> None:
        """Test that run_result contains the full RunResult."""
        result = run_program(
            "tests.cli_assets.sample_program",
            interpreter="tests.cli_assets.runresult_interpreter",
        )

        assert result.run_result is not None
        assert result.run_result.value == result.value

    def test_run_result_context_accessible(self) -> None:
        """Test that execution context is accessible from run_result."""
        result = run_program(
            "tests.cli_assets.ask_program",
            interpreter="tests.cli_assets.runresult_interpreter",
            envs=["tests.cli_assets.sample_env"],
        )

        assert result.run_result is not None
        assert result.run_result.context is not None


class TestEnvAliasResolution:
    """Tests for resolving Program-like env values lazily via Ask."""

    def test_env_value_program_like_is_resolved_on_ask(self) -> None:
        """Ensure env entries containing Programs/Effects are executed on demand."""

        @do
        def program():
            return (yield Ask("api_key"))

        ctx = ExecutionContext(
            env={
                "api_key": Ask("api_key__internal"),
                "api_key__internal": "secret_value",
            }
        )

        interpreter = ProgramInterpreter()
        result = interpreter.run(program(), ctx)

        assert result.value == "secret_value"
        assert result.context.env["api_key"] == "secret_value"
