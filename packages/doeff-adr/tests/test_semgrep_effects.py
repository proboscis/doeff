"""The operation that registers a Semgrep check in the table is effect RegisterSemgrep, and the operation that runs it and verifies hits and misses on the samples is effect AssertSemgrep;
the handler-returning factory semgrep_enforcements answers both.

Decision: register-semgrep and assert-semgrep are both effects. The former functions
register_semgrep_enforcement and assert_semgrep_enforcement were removed after
rewriting the callers in outside repos.

semgrep_enforcements() registers to and reads from the process registry, and semgrep_enforcements(table={...}) registers to and reads from that dict (an id not in the dict
does not fall back to the registry but raises KeyError). semgrep_enforcements(semgrep=...) launches semgrep from the given executable. The innermost installed handler
answers first, so a test can run only part of a program with a different table or a different semgrep. The expansion of macro defsemgrep also runs this effect under this
handler.
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

# A fake semgrep that reports a hit only for the samples that should hit (files in the bad dir) -- checks hits and misses when the semgrep execution is swapped.
_HITS_ONLY_BAD = """\
case "$*" in
  */bad/*) echo '{"results":[{"check_id":"probe","path":"bad/fixture_0.txt","start":{"line":1},"extra":{"message":"m"}}]}' ;;
  *) echo '{"results":[]}' ;;
esac
"""
# A fake semgrep that hits no sample.
_HITS_NOTHING = """echo '{"results":[]}'\n"""


def _fake_semgrep(tmp_path: Path, script_body: str, name: str = "fake-semgrep") -> str:
    """Place an executable that gives fixed answers, for tests that swap the semgrep execution."""
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + script_body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _inline(enforcement_id: str) -> RegisterSemgrep:
    """Registration of an ad hoc rule shape (hit sample forbidden-token, non-hit sample allowed-token)."""
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
    """With the real semgrep, answers if the rule hits the hit sample and does not hit the non-hit sample."""

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
    """Registers to and reads from the given dict, not the registry. An id absent from the dict is a KeyError even if present in the registry."""
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
    """The executable given by semgrep=... (str or Path) answers hits and misses: green if only the hit sample is hit, red if nothing is hit."""
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
    """If the named executable is missing, an AssertionError with the same message as when it is not on PATH, not a subprocess FileNotFoundError."""
    with isolated_registry():
        run(semgrep_enforcements()(_inline("probe_no_file")))
        with pytest.raises(AssertionError, match="semgrep executable is required"):
            run(
                semgrep_enforcements(semgrep=tmp_path / "no-such-semgrep")(
                    AssertSemgrep("probe_no_file")
                )
            )


def test_an_inner_handler_answers_before_the_outer_one(tmp_path: Path) -> None:
    """With the default semgrep_enforcements() installed outside, only part of a program can run with a different table and a different semgrep."""
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
    """Mistyping semgrep_enforcements(p) is rejected: configuration is accepted by keyword only, and a non-dict table is rejected too."""
    with pytest.raises(TypeError, match="positional"):
        semgrep_enforcements(AssertSemgrep("probe"))  # type: ignore[misc] -- passed positionally to check that the typo is rejected
    with pytest.raises(TypeError, match="takes a dict"):
        semgrep_enforcements(table=[])  # type: ignore[arg-type] -- passed a non-dict to check that the typo is rejected


def test_the_semgrep_checks_are_reached_only_through_the_effects() -> None:
    """The effects and factory are at the entry point; the former functions register_semgrep_enforcement and assert_semgrep_enforcement are in neither the entry point nor the registry
    (only the inner _register_semgrep_enforcement and _assert_semgrep_enforcement remain as the body)."""
    assert {"AssertSemgrep", "RegisterSemgrep", "semgrep_enforcements"} <= set(doeff_adr.__all__)
    for name in ("register_semgrep_enforcement", "assert_semgrep_enforcement"):
        assert name not in doeff_adr.__all__
        assert not hasattr(doeff_adr, name)
        assert not hasattr(registry_module, name)


def test_defsemgrep_expands_to_the_effects_under_their_handler() -> None:
    """The expansion runs RegisterSemgrep at import time and AssertSemgrep inside the generated test, under semgrep_enforcements()."""
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
    """The expansion reads doeff's run and effects by names that do not collide with the user module's names (_doeff-adr-run, _doeff-adr.) -- even if the module
    defines run or AssertSemgrep, the generated test works."""
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
            import probe_own_run  # type: ignore[import-not-found] -- a Hy module written in a temporary dir

            probe_own_run.test_probe_own_run_defsemgrep()
            assert probe_own_run.run() == "the module's own run"
            assert probe_own_run.AssertSemgrep() == "the module's own AssertSemgrep"
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("probe_own_run", None)


def test_a_defadr_with_defsemgrep_needs_no_interpreter_fixture(pytester) -> None:
    """defsemgrep in an outside repo works just by bumping the doeff version: even if the project has no doeff_interpreter fixture,
    the defsemgrep registration and check run under their own handler and turn green."""
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
