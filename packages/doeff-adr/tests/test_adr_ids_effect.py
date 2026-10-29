"""載っている ADR の id の一覧を読む操作は effect AdrIds で、GetAdr と同じ handler の工場 adr_registry が答える(card ki-2d28425e0d40)。

決め: 依頼書 lt-SNXDP773DP61WQ8S3D5HSHW2B1 の答え lt-NJMPGF51C0FBF49SC430JWEQ82(effect にする・ADR-DOE-API-001 R21)。
関数 adr_ids は、外の repo の呼び手(agora L6159・custody L74)を AdrIds へ書き換えた後に消した。

adr_registry() は process の登録簿の id を、adr_registry(specs=[spec, …]) はその列の id だけを並べて返す。
内側に据えた handler が先に答えるので、deftest の doeff_interpreter が外に adr_registry() を据えていても、test の中で
別の表を据えればその表の id が返る。
"""

import doeff_adr
from doeff_adr import AdrIds, AdrSpec, GetAdr, adr_registry
from doeff_adr.registry import isolated_registry, register_adr

from doeff import do, run

pytest_plugins = ["pytester"]


def _spec(adr_id: str) -> AdrSpec:
    return AdrSpec(id=adr_id, title=adr_id, status="accepted")


def test_adr_ids_answers_the_process_registry_in_order() -> None:
    with isolated_registry():
        register_adr("ADR-PROBE-002", title="second", status="proposed")
        register_adr("ADR-PROBE-001", title="first", status="proposed")
        assert run(adr_registry()(AdrIds())) == ["ADR-PROBE-001", "ADR-PROBE-002"]


def test_an_empty_registry_answers_an_empty_list() -> None:
    with isolated_registry():
        assert run(adr_registry()(AdrIds())) == []


def test_the_process_registry_is_read_when_the_effect_is_answered() -> None:
    """handler を作った後に載った ADR の id も返る(fixture が handler を作るのは ADR の module の import の前でもよい)。"""
    answer = adr_registry()
    with isolated_registry():
        register_adr("ADR-PROBE-001", title="registered later", status="proposed")
        assert run(answer(AdrIds())) == ["ADR-PROBE-001"]


def test_a_given_table_replaces_the_process_registry() -> None:
    """表を据えると、登録簿の id ではなく表の id だけが返る(specs=[] は空の表)。"""
    table = [_spec("ADR-TABLE-B"), _spec("ADR-TABLE-A")]
    with isolated_registry():
        register_adr("ADR-PROBE-001", title="registered", status="proposed")
        assert run(adr_registry(specs=table)(AdrIds())) == ["ADR-TABLE-A", "ADR-TABLE-B"]
        assert run(adr_registry(specs=[])(AdrIds())) == []


def test_an_inner_table_answers_before_the_outer_registry() -> None:
    """外に既定の adr_registry() を据えたまま、program の一部だけを別の表で走らせられる。GetAdr と AdrIds は同じ表を読む。"""
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
        register_adr("ADR-PROBE-001", title="registered", status="proposed")
        assert run(adr_registry()(body())) == (
            ["ADR-PROBE-001"],
            (["ADR-TABLE-ONLY"], "ADR-TABLE-ONLY"),
        )


def test_the_answer_is_a_new_list_each_time() -> None:
    """返した一覧を書き換えても、登録簿と表は変わらない(次の AdrIds は同じ id を返す)。"""
    table = [_spec("ADR-TABLE-A")]

    @do
    def body():
        first = yield AdrIds()
        first.clear()
        return (yield AdrIds())

    with isolated_registry():
        register_adr("ADR-PROBE-001", title="registered", status="proposed")
        assert run(adr_registry()(body())) == ["ADR-PROBE-001"]
    assert run(adr_registry(specs=table)(body())) == ["ADR-TABLE-A"]


def test_adr_ids_is_read_only_through_the_effect() -> None:
    """effect AdrIds と工場 adr_registry は入口に在り、関数 adr_ids は入口にも registry にも無い(内側の _adr_ids だけ)。"""
    assert {"AdrIds", "adr_registry"} <= set(doeff_adr.__all__)
    assert "adr_ids" not in doeff_adr.__all__
    assert not hasattr(doeff_adr, "adr_ids")
    assert not hasattr(doeff_adr.registry, "adr_ids")


def test_a_deftest_reads_the_ids_through_the_handler_its_fixture_installs(pytester) -> None:
    """ADR の deftest は (<- ids (AdrIds)) で読み、答えは doeff_interpreter が据えた adr_registry() が出す。"""
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
