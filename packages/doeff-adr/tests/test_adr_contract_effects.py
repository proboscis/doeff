"""ADR の形を確かめる操作は effect AssertAdrContract・AssertAllAdrContracts、deftest の検を表に載せる操作は effect RegisterDeftest
で、GetAdr と同じ handler の工場 adr_registry が答える(card ki-54e559d3c0e7・ADR-DOE-API-001 R30)。

決め: 依頼書 lt-6SE07X4256P323ZGZVNSEF9M95 の問い adr-scope の答え lt-RA4FZ731JNZQ6JEGVAYD35XCAZ(doeff-adr の残りの名を同じ向きで
直す)。前の関数 assert_adr_contract・assert_all_adr_contracts・register_deftest_enforcement は外の repo に呼び手が無いので、移し替えの
層を残さずに消した。

adr_registry() は process の登録簿から答え、adr_registry(specs=[…], enforcements={…}) は渡した列と辞書だけから答える(渡さない方は
空)。内側に据えた handler が先に答えるので、test は欠けた検を持つ ADR を登録簿に触らずに確かめられる。macro defadr の展開も、この
effect をこの handler の下で走らせる。
"""

import textwrap

import doeff_adr
import doeff_adr.macros as macros_module
import doeff_adr.registry as registry_module
import hy
import pytest
from doeff_adr import (
    AdrSpec,
    AssertAdrContract,
    AssertAllAdrContracts,
    EnforcementRef,
    RegisterDeftest,
    adr_registry,
)
from doeff_adr.registry import (
    AdrStatus,
    EnforcementMode,
    SemgrepSpec,
    get_enforcement,
    isolated_registry,
    register_adr,
)

from doeff import do, run

pytest_plugins = ["pytester"]


def _spec(
    adr_id: str,
    *refs: EnforcementRef,
    status: AdrStatus = "accepted",
    plans: tuple[str, ...] = (),
) -> AdrSpec:
    """検の参照 refs を挙げた ADR の値(表を差し替える test が、登録簿を通さずに渡す)。"""
    return AdrSpec(id=adr_id, title=adr_id, status=status, enforcement=refs, plans=plans)


def _deftest(enforcement_id: str, mode: EnforcementMode = "green") -> EnforcementRef:
    """deftest の検の参照(ADR が挙げる・表に載る値)を、比べやすい 1 行で作るため。"""
    return EnforcementRef(id=enforcement_id, kind="deftest", mode=mode)


@do
def _failure_of(effect):
    """effect を出し、出した所で受けた外れの文を返す(外れなければ "passed")— 失敗が effect を出した所へ返る事を見るため。"""
    try:
        yield effect
    except (AssertionError, KeyError, ValueError) as failed:
        return f"{type(failed).__name__}: {failed}"
    return "passed"


def test_a_registered_adr_with_its_registered_check_passes() -> None:
    """process の登録簿: RegisterDeftest で載せた検を挙げる採用済みの ADR は、AssertAdrContract に None で答える。"""

    @do
    def body():
        ref = yield RegisterDeftest("test_probe_contract")
        return ref, (yield AssertAdrContract("ADR-PROBE-001"))

    with isolated_registry():
        register_adr(
            "ADR-PROBE-001",
            title="probe",
            status="accepted",
            enforcement=[_deftest("test_probe_contract")],
        )
        ref, answer = run(adr_registry()(body()))
        assert ref == _deftest("test_probe_contract")
        assert get_enforcement("test_probe_contract") is ref
        assert answer is None


def test_an_adr_whose_check_is_missing_fails_where_the_effect_was_performed() -> None:
    """欠けた検を持つ ADR: 挙げた検が表に無ければ、AssertAdrContract を出した所へ AssertionError が返る。"""
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
    """渡した列に無い id は、登録簿に在っても KeyError(登録簿へ落ちない)。"""
    with isolated_registry():
        register_adr("ADR-PROBE-001", title="registered", status="proposed")
        assert run(adr_registry(specs=[])(_failure_of(AssertAdrContract("ADR-PROBE-001")))) == (
            "KeyError: 'ADR-PROBE-001'"
        )


