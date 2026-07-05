import importlib
import sys
import textwrap

import doeff_hy  # noqa: F401 - registers Hy import hooks
import pytest
from doeff_adr.registry import SemgrepSpec, clear_registry, get_adr, get_enforcement

pytest_plugins = ["pytester"]


@pytest.fixture
def tmp_hy_dir(tmp_path):
    sys.path.insert(0, str(tmp_path))
    clear_registry()
    yield tmp_path
    clear_registry()
    sys.path.remove(str(tmp_path))
    for name in list(sys.modules):
        mod = sys.modules[name]
        if hasattr(mod, "__file__") and mod.__file__ and str(tmp_path) in mod.__file__:
            del sys.modules[name]
    importlib.invalidate_caches()
    sys.path_importer_cache.clear()


def _write_and_import(tmp_path, filename, code):
    path = tmp_path / filename
    path.write_text(textwrap.dedent(code), encoding="utf-8")
    mod_name = path.stem
    sys.modules.pop(mod_name, None)
    importlib.invalidate_caches()
    sys.path_importer_cache.clear()
    return importlib.import_module(mod_name)


def _interpreter(program):
    from doeff import run

    return run(program)


def test_defadr_accepts_inline_deftest_and_defsemgrep(tmp_hy_dir):
    mod = _write_and_import(
        tmp_hy_dir,
        "test_sample_adr.hy",
        """\
        (require doeff-adr.macros [defadr deftest defsemgrep rule law])
        (import doeff-adr.macros [fact counterexample])

        (defadr ADR-SAMPLE-001
          :title "visible issue ownership"
          :status "accepted"
          :scope ["hypha.review" "webui"]
          :problem [(fact "open issue was invisible")]
          :decision [(rule R1 "open issue must have a holder")]
          :laws [(law visible-owner
                   :statement "nonterminal(issue) => visible_holder(issue)"
                   :counterexamples [(counterexample "open issue with no work and no review")])]
          :enforcement
            [(deftest test-inline-contract
               (assert (= (+ 1 1) 2)))
             (defsemgrep no-string-options
               :languages ["generic"]
               :message "string options are not typed"
               :pattern "\\"options\\": [\\"$X\\""
               :bad ["{\\"options\\": [\\"retry\\"]}"]
               :good ["{\\"options\\": [{\\"id\\": \\"retry\\"}]}"])
             (defsemgrep no-python-print
               :languages ["python"]
               :message "print is not allowed in this sample"
               :pattern "print(...)"
               :bad [{"relative-path" "sample/bad.py"
                      "source" "print('bad')\\n"}]
               :good [{"relative-path" "sample/good.py"
                       "source" "value = 1\\n"}])])
        """,
    )

    adr = get_adr("ADR-SAMPLE-001")
    assert adr.status == "accepted"
    assert [ref.id for ref in adr.enforcement] == [
        "test_inline_contract",
        "no_string_options",
        "no_python_print",
    ]
    assert callable(mod.test_inline_contract)
    assert callable(mod.test_ADR_SAMPLE_001_adr_contract)
    assert callable(mod.test_no_string_options_defsemgrep)
    assert callable(mod.test_no_python_print_defsemgrep)
    mod.test_inline_contract(_interpreter)
    mod.test_ADR_SAMPLE_001_adr_contract()
    mod.test_no_string_options_defsemgrep()
    mod.test_no_python_print_defsemgrep()
    semgrep_spec = get_enforcement("no_string_options")
    assert isinstance(semgrep_spec, SemgrepSpec)
    assert semgrep_spec.pattern == '"options": ["$X"'


