"""Reading the list of registered ADR ids is the effect AdrIds, answered by adr_registry, the same handler factory as GetAdr.

Decision: make it an effect (ADR-DOE-API-001 R21).
The function adr_ids was removed after rewriting the callers in outside repos (agora L6159, custody L74) to AdrIds.

adr_registry() returns the ids in the process registry, and adr_registry(specs=[spec, …]) returns only the ids in that list.
The innermost installed handler answers first, so even if the deftest doeff_interpreter installs adr_registry() outside, installing
a different table inside a test makes that table's ids be returned.
"""

import doeff_adr
from doeff_adr import AdrIds, AdrSpec, GetAdr, RegisterAdr, adr_registry
from doeff_adr._registry import isolated_registry

from doeff import do, run

pytest_plugins = ["pytester"]


def _spec(adr_id: str) -> AdrSpec:
    return AdrSpec(id=adr_id, title=adr_id, status="accepted")


def test_adr_ids_answers_the_process_registry_in_order() -> None:
    with isolated_registry():
        run(adr_registry()(RegisterAdr("ADR-PROBE-002", title="second", status="proposed")))
        run(adr_registry()(RegisterAdr("ADR-PROBE-001", title="first", status="proposed")))
        assert run(adr_registry()(AdrIds())) == ["ADR-PROBE-001", "ADR-PROBE-002"]


def test_an_empty_registry_answers_an_empty_list() -> None:
    with isolated_registry():
        assert run(adr_registry()(AdrIds())) == []


def test_the_process_registry_is_read_when_the_effect_is_answered() -> None:
    """ADR ids registered after the handler was created are also returned (the fixture may create the handler before the ADR module is imported)."""
    answer = adr_registry()
    with isolated_registry():
        run(
            adr_registry()(
                RegisterAdr("ADR-PROBE-001", title="registered later", status="proposed")
            )
        )
        assert run(answer(AdrIds())) == ["ADR-PROBE-001"]


def test_a_given_table_replaces_the_process_registry() -> None:
    """Installing a table returns only the table's ids, not the registry's (specs=[] is an empty table)."""
    table = [_spec("ADR-TABLE-B"), _spec("ADR-TABLE-A")]
    with isolated_registry():
        run(adr_registry()(RegisterAdr("ADR-PROBE-001", title="registered", status="proposed")))
        assert run(adr_registry(specs=table)(AdrIds())) == ["ADR-TABLE-A", "ADR-TABLE-B"]
        assert run(adr_registry(specs=[])(AdrIds())) == []


def test_an_inner_table_answers_before_the_outer_registry() -> None:
    """With the default adr_registry() still installed outside, only part of a program can be run against a different table. GetAdr and AdrIds read the same table."""
    table = [_spec("ADR-TABLE-ONLY")]

    @do
    def inner():
        ids = yield AdrIds()
        spec = yield GetAdr(ids[0])
        return ids, spec.title

    @do
    def body():
        outer = yield AdrIds()
        swapped = yield adr_registry(specs=table)(inner())
        return outer, swapped

    with isolated_registry():
        run(adr_registry()(RegisterAdr("ADR-PROBE-001", title="registered", status="proposed")))
        assert run(adr_registry()(body())) == (
            ["ADR-PROBE-001"],
            (["ADR-TABLE-ONLY"], "ADR-TABLE-ONLY"),
        )


def test_the_answer_is_a_new_list_each_time() -> None:
    """Mutating the returned list changes neither the registry nor the table (the next AdrIds returns the same ids)."""
    table = [_spec("ADR-TABLE-A")]

    @do
    def body():
        first = yield AdrIds()
        first.clear()
        return (yield AdrIds())

    with isolated_registry():
        run(adr_registry()(RegisterAdr("ADR-PROBE-001", title="registered", status="proposed")))
        assert run(adr_registry()(body())) == ["ADR-PROBE-001"]
    assert run(adr_registry(specs=table)(body())) == ["ADR-TABLE-A"]


def test_adr_ids_is_read_only_through_the_effect() -> None:
    """The effect AdrIds and the factory adr_registry are in the entry point, and the function adr_ids is in neither the entry point nor the registry (only the inner _adr_ids)."""
    assert {"AdrIds", "adr_registry"} <= set(doeff_adr.__all__)
    assert "adr_ids" not in doeff_adr.__all__
    assert not hasattr(doeff_adr, "adr_ids")
    assert not hasattr(doeff_adr._registry, "adr_ids")


def test_a_deftest_reads_the_ids_through_the_handler_its_fixture_installs(pytester) -> None:
    """An ADR deftest reads with (<- ids (AdrIds)), and the answer comes from the adr_registry() installed by doeff_interpreter."""
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
            "docs/adr/defadr_0097_adr_ids": """\
                (require doeff-adr.macros [defadr deftest rule law])
                (require doeff-hy.macros [<-])
                (import doeff-adr.macros [fact counterexample])
                (import doeff-adr [AdrIds AdrSpec adr-registry])

                (defadr ADR-SAMPLE-097
                  :title "AdrIds sample"
                  :status "accepted"
                  :problem [(fact "deftest reads the registered ids by effect")]
                  :decision [(rule R1 "read the ids with AdrIds")]
                  :laws [(law read-by-effect
                           :statement "AdrIds answers the registered ids"
                           :counterexamples [(counterexample "the ids are read by a function")])]
                  :enforcement
                    [(deftest test-adr-ids-reads-the-registry
                       (<- ids (AdrIds))
                       (assert (in "ADR-SAMPLE-097" ids)))
                     (deftest test-adr-ids-reads-a-swapped-table
                       (<- ids ((adr-registry :specs [(AdrSpec :id "ADR-SWAPPED" :title "swapped" :status "proposed")])
                                (AdrIds)))
                       (assert (= ids ["ADR-SWAPPED"])))])
                """,
        },
    )

    result = pytester.runpytest("docs/adr/defadr_0097_adr_ids.hy", "-q")

    result.assert_outcomes(passed=3)
