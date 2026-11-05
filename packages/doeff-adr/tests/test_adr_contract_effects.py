"""The operation that checks the shape of an ADR is the effect AssertAdrContract / AssertAllAdrContracts, and the operation that puts a deftest check in the table is the effect RegisterDeftest,
both answered by adr_registry, the same handler factory as GetAdr (ADR-DOE-API-001 R30).

Decision: fix the remaining names in doeff-adr in the same direction. The former functions assert_adr_contract, assert_all_adr_contracts and register_deftest_enforcement have no callers in other repos, so they were removed without leaving a
migration layer.

adr_registry() answers from the process registry, and adr_registry(specs=[...], enforcements={...}) answers only from the given list and dict (the one not given is
empty). The innermost installed handler answers first, so a test can check an ADR with a missing check without touching the registry. The expansion of the macro defadr also runs this
effect under this handler.
"""

import textwrap

import doeff_adr
import doeff_adr._registry as registry_module
import doeff_adr.macros as macros_module
import hy
import pytest
from doeff_adr import (
    AdrSpec,
    AdrStatus,
    AssertAdrContract,
    AssertAllAdrContracts,
    EnforcementMode,
    EnforcementRef,
    GetEnforcement,
    RegisterAdr,
    RegisterDeftest,
    SemgrepSpec,
    adr_registry,
)
from doeff_adr._registry import isolated_registry

from doeff import do, run

pytest_plugins = ["pytester"]


def _spec(
    adr_id: str,
    *refs: EnforcementRef,
    status: AdrStatus = "accepted",
    plans: tuple[str, ...] = (),
) -> AdrSpec:
    """An ADR value that lists check references refs (a test that swaps the table passes it without going through the registry)."""
    return AdrSpec(id=adr_id, title=adr_id, status=status, enforcement=refs, plans=plans)


def _deftest(enforcement_id: str, mode: EnforcementMode = "green") -> EnforcementRef:
    """Build a deftest check reference (listed by an ADR / present in the table) as an easy-to-compare single line."""
    return EnforcementRef(id=enforcement_id, kind="deftest", mode=mode)


@do
def _failure_of(effect):
    """Raise the effect and return the text of the failure received at the raise site ("passed" if it does not fail) -- to see that a failure returns to the site that raised the effect."""
    try:
        yield effect
    except (AssertionError, KeyError, ValueError) as failed:
        return f"{type(failed).__name__}: {failed}"
    return "passed"


def test_a_registered_adr_with_its_registered_check_passes() -> None:
    """Process registry: an adopted ADR whose listed checks were registered with RegisterDeftest answers AssertAdrContract with None."""

    @do
    def body():
        ref = yield RegisterDeftest("test_probe_contract")
        return ref, (yield AssertAdrContract("ADR-PROBE-001"))

    with isolated_registry():
        run(
            adr_registry()(
                RegisterAdr(
                    "ADR-PROBE-001",
                    title="probe",
                    status="accepted",
                    enforcement=[_deftest("test_probe_contract")],
                )
            )
        )
        ref, answer = run(adr_registry()(body()))
        assert ref == _deftest("test_probe_contract")
        assert run(adr_registry()(GetEnforcement("test_probe_contract"))) is ref
        assert answer is None


def test_an_adr_whose_check_is_missing_fails_where_the_effect_was_performed() -> None:
    """An ADR with a missing check: if a listed check is not in the table, AssertionError returns to the site that raised AssertAdrContract."""
    registry = adr_registry(specs=[_spec("ADR-PROBE-001", _deftest("test_gone"))])
    with isolated_registry():
        assert run(registry(_failure_of(AssertAdrContract("ADR-PROBE-001")))) == (
            "AssertionError: ADR-PROBE-001: enforcement 'test_gone' is not registered"
        )


@pytest.mark.parametrize(
    ("spec", "failure"),
    [
        (
            _spec("ADR-PROBE-001"),
            "AssertionError: ADR-PROBE-001: accepted ADR must have executable enforcement",
        ),
        (
            _spec("ADR-PROBE-001", _deftest("test_red", mode="expected-red")),
            "AssertionError: ADR-PROBE-001: expected-red enforcement 'test_red' must be tied to a plan",
        ),
    ],
    ids=["accepted-without-checks", "expected-red-without-plan"],
)
def test_the_other_contract_failures_are_assertion_errors(spec: AdrSpec, failure: str) -> None:
    registry = adr_registry(
        specs=[spec], enforcements={"test_red": _deftest("test_red", mode="expected-red")}
    )
    assert run(registry(_failure_of(AssertAdrContract("ADR-PROBE-001")))) == failure