def test_defsemgrep_accepts_installed_rule_fixture_form(tmp_hy_dir):
    # Exercises the installed-rule fixture form against a repo rule whose hit
    # text is legal to embed here. The previous fixture inlined the banned
    # WithHandler shim import, which tripped the raw-text architecture guard
    # (tests/architecture/test_no_public_withhandler_shim.py) — that guard is
    # deliberately blunt and must stay free of per-file exceptions.
    mod = _write_and_import(
        tmp_hy_dir,
        "test_installed_semgrep.hy",
        """\
        (require doeff-adr.macros [defsemgrep])

        (defsemgrep test-installed-future-annotations-rule
          "no-future-annotations"
          [{"relative-path" "packages/sample/bad.py"
            "source" "from __future__ import annotations\\n"}]
          [{"relative-path" "packages/sample/clean.py"
            "source" "import json\\n"}])
        """,
    )

    assert callable(mod.test_test_installed_future_annotations_rule_defsemgrep)
    mod.test_test_installed_future_annotations_rule_defsemgrep()


def test_accepted_defadr_without_enforcement_fails_contract(tmp_hy_dir):
    mod = _write_and_import(
        tmp_hy_dir,
        "test_no_enforcement.hy",
        """\
        (require doeff-adr.macros [defadr])

        (defadr ADR-SAMPLE-002
          :title "missing enforcement"
          :status "accepted")
        """,
    )

    with pytest.raises(AssertionError, match="accepted ADR must have executable enforcement"):
        mod.test_ADR_SAMPLE_002_adr_contract()


def test_defadr_rejects_unknown_status(tmp_hy_dir):
    with pytest.raises(ValueError, match="unsupported ADR status"):
        _write_and_import(
            tmp_hy_dir,
            "test_bad_status.hy",
            """\
            (require doeff-adr.macros [defadr])

            (defadr ADR-SAMPLE-003
              :title "bad status"
              :status "acceptd")
            """,
        )


def test_defadr_rejects_duplicate_enforcement_ids(tmp_hy_dir):
    with pytest.raises(ValueError, match="duplicate ADR enforcement id"):
        _write_and_import(
            tmp_hy_dir,
            "test_duplicate_enforcement.hy",
            """\
            (require doeff-adr.macros [defsemgrep])

            (defsemgrep duplicate-rule
              :pattern "$X"
              :bad ["bad"]
              :good [""])

            (defsemgrep duplicate-rule
              :pattern "$X"
              :bad ["bad"]
              :good [""])
            """,
        )


def test_expected_red_defsemgrep_still_validates_rule_fixtures(tmp_hy_dir):
    mod = _write_and_import(
        tmp_hy_dir,
        "test_expected_red_semgrep.hy",
        """\
        (require doeff-adr.macros [defsemgrep])

        (defsemgrep expected-red-rule
          :mode "expected-red"
          :languages ["generic"]
          :pattern "forbidden-token"
          :bad ["forbidden-token"]
          :good ["allowed-token"])
        """,
    )

    mod.test_expected_red_rule_defsemgrep()


def test_pytest_plugin_collects_defadr_hy_files(pytester):
    pytester.makeconftest(
        """\
        import pytest


        @pytest.fixture
        def doeff_interpreter():
            def run_program(program, *, env=None):
                from doeff import run

                if env:
                    raise ValueError("test interpreter does not accept env")
                return run(program)

            return run_program
        """
    )
    pytester.mkdir("docs")
    pytester.mkdir("docs/adr")
    pytester.makefile(
        ".hy",
        **{
            "docs/adr/defadr_0099_sample": """\
                (require doeff-adr.macros [defadr deftest rule law])
                (import doeff-adr.macros [fact counterexample])

                (defadr ADR-SAMPLE-099
                  :title "pytest plugin sample"
                  :status "accepted"
                  :problem [(fact "plugin should collect this file")]
                  :decision [(rule R1 "collect defadr hy files")]
                  :laws [(law collected-by-pytest
                           :statement "defadr_*.hy is collected"
                           :counterexamples [(counterexample "pytest sees no tests")])]
                  :enforcement
                    [(deftest test-plugin-inline-deftest
                       (assert (= (+ 1 1) 2)))])
                """,
        },
    )

    result = pytester.runpytest("docs/adr/defadr_0099_sample.hy", "-q")

    result.assert_outcomes(passed=2)
