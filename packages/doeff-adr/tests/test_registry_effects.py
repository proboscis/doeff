"""登録簿に ADR を載せる・空にする・検の id を並べる・検を引く操作は effect RegisterAdr・ClearRegistry・EnforcementIds・
GetEnforcement で、GetAdr と同じ handler の工場 adr_registry が答える。

決め: doeff-adr を範囲に入れ、関数を GetAdr と同じ向きで直す
(ADR-DOE-API-001 R32)。前の関数 register_adr・clear_registry・enforcement_ids・get_enforcement は
外の repo に呼び手が無かったので、移し替えの層を残さずに消した。

adr_registry() は process の登録簿を読み書きし、adr_registry(specs=[…], enforcements={…}) は別の登録簿から答える(どちらかを
渡せば別の登録簿で、渡さない方は空)。内側に据えた handler が先に答えるので、test は program の一部だけを別の登録簿で走らせられる
— effect だけで読み書きする test は isolated_registry が要らない。
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
    """その場の規則の形の Semgrep の検の載せ(semgrep は走らせない — 載せと引きだけを見る test のため)。"""
    return RegisterSemgrep(
        enforcement_id, pattern="forbidden-token", bad=["forbidden-token"], good=["ok"]
    )


def test_register_adr_puts_the_adr_on_the_process_registry() -> None:
    """RegisterAdr は載せた AdrSpec を答え、同じ値を GetAdr が引く(status の語は小文字に揃える)。"""
    with isolated_registry():
        spec = run(
            adr_registry()(
                RegisterAdr("ADR-PROBE-001", title="probe", status=":Accepted", plans=["p"])
            )
        )
        assert spec == AdrSpec(id="ADR-PROBE-001", title="probe", status="accepted", plans=("p",))
        assert run(adr_registry()(GetAdr("ADR-PROBE-001"))) is spec


def test_register_adr_failures_are_raised_where_the_effect_was_performed() -> None:
    """同じ id と決まった語でない status は ValueError で、effect を出した所で受けられる。"""

    @do
    def refused(effect: RegisterAdr):
        """載せが断られた時の文を答える(断られずに載れば "accepted")— 断りが effect を出した所へ返る事を見るため。"""
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
    """RegisterSemgrep で載せた検を、EnforcementIds が並べ、GetEnforcement が引く。載っていない id は KeyError。"""
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
    """別の登録簿を据えると、6 つの effect は外の登録簿を読み書きしない: 載せは別の表へ行き、無い id は外に在っても KeyError、
    ClearRegistry は別の表だけを空にする。"""
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
    """semgrep_enforcements(table=d) と adr_registry(enforcements=d) に同じ辞書を渡すと、RegisterSemgrep の載せを GetEnforcement が
    引き、RegisterAdr の :enforcement の id はその検の種類(defsemgrep)を読む。"""
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
    """別の登録簿は工場を呼んだ時に 1 つ作る: 同じ handler の値を使い回す間は載せが残り、工場を呼び直すと空から始まる。"""
    answer = adr_registry(specs=[])
    run(answer(RegisterAdr("ADR-PROBE-001", title="kept", status="proposed")))
    assert run(answer(AdrIds())) == ["ADR-PROBE-001"]
    assert run(adr_registry(specs=[])(AdrIds())) == []


def test_the_factory_refuses_an_enforcement_table_that_is_not_a_dict() -> None:
    with pytest.raises(TypeError, match="takes a dict from id to enforcement, got list"):
        adr_registry(enforcements=[])  # type: ignore[arg-type] — 書き違いを断る事を見るため、辞書でない値を渡す


def test_the_registry_is_read_and_written_only_through_the_effects() -> None:
    """4 つの effect は入口に在り、前の関数 register_adr・clear_registry・enforcement_ids・get_enforcement は入口にも registry にも無い。"""
    assert {"RegisterAdr", "ClearRegistry", "EnforcementIds", "GetEnforcement"} <= set(
        doeff_adr.__all__
    )
    for gone in ("register_adr", "clear_registry", "enforcement_ids", "get_enforcement"):
        assert gone not in doeff_adr.__all__
        assert not hasattr(doeff_adr, gone)
        assert not hasattr(doeff_adr._registry, gone)


def test_a_deftest_writes_to_a_swapped_registry_and_leaves_the_process_one(pytester) -> None:
    """ADR の deftest が別の登録簿を据えて RegisterAdr と GetEnforcement を出すと、その表が答え、defadr が載せた process の登録簿は
    変わらない(defadr の展開も RegisterAdr を adr_registry() の下で出す)。"""
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