def test_an_expected_red_check_tied_to_a_plan_and_a_proposed_adr_without_checks_pass() -> None:
    registry = adr_registry(
        specs=[
            _spec("ADR-PROBE-001", _deftest("test_red", mode="expected-red"), plans=("plan-1",)),
            _spec("ADR-PROBE-002", status="proposed"),
        ],
        enforcements={"test_red": _deftest("test_red", mode="expected-red")},
    )
    assert run(registry(_failure_of(AssertAdrContract("ADR-PROBE-001")))) == "passed"
    assert run(registry(_failure_of(AssertAdrContract("ADR-PROBE-002")))) == "passed"


def test_an_adr_that_is_not_on_the_table_is_a_key_error() -> None:
    """An id not in the given list is a KeyError even if it exists in the registry (it does not fall through to the registry)."""
    with isolated_registry():
        run(adr_registry()(RegisterAdr("ADR-PROBE-001", title="registered", status="proposed")))
        assert run(adr_registry(specs=[])(_failure_of(AssertAdrContract("ADR-PROBE-001")))) == (
            "KeyError: 'ADR-PROBE-001'"
        )


def test_assert_all_checks_every_adr_and_raises_the_first_failure() -> None:
    """Check in id order and raise the first failure. None if there is none; also None if the table is empty."""
    good = _spec("ADR-PROBE-001", _deftest("test_here"))
    bad_b = _spec("ADR-PROBE-002", _deftest("test_gone_b"))
    bad_c = _spec("ADR-PROBE-003", _deftest("test_gone_c"))
    here = {"test_here": _deftest("test_here")}
    assert run(adr_registry(specs=[good], enforcements=here)(AssertAllAdrContracts())) is None
    assert run(adr_registry(specs=[])(AssertAllAdrContracts())) is None
    assert (
        run(
            adr_registry(specs=[bad_c, good, bad_b], enforcements=here)(
                _failure_of(AssertAllAdrContracts())
            )
        )
        == "AssertionError: ADR-PROBE-002: enforcement 'test_gone_b' is not registered"
    )


def test_register_deftest_writes_to_the_given_table_only() -> None:
    """If enforcements is given, register into that dict and not into the registry. The registered checks are read by the same handler's check."""
    table: dict[str, EnforcementRef | SemgrepSpec] = {}

    @do
    def body():
        yield RegisterDeftest("test_table_only", mode="expected-red")
        return (yield AssertAdrContract("ADR-PROBE-001"))

    spec = _spec("ADR-PROBE-001", _deftest("test_table_only", mode="expected-red"), plans=("p",))
    with isolated_registry():
        assert run(adr_registry(specs=[spec], enforcements=table)(body())) is None
        assert "test_table_only" not in registry_module._ENFORCEMENTS
    assert table == {"test_table_only": _deftest("test_table_only", mode="expected-red")}


def test_specs_alone_registers_on_an_empty_table_made_by_the_factory() -> None:
    """If only specs is given, the check table is empty, and RegisterDeftest registers into the dict made when the factory was called (it persists while the same handler value is used)."""
    spec = _spec("ADR-PROBE-001", _deftest("test_later"))
    registry = adr_registry(specs=[spec])
    with isolated_registry():
        registry_module._ENFORCEMENTS["test_later"] = _deftest("test_later")
        assert run(registry(_failure_of(AssertAdrContract("ADR-PROBE-001")))) == (
            "AssertionError: ADR-PROBE-001: enforcement 'test_later' is not registered"
        )
        run(registry(RegisterDeftest("test_later")))
        assert run(registry(_failure_of(AssertAdrContract("ADR-PROBE-001")))) == "passed"


