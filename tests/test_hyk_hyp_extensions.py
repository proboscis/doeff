"""Tests for .hyk/.hyp file extension support and macro enforcement.

Verifies:
  - .hyk and .hyp are registered as Hy source suffixes
  - .hyk files can be imported and contain defk
  - .hyp files can be imported and contain defp
  - defp in .hyk raises SyntaxError
  - defk in .hyp emits a warning
  - defprogram raises SyntaxError (removed)
  - Existing .hy files are unaffected
"""

import importlib
import importlib.machinery
import os
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

import hy
import hy.compiler
import hy.reader

import doeff_hy  # registers .hyk/.hyp extensions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_hy_dir(tmp_path):
    """Create a temp directory on sys.path for .hyk/.hyp imports."""
    sys.path.insert(0, str(tmp_path))
    yield tmp_path
    sys.path.remove(str(tmp_path))
    # Clean up imported modules
    for name in list(sys.modules):
        mod = sys.modules[name]
        if hasattr(mod, "__file__") and mod.__file__ and str(tmp_path) in mod.__file__:
            del sys.modules[name]
    importlib.invalidate_caches()
    sys.path_importer_cache.clear()


def _write_and_import(tmp_path, filename, code):
    """Write a Hy file and import it, clearing caches."""
    filepath = tmp_path / filename
    filepath.write_text(textwrap.dedent(code))
    mod_name = filepath.stem
    # Clear any cached bytecode
    cache_dir = tmp_path / "__pycache__"
    if cache_dir.exists():
        for f in cache_dir.iterdir():
            if mod_name in f.name:
                f.unlink()
    # Clear from sys.modules if previously imported
    sys.modules.pop(mod_name, None)
    importlib.invalidate_caches()
    sys.path_importer_cache.clear()
    return importlib.import_module(mod_name)


# ---------------------------------------------------------------------------
# Extension registration
# ---------------------------------------------------------------------------

class TestExtensionRegistration:
    def test_hyk_in_source_suffixes(self):
        assert ".hyk" in importlib.machinery.SOURCE_SUFFIXES

    def test_hyp_in_source_suffixes(self):
        assert ".hyp" in importlib.machinery.SOURCE_SUFFIXES

    def test_hy_still_in_source_suffixes(self):
        assert ".hy" in importlib.machinery.SOURCE_SUFFIXES


# ---------------------------------------------------------------------------
# Import .hyk files
# ---------------------------------------------------------------------------

class TestHykImport:
    def test_import_hyk_module(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "mylib_k.hyk", """\
            (setv VERSION "1.0")
            (defn greet [name] (+ "Hello " name))
        """)
        assert mod.__file__.endswith(".hyk")
        assert mod.VERSION == "1.0"
        assert mod.greet("world") == "Hello world"

    def test_hyk_defk_compiles(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "kleisli_mod.hyk", """\
            (require doeff-hy.macros [defk <-])
            (import doeff [do :as _doeff-do])
            (import doeff [EffectBase])
            (import dataclasses [dataclass])
            (defclass [(dataclass :frozen True)] Greet [EffectBase]
              #^ str name)
            (defk say-hello [prefix]
              {:pre [(: prefix str)] :post [(: % str)]}
              (<- name (Greet :name "world"))
              (+ prefix " " name))
        """)
        assert callable(mod.say_hello)
        assert mod.say_hello.__doeff_name__ == "say-hello"


# ---------------------------------------------------------------------------
# Import .hyp files
# ---------------------------------------------------------------------------

class TestHypImport:
    def test_import_hyp_module(self, tmp_hy_dir):
        mod = _write_and_import(tmp_hy_dir, "prog_mod.hyp", """\
            (setv ENTRY "main")
            (defn run-it [] "running")
        """)
        assert mod.__file__.endswith(".hyp")
        assert mod.ENTRY == "main"

    def test_hyp_defp_compiles(self, tmp_hy_dir):
        from doeff import DoExpr

        mod = _write_and_import(tmp_hy_dir, "entry_mod.hyp", """\
            (require doeff-hy.macros [defp])
            (import doeff [do :as _doeff-do])
            (defp p-hello
              {:post [(: % str)]}
              "hello")
        """)
        assert isinstance(mod.p_hello, DoExpr)


