"""Tests for the deftest macro — effectful tests that expand to pytest functions.

Verifies:
  - deftest generates a callable test function
  - deftest body can use <- for effect binding
  - deftest with :interpreters generates parametrize marker
  - deftest with fixture params [trade-date] works
  - deftest with :params generates parametrize marker
  - deftest without :interpreters uses bare doeff_interpreter fixture
  - Assert failures inside deftest propagate correctly
"""

import importlib
import sys
import textwrap
from dataclasses import dataclass

import doeff_hy  # noqa — registers extensions
import pytest

from doeff import EffectBase, Pass, Resume, run
from doeff import do as _doeff_do
from doeff import handler as _program_handler

# ---------------------------------------------------------------------------
# Test effect + handler for use in deftest tests
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class GetValue(EffectBase):
    key: str


def stub_handler():
    @_doeff_do
    def _handler(effect, k):
        if isinstance(effect, GetValue):
            values = {"price": 100.0, "name": "TestCo"}
            yield Resume(k, values.get(effect.key))
        else:
            yield Pass(effect, k)
    return _program_handler(_handler)


def stub_interpreter(program):
    """Simple test interpreter — stub handler + run."""
    return run(stub_handler()(program))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_hy_dir(tmp_path):
    sys.path.insert(0, str(tmp_path))
    yield tmp_path
    sys.path.remove(str(tmp_path))
    for name in list(sys.modules):
        mod = sys.modules[name]
        if hasattr(mod, "__file__") and mod.__file__ and str(tmp_path) in mod.__file__:
            del sys.modules[name]
    importlib.invalidate_caches()
    sys.path_importer_cache.clear()


def _write_and_import(tmp_path, filename, code):
    filepath = tmp_path / filename
    filepath.write_text(textwrap.dedent(code))
    mod_name = filepath.stem
    cache_dir = tmp_path / "__pycache__"
    if cache_dir.exists():
        for f in cache_dir.iterdir():
            if mod_name in f.name:
                f.unlink()
    sys.modules.pop(mod_name, None)
    importlib.invalidate_caches()
    sys.path_importer_cache.clear()
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Basic deftest
# ---------------------------------------------------------------------------

class TestDeftestBasic:
    def test_deftest_generates_function(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "basic_test.hy", """\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (deftest test-basic
              (assert True))
        """)
        assert callable(mod.test_basic)

    def test_deftest_takes_doeff_interpreter(self, tmp_hy_dir):
        """deftest function should accept doeff_interpreter as first arg."""
        import inspect
        mod = _write_and_import(tmp_hy_dir, "interp_test.hy", """\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (deftest test-with-interp
              (assert True))
        """)
        sig = inspect.signature(mod.test_with_interp)
        assert "doeff_interpreter" in sig.parameters

    def test_deftest_runs_with_interpreter(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "run_test.hy", """\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (deftest test-runs
              (assert (= 1 1)))
        """)
        mod.test_runs(stub_interpreter)


# ---------------------------------------------------------------------------
# Effect binding in deftest
# ---------------------------------------------------------------------------

class TestDeftestEffects:
    def test_deftest_with_effect_binding(self, tmp_hy_dir):
        """deftest can use <- to bind effects."""
        # Write a self-contained test that imports GetValue from this test module
        (tmp_hy_dir / "effect_test.hy").write_text(textwrap.dedent("""\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (import test-deftest-macro [GetValue])
            (deftest test-effect
              (<- price (GetValue :key "price"))
              (assert (= price 100.0)))
        """))
        # Need this test module importable
        sys.modules.pop("effect_test", None)
        importlib.invalidate_caches()
        sys.path_importer_cache.clear()
        mod = importlib.import_module("effect_test")
        mod.test_effect(stub_interpreter)

    def test_deftest_assert_failure_propagates(self, tmp_hy_dir):
        """AssertionError inside deftest should propagate."""
        (tmp_hy_dir / "fail_test.hy").write_text(textwrap.dedent("""\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (import test-deftest-macro [GetValue])
            (deftest test-fails
              (<- price (GetValue :key "price"))
              (assert (= price 999.0) "price should be 999"))
        """))
        sys.modules.pop("fail_test", None)
        importlib.invalidate_caches()
        sys.path_importer_cache.clear()
        mod = importlib.import_module("fail_test")
        with pytest.raises(AssertionError, match="price should be 999"):
            mod.test_fails(stub_interpreter)


# ---------------------------------------------------------------------------
# Interpreter parametrize
# ---------------------------------------------------------------------------

class TestDeftestInterpreters:
    def test_interpreters_adds_parametrize_marker(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "param_interp.hy", """\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (import pytest)
            (deftest test-multi
              {:interpreters ["test_a" "test_b"]}
              (assert True))
        """)
        # Check pytest markers
        markers = list(mod.test_multi.pytestmark)
        param_markers = [m for m in markers if m.name == "parametrize"]
        assert len(param_markers) > 0
        # Check the parametrize values
        marker = param_markers[0]
        assert marker.args[0] == "doeff_interpreter_name"
        assert list(marker.args[1]) == ["test_a", "test_b"]


# ---------------------------------------------------------------------------
# Fixture params
# ---------------------------------------------------------------------------

