"""Looking up a design record (ADR) by id is the effect GetAdr, answered by the handler factory adr_registry.

Decision: make it an effect.
The function get_adr, which was a migration shim layer, was removed after rewriting the callers in outside repos.

adr_registry() answers from the process registry, and adr_registry(specs=[spec, …]) answers only from that list (an id not in the list
raises KeyError instead of falling back to the registry).
The innermost installed handler answers first, so even if the deftest doeff_interpreter installs adr_registry() outside, installing
a different table inside a test makes that table answer.
"""

import doeff_adr
import pytest
from doeff_adr import AdrSpec, GetAdr, RegisterAdr, adr_registry
from doeff_adr._registry import isolated_registry

from doeff import do, run

pytest_plugins = ["pytester"]


def _spec(adr_id: str, title: str) -> AdrSpec:
    return AdrSpec(id=adr_id, title=title, status="accepted")


def test_get_adr_answers_from_the_process_registry() -> None:
    with isolated_registry():
        run(adr_registry()(RegisterAdr("ADR-PROBE-001", title="registered", status="proposed")))
        assert run(adr_registry()(GetAdr("ADR-PROBE-001"))).title == "registered"


def test_the_process_registry_is_read_when_the_effect_is_answered() -> None:
    """ADRs registered after the handler was created also answer (the fixture may create the handler before the ADR module is imported)."""
    answer = adr_registry()
    with isolated_registry():
        run(
            adr_registry()(
                RegisterAdr("ADR-PROBE-001", title="registered later", status="proposed")
            )
        )
        assert run(answer(GetAdr("ADR-PROBE-001"))).title == "registered later"


def test_a_given_table_replaces_the_process_registry() -> None:
    table = [
        _spec("ADR-PROBE-001", "from the table"),
        _spec("ADR-TABLE-ONLY", "only in the table"),
    ]
    with isolated_registry():
        run(adr_registry()(RegisterAdr("ADR-PROBE-001", title="registered", status="proposed")))
        assert run(adr_registry(specs=table)(GetAdr("ADR-PROBE-001"))).title == "from the table"
        assert run(adr_registry(specs=table)(GetAdr("ADR-TABLE-ONLY"))).title == "only in the table"


def test_an_inner_table_answers_before_the_outer_registry() -> None:
    """With the default adr_registry() still installed outside, only part of a program can be run against a different table."""
    table = [_spec("ADR-PROBE-001", "from the table")]

    @do
    def body():
        outer = yield GetAdr("ADR-PROBE-001")
        inner = yield adr_registry(specs=table)(GetAdr("ADR-PROBE-001"))
        return outer.title, inner.title

    with isolated_registry():
        run(adr_registry()(RegisterAdr("ADR-PROBE-001", title="registered", status="proposed")))
        assert run(adr_registry()(body())) == ("registered", "from the table")


def test_an_id_outside_the_table_is_a_key_error_not_the_outer_answer() -> None:
    """An id missing from the replacement table does not fall back to the outer registry. KeyError can be caught where GetAdr was raised."""

    @do
    def body():
        try:
            yield GetAdr("ADR-PROBE-001")
        except KeyError as error:
            return error.args
        return "answered"

    with isolated_registry():
        run(adr_registry()(RegisterAdr("ADR-PROBE-001", title="registered", status="proposed")))
        assert run(adr_registry()(adr_registry(specs=[])(body()))) == ("ADR-PROBE-001",)


def test_an_unregistered_id_is_a_key_error() -> None:
    with isolated_registry(), pytest.raises(KeyError, match="ADR-NOT-REGISTERED"):
        run(adr_registry()(GetAdr("ADR-NOT-REGISTERED")))


def test_the_factory_refuses_a_program_in_place_of_the_table() -> None:
    """Reject a mistyped adr_registry(p): specs is accepted only as a keyword, and non-list values are rejected too (adr_registry()(p) is the correct form)."""
    with pytest.raises(TypeError, match="positional"):
        adr_registry(GetAdr("ADR-PROBE-001"))  # type: ignore[misc] — to check that a mistake is rejected, pass it positionally
    with pytest.raises(TypeError, match="takes a list of AdrSpec"):
        adr_registry(specs=GetAdr("ADR-PROBE-001"))  # type: ignore[arg-type] — to check that a mistake is rejected, pass a non-list value


def test_the_table_refuses_a_repeated_id() -> None:
    """A table holding two ADRs with the same id is rejected at creation, since which one answers would be undecided."""
    with pytest.raises(ValueError, match="duplicate ADR id in the table: ADR-PROBE-001"):
        adr_registry(specs=[_spec("ADR-PROBE-001", "a"), _spec("ADR-PROBE-001", "b")])


def test_the_effect_is_public_and_get_adr_is_gone() -> None:
    """The effect GetAdr and the handler factory adr_registry are in the entry point, and the former shim function get_adr is in neither the entry point nor the registry."""
    assert {"GetAdr", "adr_registry"} <= set(doeff_adr.__all__)
    assert "get_adr" not in doeff_adr.__all__
    with pytest.raises(AttributeError, match="get_adr"):
        getattr(doeff_adr, "get_adr")  # noqa: B009 — look up a removed name without going through the type checker
    assert not hasattr(doeff_adr._registry, "get_adr")


def test_a_deftest_reads_the_adr_through_the_handler_its_fixture_installs(pytester) -> None:
    """An ADR deftest reads with (<- spec (GetAdr ADR-ID)), and the answer comes from the adr_registry() installed by doeff_interpreter."""
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
            "docs/adr/defadr_0098_get_adr": """\
                (require doeff-adr.macros [defadr deftest rule law])
                (require doeff-hy.macros [<-])
                (import doeff-adr.macros [fact counterexample])
                (import doeff-adr [AdrSpec GetAdr adr-registry])

                (defadr ADR-SAMPLE-098
                  :title "GetAdr sample"
                  :status "accepted"
                  :problem [(fact "deftest reads its ADR by effect")]
                  :decision [(rule R1 "read the ADR with GetAdr")]
                  :laws [(law read-by-effect
                           :statement "GetAdr answers the registered ADR"
                           :counterexamples [(counterexample "the ADR is read by a function")])]
                  :enforcement
                    [(deftest test-get-adr-reads-the-registered-adr
                       (<- spec (GetAdr "ADR-SAMPLE-098"))
                       (assert (= spec.title "GetAdr sample"))
                       (assert (= (get (. spec laws) 0 "id") "read-by-effect")))
                     (deftest test-get-adr-reads-a-swapped-table
                       (<- spec ((adr-registry :specs [(AdrSpec :id "ADR-SAMPLE-098" :title "swapped" :status "proposed")])
                                 (GetAdr "ADR-SAMPLE-098")))
                       (assert (= spec.title "swapped")))])
                """,
        },
    )

    result = pytester.runpytest("docs/adr/defadr_0098_get_adr.hy", "-q")

    result.assert_outcomes(passed=3)
