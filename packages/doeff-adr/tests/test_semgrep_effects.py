"""Semgrep の検を表に載せる操作は effect RegisterSemgrep、走らせて見本の当たり外れを確かめる操作は effect AssertSemgrep で、
handler を返す工場 semgrep_enforcements が答える(card ki-2a549274055c)。

決め: 依頼書 lt-WVC8PTVD0E022XXMKP1J98ACDP の答え lt-KY48MR4KP2N0GS9MQCCJTJMH55(register-semgrep)と、依頼書
lt-5CQZ1TMGZSCQ01PXH8JY2JK3DN の答え lt-19276K37Z3TCCRTBMXWBMMXVMN(assert-semgrep)— どちらも effect にする。前の関数
register_semgrep_enforcement と assert_semgrep_enforcement は、外の repo の呼び手を書き換えた後(card ki-ac39cb3c0bcd)に
消した(card ki-baf84c90f3df)。

semgrep_enforcements() は process の登録簿に載せて読み、semgrep_enforcements(table={…}) はその辞書に載せて読む(辞書に無い id は
登録簿へ落ちずに KeyError)。semgrep_enforcements(semgrep=…) は渡した実行 file で semgrep を起こす。内側に据えた handler が先に
答えるので、test は program の一部だけを別の表や別の semgrep で走らせられる。macro defsemgrep の展開も、この effect をこの
handler の下で走らせる。
"""

import stat
import sys
import textwrap
from pathlib import Path

import doeff_adr
import doeff_adr._registry as registry_module
import doeff_adr.macros as macros_module
import hy
import pytest
from doeff_adr import (
    AssertSemgrep,
    EnforcementIds,
    EnforcementRef,
    GetEnforcement,
    RegisterSemgrep,
    SemgrepSpec,
    adr_registry,
    semgrep_enforcements,
)
from doeff_adr._registry import isolated_registry

from doeff import do, run

pytest_plugins = ["pytester"]

# 当たる見本(bad の dir の file)にだけ当たったと答える偽の semgrep — semgrep の実行を差し替えた時の当たり外れを見る。
_HITS_ONLY_BAD = """\
case "$*" in
  */bad/*) echo '{"results":[{"check_id":"probe","path":"bad/fixture_0.txt","start":{"line":1},"extra":{"message":"m"}}]}' ;;
  *) echo '{"results":[]}' ;;
esac
"""
# どの見本にも当たらない偽の semgrep。
_HITS_NOTHING = """echo '{"results":[]}'\n"""


def _fake_semgrep(tmp_path: Path, script_body: str, name: str = "fake-semgrep") -> str:
    """semgrep の実行を差し替える test のための、決めた答えを出す実行 file を置く。"""
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + script_body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _inline(enforcement_id: str) -> RegisterSemgrep:
    """その場の規則の形の載せ(当たる見本 forbidden-token・当たらない見本 allowed-token)。"""
    return RegisterSemgrep(
        enforcement_id,
        pattern="forbidden-token",
        bad=["forbidden-token"],
        good=["allowed-token"],
    )


def test_register_semgrep_puts_the_rule_on_the_process_registry() -> None:
    with isolated_registry():
        spec = run(semgrep_enforcements()(_inline("probe_inline")))
        assert isinstance(spec, SemgrepSpec)
        assert run(adr_registry()(GetEnforcement("probe_inline"))) is spec
        assert spec.bad == ("forbidden-token",)


def test_assert_semgrep_checks_the_fixtures_with_semgrep() -> None:
    """本物の semgrep で、規則が当たる見本に当たり、当たらない見本に当たらなければ答える。"""

    @do
    def body():
        yield _inline("probe_inline")
        return (yield AssertSemgrep("probe_inline"))

    with isolated_registry():
        assert run(semgrep_enforcements()(body())) is None


def test_a_rule_that_misses_its_bad_fixture_is_an_assertion_error_where_the_effect_was_performed() -> (
    None
):
    @do
    def body():
        yield RegisterSemgrep(
            "probe_misses", pattern="never-there", bad=["forbidden-token"], good=["x"]
        )
        try:
            yield AssertSemgrep("probe_misses")
        except AssertionError as failed:
            return str(failed)
        return "passed"

    with isolated_registry():
        assert run(semgrep_enforcements()(body())) == (
            "probe_misses: defsemgrep did not match any bad fixture"
        )