# ---------------------------------------------------------------------------
# Cross-import between .hyk and .hyp
# ---------------------------------------------------------------------------

class TestCrossImport:
    def test_hyp_imports_from_hyk(self, tmp_hy_dir):
        from doeff import DoExpr

        _write_and_import(tmp_hy_dir, "lib_cross.hyk", """\
            (setv MAGIC 42)
        """)
        mod = _write_and_import(tmp_hy_dir, "prog_cross.hyp", """\
            (require doeff-hy.macros [defp])
            (import doeff [do :as _doeff-do])
            (import lib-cross [MAGIC])
            (defp p-magic
              {:post [(: % int)]}
              MAGIC)
        """)
        assert isinstance(mod.p_magic, DoExpr)


# ---------------------------------------------------------------------------
# Macro enforcement: defp blocked in .hyk
# ---------------------------------------------------------------------------

class TestDefpBlockedInHyk:
    def test_defp_in_hyk_raises_syntax_error(self, tmp_hy_dir):
        with pytest.raises(Exception, match="cannot define a Program entrypoint in a .hyk file"):
            _write_and_import(tmp_hy_dir, "bad_prog.hyk", """\
                (require doeff-hy.macros [defp])
                (import doeff [do :as _doeff-do])
                (defp bad-program {:post []} 42)
            """)

    def test_defpp_in_hyk_raises_syntax_error(self, tmp_hy_dir):
        with pytest.raises(Exception, match="cannot define a Program entrypoint in a .hyk file"):
            _write_and_import(tmp_hy_dir, "bad_pp.hyk", """\
                (require doeff-hy.macros [defpp])
                (import doeff [do :as _doeff-do])
                (defpp bad-meta {:post []} 42)
            """)


# ---------------------------------------------------------------------------
# Macro enforcement: defk warning in .hyp
# ---------------------------------------------------------------------------

class TestDefkWarningInHyp:
    def test_defk_in_hyp_warns(self, tmp_hy_dir):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _write_and_import(tmp_hy_dir, "mixed_hyp.hyp", """\
                (require doeff-hy.macros [defk])
                (import doeff [do :as _doeff-do])
                (defk helper-fn [x]
                  {:pre [(: x int)] :post [(: % int)]}
                  x)
            """)
            user_warnings = [x for x in w if issubclass(x.category, UserWarning)]
            assert any(".hyp files are for Program entrypoints" in str(x.message) for x in user_warnings)


# ---------------------------------------------------------------------------
# defprogram removed
# ---------------------------------------------------------------------------

class TestDefprogramRemoved:
    def test_defprogram_raises_syntax_error(self):
        code = """\
            (require doeff-hy.macros [defprogram])
            (import doeff [do :as _doeff-do])
            (defprogram old-style {:post []} 42)
        """
        with pytest.raises(Exception, match="defprogram is removed"):
            tree = hy.reader.read_many(textwrap.dedent(code), filename="test.hy")
            hy.compiler.hy_compile(tree, "__main__")


# ---------------------------------------------------------------------------
# Existing .hy files unaffected
# ---------------------------------------------------------------------------

class TestHyUnaffected:
    def test_defp_in_hy_no_error(self, tmp_hy_dir):
        """defp in .hy should work without error or warning."""
        from doeff import DoExpr

        mod = _write_and_import(tmp_hy_dir, "normal_hy.hy", """\
            (require doeff-hy.macros [defp])
            (import doeff [do :as _doeff-do])
            (defp p-normal {:post [(: % int)]} 42)
        """)
        assert isinstance(mod.p_normal, DoExpr)

    def test_defk_in_hy_no_warning(self, tmp_hy_dir):
        """defk in .hy should work without warning."""
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            _write_and_import(tmp_hy_dir, "normal_k.hy", """\
                (require doeff-hy.macros [defk])
                (import doeff [do :as _doeff-do])
                (defk normal-fn [x]
                  {:pre [(: x int)] :post [(: % int)]}
                  x)
            """)
            hyp_warnings = [x for x in w if ".hyp files" in str(x.message)]
            assert len(hyp_warnings) == 0