class TestDeftestFixtureParams:
    def test_fixture_params_in_signature(self, tmp_hy_dir):
        import inspect
        mod = _write_and_import(tmp_hy_dir, "fixture_test.hy", """\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (deftest test-with-fixture [trade-date]
              (assert (isinstance trade-date str)))
        """)
        sig = inspect.signature(mod.test_with_fixture)
        assert "doeff_interpreter" in sig.parameters
        assert "trade_date" in sig.parameters

    def test_params_adds_parametrize(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "params_test.hy", """\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (import pytest)
            (deftest test-dates [trade-date]
              {:params {"trade-date" ["2026-04-01" "2026-03-15"]}}
              (assert (isinstance trade-date str)))
        """)
        markers = list(mod.test_dates.pytestmark)
        param_markers = [m for m in markers if m.name == "parametrize"]
        assert len(param_markers) > 0


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------

class TestDeftestMarks:
    def test_marks_adds_pytest_marks(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "marks_test.hy", """\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (import pytest)
            (deftest test-marked
              {:marks ["e2e" "slow"]}
              (assert True))
        """)
        markers = list(mod.test_marked.pytestmark)
        mark_names = {m.name for m in markers}
        assert "e2e" in mark_names
        assert "slow" in mark_names

    def test_skipif_adds_skip_marker(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "skipif_test.hy", """\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (import pytest)
            (deftest test-skippable
              {:skip-if True :skip-reason "always skip"}
              (assert False "should not run"))
        """)
        markers = list(mod.test_skippable.pytestmark)
        skip_markers = [m for m in markers if m.name == "skipif"]
        assert len(skip_markers) == 1
        assert skip_markers[0].kwargs["reason"] == "always skip"

    def test_skipif_with_expression(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "skipif_expr.hy", """\
            (require doeff-hy.macros [deftest <-])
            (import doeff [do :as _doeff-do])
            (import pytest)
            (import sys)
            (deftest test-platform-skip
              {:skip-if (= sys.platform "darwin") :skip-reason "not on mac"}
              (assert True))
        """)
        markers = list(mod.test_platform_skip.pytestmark)
        skip_markers = [m for m in markers if m.name == "skipif"]
        assert len(skip_markers) == 1


# ---------------------------------------------------------------------------
# Allowed in all file types
# ---------------------------------------------------------------------------

class TestDeftestFileTypes:
    def test_deftest_in_hy(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "test_in_hy.hy", """\
            (require doeff-hy.macros [deftest])
            (import doeff [do :as _doeff-do])
            (deftest test-in-hy (assert True))
        """)
        assert callable(mod.test_in_hy)

    def test_deftest_in_hyk(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "test_in_hyk.hyk", """\
            (require doeff-hy.macros [deftest])
            (import doeff [do :as _doeff-do])
            (deftest test-in-hyk (assert True))
        """)
        assert callable(mod.test_in_hyk)

    def test_deftest_in_hyp(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "test_in_hyp.hyp", """\
            (require doeff-hy.macros [deftest])
            (import doeff [do :as _doeff-do])
            (deftest test-in-hyp (assert True))
        """)
        assert callable(mod.test_in_hyp)


# ---------------------------------------------------------------------------
# Typed binding — (<- name Type expr) at the top level of a deftest body
# ---------------------------------------------------------------------------

class TestDeftestTypedBind:
    """deftest の先頭階の (<- name Type expr) は <- macro と同じ isinstance の保証を出す。

    deftest は本体の <- を自分で読んで yield へ書き換えるため、型欄が捨てられ
    (<- x str eff) が実行時に無検査だった。共通の品質検査(dotfiles
    agent/quality/hy_dsl.py effect_bind)はその実態に合わせて型を object に投影
    せざるを得ず、ACP の段 0 の法で native-type が 30 件以上出た。定義点は
    macros.hy の _bind-yield 1 つ(<- macro / do! / defp / for/do と共有)。
    """

    def _import(self, tmp_hy_dir, filename, body):
        (tmp_hy_dir / filename).write_text(textwrap.dedent("""\
            (require doeff-hy.macros [deftest <-])
            (import test-deftest-macro [GetValue])
        """) + textwrap.dedent(body))
        sys.modules.pop(filename.removesuffix(".hy"), None)
        importlib.invalidate_caches()
        sys.path_importer_cache.clear()
        return importlib.import_module(filename.removesuffix(".hy"))

    def test_typed_bind_passes_when_type_matches(self, tmp_hy_dir):
        mod = self._import(tmp_hy_dir, "typed_ok_test.hy", """\
            (deftest test-typed-ok
              (<- price float (GetValue :key "price"))
              (<- name str (GetValue :key "name"))
              (assert (= price 100.0))
              (assert (= name "TestCo")))
        """)
        mod.test_typed_ok(stub_interpreter)

    def test_typed_bind_fails_when_type_mismatches(self, tmp_hy_dir):
        """型違いは束縛の直後に AssertionError — 後続の assert に届く前に落ちる。"""
        mod = self._import(tmp_hy_dir, "typed_bad_test.hy", """\
            (deftest test-typed-bad
              (<- price str (GetValue :key "price"))
              (assert False "unreachable: isinstance guard must fire first"))
        """)
        with pytest.raises(AssertionError, match=r"expected str, got float"):
            mod.test_typed_bad(stub_interpreter)

    def test_untyped_bind_is_unchanged(self, tmp_hy_dir):
        """2 要素・3 要素の形は今日のまま(型検査を足さない)。"""
        mod = self._import(tmp_hy_dir, "untyped_test.hy", """\
            (deftest test-untyped
              (<- price (GetValue :key "price"))
              (<- (GetValue :key "name"))
              (assert (= price 100.0)))
        """)
        mod.test_untyped(stub_interpreter)
