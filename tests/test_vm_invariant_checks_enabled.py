"""ADR-DOE-ENFORCE-001 R4: doeff's pytest always runs with the VM conformance oracle enabled.

B3 ruling (2026-07-14): the oracle is a per-step run-time check, and when enabled, the VM execution of the whole pytest suite directly
exercises the oracle. The check is built into every build and is enabled at run time -- the root
conftest.py enables it with the ini value vm_invariant_checks (default true) (the earlier split of builds by cargo feature made the same venv
15 times faster or slower depending on the path that built it last).

This test is not skipped -- a run with the oracle disabled hard-fails (no false green,
ADR-DOE-ENFORCE-001 law `default-pytest-sees-all-enforcement`)。
"""

from doeff_core_effects import vm_probe_handler

from doeff import ReadInvariantChecks, run


def test_vm_built_with_invariant_checks():
    # If the VM binary is stale, vm_probe_handler fails with the ImportError "rebuild with `make sync`" (stale Rust VM build).
    assert run(vm_probe_handler(ReadInvariantChecks())), (
        "The VM oracle is disabled -- ADR-DOE-ENFORCE-001 R4. The root conftest.py should"
        "enable it with the ini value vm_invariant_checks -- check whether -o vm_invariant_checks=false was passed, or the run bypassed conftest"
    )


def test_a_child_cli_is_handed_the_session_oracle_explicitly():
    """Failure case: the parent does not write the flag to an environment variable, so unless the component that spawns the child CLI passes it, the child runs silently without the check.

    The one place where the 9 files of tests/cli spawn children (cli_child.with_settings) must put this session's VM state explicitly
    on the command line as DOEFF_VM_INVARIANT_CHECKS. A value the test passes itself wins over it.
    """
    from tests.cli.cli_child import with_settings

    assert "DOEFF_VM_INVARIANT_CHECKS=1" in with_settings(["true"], {}), with_settings(["true"], {})
    assert "DOEFF_VM_INVARIANT_CHECKS=0" in with_settings(["true"], {"DOEFF_VM_INVARIANT_CHECKS": "0"})
