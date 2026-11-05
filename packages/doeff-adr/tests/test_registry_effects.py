"""The operations that register an ADR in the registry, empty it, list check ids and look up a check are the effects RegisterAdr, ClearRegistry, EnforcementIds and
GetEnforcement, answered by adr_registry, the same handler factory as GetAdr.

Decision: bring doeff-adr into scope and fix the functions in the same direction as GetAdr
(ADR-DOE-API-001 R32). The former functions register_adr, clear_registry, enforcement_ids and get_enforcement had
no callers in other repos, so they were removed without leaving a migration layer.

adr_registry() reads and writes the process registry, and adr_registry(specs=[...], enforcements={...}) answers from a separate registry (if either is
given it is a separate registry, and the one not given is empty). The innermost installed handler answers first, so a test can run only part of a program against a separate registry
-- a test that reads and writes only through effects does not need isolated_registry.
"""

import doeff_adr
import doeff_adr._registry
import pytest
from doeff_adr import (
    AdrIds,
    AdrSpec,
    ClearRegistry,
    EnforcementIds,
    EnforcementRef,
    GetAdr,
    GetEnforcement,
    RegisterAdr,
    RegisterSemgrep,
    SemgrepSpec,
    adr_registry,
    semgrep_enforcements,
)
from doeff_adr._registry import isolated_registry

from doeff import do, run

pytest_plugins = ["pytester"]


def _inline_rule(enforcement_id: str) -> RegisterSemgrep:
    """Register an in-place-rule-form Semgrep check (semgrep is not run -- for a test that only looks at registration and lookup)."""
    return RegisterSemgrep(
        enforcement_id, pattern="forbidden-token", bad=["forbidden-token"], good=["ok"]
    )


def test_register_adr_puts_the_adr_on_the_process_registry() -> None:
    """RegisterAdr answers the registered AdrSpec, and GetAdr looks up the same value (the status word is lowercased)."""
    with isolated_registry():
        spec = run(
            adr_registry()(
                RegisterAdr("ADR-PROBE-001", title="probe", status=":Accepted", plans=["p"])
            )
        )
        assert spec == AdrSpec(id="ADR-PROBE-001", title="probe", status="accepted", plans=("p",))
        assert run(adr_registry()(GetAdr("ADR-PROBE-001"))) is spec


def test_register_adr_failures_are_raised_where_the_effect_was_performed() -> None:
    """The same id, or a status that is not one of the fixed words, is a ValueError, which can be caught at the site that raised the effect."""

    @do
    def refused(effect: RegisterAdr):
        """Return the text when registration is rejected ("accepted" if it registers without rejection) -- to see that the rejection returns to the site that raised the effect."""
        try:
            yield effect
        except ValueError as failed:
            return str(failed)
        return "accepted"

    @do
    def body():
        yield RegisterAdr("ADR-PROBE-001", title="first", status="proposed")
        again = yield refused(RegisterAdr("ADR-PROBE-001", title="again", status="proposed"))
        bad_status = yield refused(RegisterAdr("ADR-PROBE-002", title="bad status", status="draft"))
        return again, bad_status

    with isolated_registry():
        assert run(adr_registry()(body())) == (
            "duplicate ADR id: ADR-PROBE-001",
            "unsupported ADR status: 'draft'",
        )


def test_enforcement_ids_and_get_enforcement_read_the_process_registry() -> None:
    """Checks registered with RegisterSemgrep are listed by EnforcementIds and looked up by GetEnforcement. An id not registered is a KeyError."""
    with isolated_registry():
        rule = run(semgrep_enforcements()(_inline_rule("probe_rule")))
        assert run(adr_registry()(EnforcementIds())) == ["probe_rule"]
        assert run(adr_registry()(GetEnforcement("probe_rule"))) is rule
        with pytest.raises(KeyError, match="probe_missing"):
            run(adr_registry()(GetEnforcement("probe_missing")))


def test_clear_registry_empties_the_adrs_and_the_enforcements() -> None:
    with isolated_registry():
        run(semgrep_enforcements()(_inline_rule("probe_rule")))
        run(adr_registry()(RegisterAdr("ADR-PROBE-001", title="probe", status="proposed")))

        @do
        def body():
            yield ClearRegistry()
            return (yield AdrIds()), (yield EnforcementIds())

        assert run(adr_registry()(body())) == ([], [])


def test_a_separate_registry_answers_every_effect_without_falling_through() -> None:
    """With a separate registry installed, the 6 effects do not read or write the outer registry: registration goes to the separate table, an id that is absent is a KeyError even if it exists outside,
    and ClearRegistry empties only the separate table."""
    checks: dict[str, EnforcementRef | SemgrepSpec] = {}

    @do
    def inner():
        yield RegisterAdr("ADR-INNER", title="inner", status="proposed")
        seen = (yield AdrIds()), (yield EnforcementIds())
        try:
            yield GetEnforcement("probe_outer_rule")
        except KeyError as missing:
            seen = (*seen, missing.args)
        yield ClearRegistry()
        return (*seen, (yield AdrIds()))

    @do
    def body():
        swapped = yield adr_registry(
            specs=[AdrSpec(id="ADR-GIVEN", title="given", status="accepted")], enforcements=checks
        )(inner())
        return swapped, (yield AdrIds()), (yield EnforcementIds())

    with isolated_registry():
        run(semgrep_enforcements()(_inline_rule("probe_outer_rule")))
        run(adr_registry()(RegisterAdr("ADR-OUTER", title="outer", status="proposed")))
        assert run(adr_registry()(body())) == (
            (["ADR-GIVEN", "ADR-INNER"], [], ("probe_outer_rule",), []),
            ["ADR-OUTER"],
            ["probe_outer_rule"],
        )