def test_a_given_table_replaces_the_process_registry() -> None:
    """渡した辞書に載せて読み、登録簿には載せない。辞書に無い id は、登録簿に在っても KeyError。"""
    table: dict[str, EnforcementRef | SemgrepSpec] = {}

    @do
    def body():
        yield _inline("probe_table_only")
        yield AssertSemgrep("probe_table_only")
        try:
            yield AssertSemgrep("probe_registry_only")
        except KeyError as missing:
            return missing.args
        return "fell through"

    with isolated_registry():
        run(semgrep_enforcements()(_inline("probe_registry_only")))
        assert run(semgrep_enforcements(table=table)(body())) == ("probe_registry_only",)
        assert list(table) == ["probe_table_only"]
        assert "probe_table_only" not in run(adr_registry()(EnforcementIds()))


def test_the_semgrep_run_can_be_replaced(tmp_path: Path) -> None:
    """semgrep=… の実行 file(str か Path)が当たり外れを答える: 当たる見本にだけ当たれば緑、何にも当たらなければ赤。"""
    hits_only_bad = _fake_semgrep(tmp_path, _HITS_ONLY_BAD, "hits-only-bad")
    hits_nothing = _fake_semgrep(tmp_path, _HITS_NOTHING, "hits-nothing")
    with isolated_registry():
        run(semgrep_enforcements()(_inline("probe_fake")))
        assert run(semgrep_enforcements(semgrep=hits_only_bad)(AssertSemgrep("probe_fake"))) is None
        assert (
            run(semgrep_enforcements(semgrep=Path(hits_only_bad))(AssertSemgrep("probe_fake")))
            is None
        )
        with pytest.raises(AssertionError, match="did not match any bad fixture"):
            run(semgrep_enforcements(semgrep=hits_nothing)(AssertSemgrep("probe_fake")))


def test_a_named_semgrep_that_is_not_a_file_is_refused_like_a_missing_one(tmp_path: Path) -> None:
    """名指した実行 file が無ければ、subprocess の FileNotFoundError でなく PATH に無い時と同じ文の AssertionError。"""
    with isolated_registry():
        run(semgrep_enforcements()(_inline("probe_no_file")))
        with pytest.raises(AssertionError, match="semgrep executable is required"):
            run(
                semgrep_enforcements(semgrep=tmp_path / "no-such-semgrep")(
                    AssertSemgrep("probe_no_file")
                )
            )


def test_an_inner_handler_answers_before_the_outer_one(tmp_path: Path) -> None:
    """外に既定の semgrep_enforcements() を据えたまま、program の一部だけを別の表と別の semgrep で走らせられる。"""
    fake = _fake_semgrep(tmp_path, _HITS_NOTHING)
    table: dict[str, EnforcementRef | SemgrepSpec] = {}

    @do
    def body():
        yield _inline("probe_nested")
        yield AssertSemgrep("probe_nested")
        inner = semgrep_enforcements(table=table, semgrep=fake)
        yield inner(_inline("probe_nested"))
        try:
            yield inner(AssertSemgrep("probe_nested"))
        except AssertionError as failed:
            return str(failed)
        return "inner passed"

    with isolated_registry():
        assert run(semgrep_enforcements()(body())) == (
            "probe_nested: defsemgrep did not match any bad fixture"
        )


def test_registering_an_id_twice_or_without_a_rule_is_a_value_error() -> None:
    with isolated_registry():
        run(semgrep_enforcements()(_inline("probe_twice")))
        with pytest.raises(ValueError, match="duplicate ADR enforcement id: probe_twice"):
            run(semgrep_enforcements()(_inline("probe_twice")))
        with pytest.raises(ValueError, match="requires either pattern= or rule_id="):
            run(semgrep_enforcements()(RegisterSemgrep("probe_no_rule")))


