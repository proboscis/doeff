"""設計の記録(ADR)を id で引く操作は effect GetAdr で、handler を返す工場 adr_registry が答える(card ki-24a2bca4339f)。

決め: 依頼書 lt-TNNNQAXTY6XY4RAKWDDFX62NSM の問い get-adr の答え lt-82V6XBGCCR214RCF3SRKSZGTP5(effect にする)。
移し替えの層だった関数 get_adr は、外の repo の呼び手を書き換えた後(card ki-f2d54a67497b)に消した(card ki-c46e83ebd6ac)。

adr_registry() は process の登録簿から、adr_registry(specs=[spec, …]) はその列だけから答える(列に無い id は登録簿へ
落ちずに KeyError)。
内側に据えた handler が先に答えるので、deftest の doeff_interpreter が外に adr_registry() を据えていても、test の中で
別の表を据えればその表が答える。
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
    """handler を作った後に載った ADR も答える(fixture が handler を作るのは ADR の module の import の前でもよい)。"""
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
    """外に既定の adr_registry() を据えたまま、program の一部だけを別の表で走らせられる。"""
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
    """差し替えた表に無い id は、外の登録簿の答えへ落ちない。KeyError は GetAdr を出した所で受けられる。"""

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
    """adr_registry(p) と書き違えたら断る: specs は keyword だけで受け、列でない値も断る(adr_registry()(p) が正しい形)。"""
    with pytest.raises(TypeError, match="positional"):
        adr_registry(GetAdr("ADR-PROBE-001"))  # type: ignore[misc] — 書き違いを断る事を見るため、位置の引数で渡す
    with pytest.raises(TypeError, match="takes a list of AdrSpec"):
        adr_registry(specs=GetAdr("ADR-PROBE-001"))  # type: ignore[arg-type] — 書き違いを断る事を見るため、列でない値を渡す


def test_the_table_refuses_a_repeated_id() -> None:
    """同じ id の ADR が 2 つ在る表は、どちらが答えるかが決まらないので作る時に断る。"""
    with pytest.raises(ValueError, match="duplicate ADR id in the table: ADR-PROBE-001"):
        adr_registry(specs=[_spec("ADR-PROBE-001", "a"), _spec("ADR-PROBE-001", "b")])


def test_the_effect_is_public_and_get_adr_is_gone() -> None:
    """effect GetAdr と handler の工場 adr_registry は入口に在り、移し替えの層だった関数 get_adr は入口にも registry にも無い。"""
    assert {"GetAdr", "adr_registry"} <= set(doeff_adr.__all__)
    assert "get_adr" not in doeff_adr.__all__
    with pytest.raises(AttributeError, match="get_adr"):
        getattr(doeff_adr, "get_adr")  # noqa: B009 — 消えた名を型の検を通さずに引く
    assert not hasattr(doeff_adr._registry, "get_adr")


def test_a_deftest_reads_the_adr_through_the_handler_its_fixture_installs(pytester) -> None:
    """ADR の deftest は (<- spec (GetAdr ADR-ID)) で読み、answer は doeff_interpreter が据えた adr_registry() が出す。"""
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