def test_registering_an_id_twice_or_with_an_unknown_mode_is_a_value_error() -> None:
    @do
    def twice():
        yield RegisterDeftest("test_twice")
        return (yield _failure_of(RegisterDeftest("test_twice")))

    registry = adr_registry(enforcements={})
    assert run(registry(twice())) == "ValueError: duplicate ADR enforcement id: test_twice"
    assert run(registry(_failure_of(RegisterDeftest("test_odd", mode="amber")))) == (  # type: ignore[arg-type] — to see that an unknown mode is rejected
        "ValueError: unsupported enforcement mode: 'amber'"
    )


def test_an_inner_table_answers_before_the_outer_registry() -> None:
    """With the default adr_registry() installed outside, only part of the program can be checked with a table that has a missing check."""
    inner = adr_registry(specs=[_spec("ADR-PROBE-001", _deftest("test_gone"))])

    @do
    def body():
        outer = yield _failure_of(AssertAdrContract("ADR-PROBE-001"))
        return outer, (yield inner(_failure_of(AssertAdrContract("ADR-PROBE-001"))))

    with isolated_registry():
        run(adr_registry()(RegisterDeftest("test_gone")))
        run(
            adr_registry()(
                RegisterAdr(
                    "ADR-PROBE-001", title="probe", status="accepted", enforcement=["test_gone"]
                )
            )
        )
        assert run(adr_registry()(body())) == (
            "passed",
            "AssertionError: ADR-PROBE-001: enforcement 'test_gone' is not registered",
        )


def test_the_factory_refuses_a_table_that_is_not_a_dict() -> None:
    with pytest.raises(TypeError, match="positional"):
        adr_registry(AssertAdrContract("ADR-PROBE-001"))  # type: ignore[misc] — to see that a typo is rejected, pass it as a positional argument
    with pytest.raises(TypeError, match="takes a dict"):
        adr_registry(enforcements=[])  # type: ignore[arg-type] — to see that a typo is rejected, pass a non-dict value


def test_the_contract_checks_are_reached_only_through_the_effects() -> None:
    """The effects are at the entry point, and the 3 former functions are in neither the entry point nor the registry (only the inner _assert_adr_contract and
    _register_deftest_enforcement remain as bodies)."""
    assert {"AssertAdrContract", "AssertAllAdrContracts", "RegisterDeftest"} <= set(
        doeff_adr.__all__
    )
    for name in ("assert_adr_contract", "assert_all_adr_contracts", "register_deftest_enforcement"):
        assert name not in doeff_adr.__all__
        assert not hasattr(doeff_adr, name)
        assert not hasattr(registry_module, name)


def test_defadr_expands_to_the_effects_under_their_handler() -> None:
    """The expansion runs the deftest registration as RegisterDeftest, and the check of the resulting test as AssertAdrContract, under adr_registry()."""
    expanded = hy.macroexpand(
        hy.read(
            """(defadr ADR-PROBE-EXPANSION
                 :title "expansion" :status "accepted"
                 :enforcement [(deftest test-probe-expansion (assert True))])"""
        ),
        macros_module,
    )
    text = hy.repr(expanded)
    assert "RegisterDeftest" in text
    assert "AssertAdrContract" in text
    assert "adr-registry" in text
    assert "register-deftest-enforcement" not in text
    assert "assert-adr-contract" not in text


def test_the_adr_contract_test_needs_no_interpreter_fixture(pytester) -> None:
    """defadr in another repo works just by bumping the doeff version: even if the project has no doeff_interpreter fixture, the deftest registration and
    the ADR shape check run under its own handler. The resulting test works even if the module defines run or AssertAdrContract."""
    pytester.mkdir("docs")
    pytester.mkdir("docs/adr")
    pytester.makefile(
        ".hy",
        **{
            "docs/adr/defadr_0095_contract_effects": textwrap.dedent(
                """\
                (require doeff-adr.macros [defadr])
                (require doeff-hy.macros [deftest])

                (defadr ADR-SAMPLE-095
                  :title "contract effects sample"
                  :status "accepted"
                  :enforcement [(deftest test-sample-098-check (assert True))])

                (defn run [] "the module's own run")
                (defn AssertAdrContract [] "the module's own AssertAdrContract")
                """
            ),
        },
    )

    result = pytester.runpytest(
        "docs/adr/defadr_0095_contract_effects.hy", "-q", "-k", "adr_contract"
    )

    result.assert_outcomes(passed=1, deselected=1)