def test_the_factory_refuses_a_program_in_place_of_its_settings() -> None:
    """semgrep_enforcements(p) と書き違えたら断る: 設定は keyword だけで受け、辞書でない表も断る。"""
    with pytest.raises(TypeError, match="positional"):
        semgrep_enforcements(AssertSemgrep("probe"))  # type: ignore[misc] — 書き違いを断る事を見るため、位置の引数で渡す
    with pytest.raises(TypeError, match="takes a dict"):
        semgrep_enforcements(table=[])  # type: ignore[arg-type] — 書き違いを断る事を見るため、辞書でない値を渡す


def test_the_semgrep_checks_are_reached_only_through_the_effects() -> None:
    """effect と工場は入口に在り、前の関数 register_semgrep_enforcement・assert_semgrep_enforcement は入口にも registry にも
    無い(本体は内側の _register_semgrep_enforcement・_assert_semgrep_enforcement だけ)。"""
    assert {"AssertSemgrep", "RegisterSemgrep", "semgrep_enforcements"} <= set(doeff_adr.__all__)
    for name in ("register_semgrep_enforcement", "assert_semgrep_enforcement"):
        assert name not in doeff_adr.__all__
        assert not hasattr(doeff_adr, name)
        assert not hasattr(registry_module, name)


def test_defsemgrep_expands_to_the_effects_under_their_handler() -> None:
    """展開は RegisterSemgrep を import の時に、AssertSemgrep を生まれる test の中で、semgrep_enforcements() の下で走らせる。"""
    expanded = hy.macroexpand(
        hy.read(
            """(defsemgrep probe-expansion
                 :pattern "forbidden-token" :bad ["forbidden-token"] :good ["allowed-token"])"""
        ),
        macros_module,
    )
    text = hy.repr(expanded)
    assert "RegisterSemgrep" in text
    assert "AssertSemgrep" in text
    assert "semgrep-enforcements" in text
    assert "register-semgrep-enforcement" not in text
    assert "assert-semgrep-enforcement" not in text


def test_a_defsemgrep_module_with_its_own_run_still_checks_its_rule(tmp_path: Path) -> None:
    """展開は doeff の run と effect を使い手の module の名と重ならない名(_doeff-adr-run・_doeff-adr.)で読む — module が
    run や AssertSemgrep を定義しても、生まれる test は動く。"""
    (tmp_path / "probe_own_run.hy").write_text(
        textwrap.dedent(
            """\
            (require doeff-adr.macros [defsemgrep])

            (defsemgrep probe-own-run
              :pattern "forbidden-token"
              :bad ["forbidden-token"]
              :good ["allowed-token"])

            (defn run [] "the module's own run")
            (defn AssertSemgrep [] "the module's own AssertSemgrep")
            """
        ),
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        with isolated_registry():
            import probe_own_run  # type: ignore[import-not-found] — 一時の dir に書いた Hy の module

            probe_own_run.test_probe_own_run_defsemgrep()
            assert probe_own_run.run() == "the module's own run"
            assert probe_own_run.AssertSemgrep() == "the module's own AssertSemgrep"
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("probe_own_run", None)


def test_a_defadr_with_defsemgrep_needs_no_interpreter_fixture(pytester) -> None:
    """外の repo の defsemgrep は doeff の版を上げるだけで動く: project が doeff_interpreter fixture を持たなくても、
    defsemgrep の載せと検は自分の handler の下で走って緑になる。"""
    pytester.mkdir("docs")
    pytester.mkdir("docs/adr")
    pytester.makefile(
        ".hy",
        **{
            "docs/adr/defadr_0097_semgrep_effects": """\
                (require doeff-adr.macros [defadr defsemgrep rule law])
                (import doeff-adr.macros [fact counterexample])

                (defadr ADR-SAMPLE-097
                  :title "defsemgrep sample"
                  :status "accepted"
                  :problem [(fact "a forbidden token")]
                  :decision [(rule R1 "the token is refused")]
                  :laws [(law no-forbidden-token
                           :statement "no file carries the forbidden token"
                           :counterexamples [(counterexample "a file writes forbidden-token")])]
                  :enforcement
                    [(defsemgrep no-forbidden-token
                       :pattern "forbidden-token"
                       :bad ["forbidden-token"]
                       :good ["allowed-token"])])
                """,
        },
    )

    result = pytester.runpytest("docs/adr/defadr_0097_semgrep_effects.hy", "-q")

    result.assert_outcomes(passed=2)