def test_one_dict_is_shared_with_semgrep_enforcements() -> None:
    """When the same dict is passed to semgrep_enforcements(table=d) and adr_registry(enforcements=d), GetEnforcement looks up the registration of RegisterSemgrep,
    and the :enforcement id of RegisterAdr reads the kind of that check (defsemgrep)."""
    checks: dict[str, EnforcementRef | SemgrepSpec] = {}

    @do
    def body():
        rule = yield semgrep_enforcements(table=checks)(_inline_rule("probe_rule"))
        spec = yield RegisterAdr(
            "ADR-PROBE-001",
            title="probe",
            status="accepted",
            enforcement=["probe_rule", "probe_unknown"],
        )
        return rule, spec.enforcement, (yield GetEnforcement("probe_rule"))

    rule, refs, looked_up = run(adr_registry(enforcements=checks)(body()))
    assert looked_up is rule
    assert refs == (
        EnforcementRef(id="probe_rule", kind="defsemgrep"),
        EnforcementRef(id="probe_unknown", kind="unknown"),
    )
    assert list(checks) == ["probe_rule"]
    assert "ADR-PROBE-001" not in run(adr_registry()(AdrIds()))


def test_a_separate_registry_lives_as_long_as_its_handler_value() -> None:
    """A separate registry is made once when the factory is called: registrations persist while the same handler value is reused, and calling the factory again starts from empty."""
    answer = adr_registry(specs=[])
    run(answer(RegisterAdr("ADR-PROBE-001", title="kept", status="proposed")))
    assert run(answer(AdrIds())) == ["ADR-PROBE-001"]
    assert run(adr_registry(specs=[])(AdrIds())) == []


def test_the_factory_refuses_an_enforcement_table_that_is_not_a_dict() -> None:
    with pytest.raises(TypeError, match="takes a dict from id to enforcement, got list"):
        adr_registry(enforcements=[])  # type: ignore[arg-type] — to see that a typo is rejected, pass a non-dict value


def test_the_registry_is_read_and_written_only_through_the_effects() -> None:
    """The 4 effects are at the entry point, and the former functions register_adr, clear_registry, enforcement_ids and get_enforcement are in neither the entry point nor the registry."""
    assert {"RegisterAdr", "ClearRegistry", "EnforcementIds", "GetEnforcement"} <= set(
        doeff_adr.__all__
    )
    for gone in ("register_adr", "clear_registry", "enforcement_ids", "get_enforcement"):
        assert gone not in doeff_adr.__all__
        assert not hasattr(doeff_adr, gone)
        assert not hasattr(doeff_adr._registry, gone)


def test_a_deftest_writes_to_a_swapped_registry_and_leaves_the_process_one(pytester) -> None:
    """When an ADR's deftest installs a separate registry and raises RegisterAdr and GetEnforcement, that table answers, and the process registry that defadr registered
    is unchanged (the defadr expansion also raises RegisterAdr under adr_registry())."""
    pytester.makeconftest(
        """\
        import pytest


        @pytest.fixture
        def doeff_interpreter():
            def run_program(program, *, env=None):
                from doeff import run
                from doeff_adr import adr_registry

                if env:
                    raise ValueError("test interpreter does not accept env")
                return run(adr_registry()(program))

            return run_program
        """
    )
    pytester.mkdir("docs")
    pytester.mkdir("docs/adr")
    pytester.makefile(
        ".hy",
        **{
            "docs/adr/defadr_0096_registry_effects": """\
                (require doeff-adr.macros [defadr deftest rule law])
                (require doeff-hy.macros [<- do!])
                (import doeff-adr.macros [fact counterexample])
                (import doeff-adr [AdrIds EnforcementIds EnforcementRef GetEnforcement RegisterAdr adr-registry])

                (defadr ADR-SAMPLE-096
                  :title "registry effects sample"
                  :status "accepted"
                  :problem [(fact "deftest writes the registry by effect")]
                  :decision [(rule R1 "write the registry with RegisterAdr")]
                  :laws [(law write-by-effect
                           :statement "RegisterAdr writes the registry of the handler"
                           :counterexamples [(counterexample "the registry is written by a function")])]
                  :enforcement
                    [(deftest test-a-swapped-registry-answers
                       (<- answers ((adr-registry :enforcements {"probe" (EnforcementRef :id "probe" :kind "deftest")})
                                    (do!
                                      (<- _ (RegisterAdr "ADR-SWAPPED" :title "swapped" :status "proposed"))
                                      (<- ids (AdrIds))
                                      (<- check (GetEnforcement "probe"))
                                      #(ids check.kind))))
                       (assert (= answers #(["ADR-SWAPPED"] "deftest")))
                       (<- outer (AdrIds))
                       (assert (in "ADR-SAMPLE-096" outer))
                       (assert (not-in "ADR-SWAPPED" outer))
                       (<- outer-checks (EnforcementIds))
                       (assert (in "test_a_swapped_registry_answers" outer-checks)))])
                """,
        },
    )

    result = pytester.runpytest("docs/adr/defadr_0096_registry_effects.hy", "-q")

    result.assert_outcomes(passed=2)