def test_assert_all_checks_every_adr_and_raises_the_first_failure() -> None:
    """id の順に確かめ、最初の外れを投げる。外れが無ければ None、表が空でも None。"""
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
    """enforcements を渡せばその辞書に載せ、登録簿には載せない。載せた検は同じ handler の確かめが読む。"""
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
    """specs だけを渡すと検の表は空で、RegisterDeftest は工場を呼んだ時に作った辞書へ載せる(同じ handler の値の間は残る)。"""
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
    assert run(registry(_failure_of(RegisterDeftest("test_odd", mode="amber")))) == (  # type: ignore[arg-type] — 知らない mode を断る事を見る
        "ValueError: unsupported enforcement mode: 'amber'"
    )


def test_an_inner_table_answers_before_the_outer_registry() -> None:
    """外に既定の adr_registry() を据えたまま、program の一部だけを欠けた検を持つ表で確かめられる。"""
    inner = adr_registry(specs=[_spec("ADR-PROBE-001", _deftest("test_gone"))])

    @do
    def body():
        outer = yield _failure_of(AssertAdrContract("ADR-PROBE-001"))
        return outer, (yield inner(_failure_of(AssertAdrContract("ADR-PROBE-001"))))

    with isolated_registry():
        run(adr_registry()(RegisterDeftest("test_gone")))
        register_adr("ADR-PROBE-001", title="probe", status="accepted", enforcement=["test_gone"])
        assert run(adr_registry()(body())) == (
            "passed",
            "AssertionError: ADR-PROBE-001: enforcement 'test_gone' is not registered",
        )


def test_the_factory_refuses_a_table_that_is_not_a_dict() -> None:
    with pytest.raises(TypeError, match="positional"):
        adr_registry(AssertAdrContract("ADR-PROBE-001"))  # type: ignore[misc] — 書き違いを断る事を見るため、位置の引数で渡す
    with pytest.raises(TypeError, match="takes a dict"):
        adr_registry(enforcements=[])  # type: ignore[arg-type] — 書き違いを断る事を見るため、辞書でない値を渡す


def test_the_contract_checks_are_reached_only_through_the_effects() -> None:
    """effect は入口に在り、前の関数 3 個は入口にも registry にも無い(本体は内側の _assert_adr_contract・
    _register_deftest_enforcement だけ)。"""
    assert {"AssertAdrContract", "AssertAllAdrContracts", "RegisterDeftest"} <= set(
        doeff_adr.__all__
    )
    for name in ("assert_adr_contract", "assert_all_adr_contracts", "register_deftest_enforcement"):
        assert name not in doeff_adr.__all__
        assert not hasattr(doeff_adr, name)
        assert not hasattr(registry_module, name)


def test_defadr_expands_to_the_effects_under_their_handler() -> None:
    """展開は deftest の載せを RegisterDeftest として、生まれる test の確かめを AssertAdrContract として adr_registry() の下で走らせる。"""
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
    """外の repo の defadr は doeff の版を上げるだけで動く: project が doeff_interpreter fixture を持たなくても、deftest の載せと
    ADR の形の確かめは自分の handler の下で走る。module が run や AssertAdrContract を定義しても、生まれる test は動く。"""
    pytester.mkdir("docs")
    pytester.mkdir("docs/adr")
    pytester.makefile(
        ".hy",
        **{
            "docs/adr/defadr_0098_contract_effects": textwrap.dedent(
                """\
                (require doeff-adr.macros [defadr])
                (require doeff-hy.macros [deftest])

                (defadr ADR-SAMPLE-098
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
        "docs/adr/defadr_0098_contract_effects.hy", "-q", "-k", "adr_contract"
    )

    result.assert_outcomes(passed=1, deselected=1)
