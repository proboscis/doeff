"""Whether the VM's invariant checks are enabled or disabled is decided by a run-time switch in every build.

Before, builds were split by cargo feature, and the same venv changed speed by 15 times depending on the path that built it last (make sync / uv sync).
Now the check is in every build and is decided by DOEFF_VM_INVARIANT_CHECKS (read once by the extension at first) or
the effect SetInvariantChecks. This test sees in child processes that the same build is in both states by the variable alone.
"""

import subprocess
import sys

import pytest

PROBE = (
    "from doeff_core_effects._handlers import vm_probe_handler\n"
    "from doeff import ReadInvariantChecks, do, run\n"
    "@do\n"
    "def one():\n"
    "    return 1\n"
    "assert run(one()) == 1\n"
    "print(run(vm_probe_handler(ReadInvariantChecks())))\n"
)


def _probe(value: str | None) -> subprocess.CompletedProcess[str]:
    """To run one program in a child process given (or with removed) the variable, and read the state of the check."""
    # The child inherits this process's environment -- remove the variable (`env -u`), and if value is given, set it to that value.
    setting = [] if value is None else [f"DOEFF_VM_INVARIANT_CHECKS={value}"]
    return subprocess.run(
        ["env", "-u", "DOEFF_VM_INVARIANT_CHECKS", *setting, sys.executable, "-c", PROBE],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


@pytest.mark.parametrize(("value", "expected"), [(None, "False"), ("0", "False"), ("1", "True")])
def test_env_decides_the_checks_on_the_same_build(value: str | None, expected: str) -> None:
    result = _probe(value)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == expected


def test_unknown_value_is_refused() -> None:
    result = _probe("yes")
    assert result.returncode != 0
    assert "DOEFF_VM_INVARIANT_CHECKS" in result.stderr


def test_setter_turns_the_checks_on_and_off() -> None:
    from doeff_core_effects import vm_probe_handler

    from doeff import ReadInvariantChecks, SetInvariantChecks, do, run

    @do
    def switch_off_and_on():
        yield SetInvariantChecks(False)
        off = yield ReadInvariantChecks()
        yield SetInvariantChecks(True)
        on = yield ReadInvariantChecks()
        return off, on

    before = run(vm_probe_handler(ReadInvariantChecks()))
    try:
        assert run(vm_probe_handler(switch_off_and_on())) == (False, True)
    finally:
        run(vm_probe_handler(SetInvariantChecks(before)))
