"""Test that RunResult.display shows redundant environment settings."""

from doeff import Program, ProgramInterpreter, do
from doeff.effects import Dep, Local


def test_display_shows_redundant_env_settings():
    """Verify that display() identifies and shows unused environment variables."""
    from doeff.types import ExecutionContext, WGraph, WStep, WNode

    # Create a program that only uses 'used_var' via Dep
    @do
    def my_program():
        used = yield Dep("used_var")
        return f"Got: {used}"

    # Provide env with both used and unused variables via initial context
    env = {
        "used_var": "I am used",
        "unused_var_1": "I am not used",
        "unused_var_2": "Also not used",
    }

    # Create initial context with the environment
    initial_context = ExecutionContext(
        env=env,
        state={},
        log=[],
        graph=WGraph(
            last=WStep(inputs=(), output=WNode("_root"), meta={}),
            steps=frozenset(),
        ),
        io_allowed=True,
        program_call_stack=[],
    )

    # Run the program with the initial context
    program = my_program()
    interpreter = ProgramInterpreter()
    result = interpreter.run(program, context=initial_context)

    # Get the display output with verbose=True (required for env section)
    display_output = result.display(verbose=True)

    # Verify the output contains the environment section
    assert "🌍 Environment:" in display_output

    # Verify it shows "Used:" section
    assert "Used:" in display_output
    assert "used_var" in display_output

    # Verify it shows "Redundant (not requested):" section
    assert "Redundant (not requested):" in display_output
    assert "unused_var_1" in display_output
    assert "unused_var_2" in display_output


def test_display_no_redundant_when_all_used():
    """Verify that no redundant section appears when all env vars are used."""
    from doeff.types import ExecutionContext, WGraph, WStep, WNode

    @do
    def my_program():
        var1 = yield Dep("var1")
        var2 = yield Dep("var2")
        return f"{var1}, {var2}"

    env = {
        "var1": "value1",
        "var2": "value2",
    }

    initial_context = ExecutionContext(
        env=env,
        state={},
        log=[],
        graph=WGraph(
            last=WStep(inputs=(), output=WNode("_root"), meta={}),
            steps=frozenset(),
        ),
        io_allowed=True,
        program_call_stack=[],
    )

    program = my_program()
    interpreter = ProgramInterpreter()
    result = interpreter.run(program, context=initial_context)

    display_output = result.display(verbose=True)

    # Should show Used section
    assert "Used:" in display_output
    assert "var1" in display_output
    assert "var2" in display_output

    # Should NOT show redundant section since all vars are used
    assert "Redundant (not requested):" not in display_output


def test_display_all_redundant_when_none_used():
    """Verify that all env vars shown as redundant when none are used."""
    from doeff.types import ExecutionContext, WGraph, WStep, WNode

    # Program that doesn't use any env vars
    my_program = Program.pure(42)

    env = {
        "unused1": "value1",
        "unused2": "value2",
    }

    initial_context = ExecutionContext(
        env=env,
        state={},
        log=[],
        graph=WGraph(
            last=WStep(inputs=(), output=WNode("_root"), meta={}),
            steps=frozenset(),
        ),
        io_allowed=True,
        program_call_stack=[],
    )

    interpreter = ProgramInterpreter()
    result = interpreter.run(my_program, context=initial_context)

    display_output = result.display(verbose=True)

    # Should show environment section
    assert "🌍 Environment:" in display_output

    # Should NOT show Used section (no vars were used)
    assert "Used:" not in display_output

    # Should show all as redundant
    assert "Redundant (not requested):" in display_output
    assert "unused1" in display_output
    assert "unused2" in display_output


def test_display_no_env_section_when_not_verbose():
    """Verify that environment section is hidden when verbose=False."""
    from doeff.types import ExecutionContext, WGraph, WStep, WNode

    @do
    def my_program():
        used = yield Dep("used_var")
        return used

    env = {
        "used_var": "value",
        "unused_var": "unused",
    }

    initial_context = ExecutionContext(
        env=env,
        state={},
        log=[],
        graph=WGraph(
            last=WStep(inputs=(), output=WNode("_root"), meta={}),
            steps=frozenset(),
        ),
        io_allowed=True,
        program_call_stack=[],
    )

    program = my_program()
    interpreter = ProgramInterpreter()
    result = interpreter.run(program, context=initial_context)

    display_output = result.display(verbose=False)

    # Environment section should not appear when verbose=False
    assert "🌍 Environment:" not in display_output
    assert "Redundant (not requested):" not in display_output
