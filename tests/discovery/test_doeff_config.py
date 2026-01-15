"""Test ~/.doeff.py configuration file loading."""

import tempfile
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skip(
    reason="CLI doeff_config tests need end-to-end testing with full environment"
)

from doeff import Program, CESKInterpreter
from doeff.__main__ import handle_run
import argparse


def test_loads_default_env_from_home_doeff_py(monkeypatch):
    """Test that __default_env__ is loaded from ~/.doeff.py if it exists."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake home directory
        fake_home = Path(tmpdir) / "home"
        fake_home.mkdir()

        # Create ~/.doeff.py
        doeff_config = fake_home / ".doeff.py"
        doeff_config.write_text(textwrap.dedent("""
            __default_env__ = {
                "base_value": 42,
                "base_msg": "from home config",
            }
        """))

        # Create a test program directory
        test_pkg = Path(tmpdir) / "testpkg"
        test_pkg.mkdir()
        (test_pkg / "__init__.py").write_text(textwrap.dedent("""
            from doeff import Program, do
            from doeff.effects import Dep

            # doeff: interpreter, default
            def my_interpreter(program):
                from doeff import CESKInterpreter
                return CESKInterpreter().run(program)

            @do
            def my_program():
                base_value = yield Dep("base_value")
                base_msg = yield Dep("base_msg")
                return f"{base_msg}: {base_value}"
        """))

        # Patch Path.home() to return fake home
        import sys
        sys.path.insert(0, str(tmpdir))

        try:
            with patch('pathlib.Path.home', return_value=fake_home):
                # Create args (provide interpreter explicitly)
                args = argparse.Namespace(
                    program="testpkg.my_program",
                    interpreter="testpkg.my_interpreter",
                    envs=[],
                    apply=None,
                    transform=None,
                    format="text",
                    report=False,
                    report_verbose=False,
                )

                # This should load ~/.doeff.py and use its __default_env__
                import io
                from contextlib import redirect_stdout

                f = io.StringIO()
                with redirect_stdout(f):
                    result = handle_run(args)

                output = f.getvalue().strip()
                assert result == 0
                assert output == "from home config: 42"

        finally:
            sys.path.remove(str(tmpdir))
            # Clean up sys.modules
            if "_doeff_config" in sys.modules:
                del sys.modules["_doeff_config"]
            if "testpkg" in sys.modules:
                del sys.modules["testpkg"]


def test_default_env_can_contain_program_values(monkeypatch):
    """Test that __default_env__ in ~/.doeff.py can contain Program values."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake home directory
        fake_home = Path(tmpdir) / "home"
        fake_home.mkdir()

        # Create ~/.doeff.py with Program values
        doeff_config = fake_home / ".doeff.py"
        doeff_config.write_text(textwrap.dedent("""
            from doeff import Program

            __default_env__ = {
                "static_value": 10,
                "dynamic_value": Program.pure(32),
            }
        """))

        # Create a test program
        test_pkg = Path(tmpdir) / "testpkg"
        test_pkg.mkdir()
        (test_pkg / "__init__.py").write_text(textwrap.dedent("""
            from doeff import Program, do
            from doeff.effects import Dep

            # doeff: interpreter, default
            def my_interpreter(program):
                from doeff import CESKInterpreter
                return CESKInterpreter().run(program)

            @do
            def my_program():
                static = yield Dep("static_value")
                dynamic = yield Dep("dynamic_value")
                return static + dynamic
        """))

        import sys
        sys.path.insert(0, str(tmpdir))

        try:
            with patch('pathlib.Path.home', return_value=fake_home):
                args = argparse.Namespace(
                    program="testpkg.my_program",
                    interpreter="testpkg.my_interpreter",
                    envs=[],
                    apply=None,
                    transform=None,
                    format="text",
                    report=False,
                    report_verbose=False,
                )

                import io
                from contextlib import redirect_stdout

                f = io.StringIO()
                with redirect_stdout(f):
                    result = handle_run(args)

                output = f.getvalue().strip()
                assert result == 0
                assert output == "42"

        finally:
            sys.path.remove(str(tmpdir))
            # Clean up sys.modules
            if "_doeff_config" in sys.modules:
                del sys.modules["_doeff_config"]
            if "testpkg" in sys.modules:
                del sys.modules["testpkg"]


def test_explicit_env_overrides_default_env(monkeypatch):
    """Test that explicit --env values override ~/.doeff.py __default_env__."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a fake home directory
        fake_home = Path(tmpdir) / "home"
        fake_home.mkdir()

        # Create ~/.doeff.py
        doeff_config = fake_home / ".doeff.py"
        doeff_config.write_text(textwrap.dedent("""
            __default_env__ = {
                "value": 10,
                "msg": "default",
            }
        """))

        # Create a test program with override env
        test_pkg = Path(tmpdir) / "testpkg"
        test_pkg.mkdir()
        (test_pkg / "__init__.py").write_text(textwrap.dedent("""
            from doeff import Program, do
            from doeff.effects import Dep

            # doeff: interpreter, default
            def my_interpreter(program):
                from doeff import CESKInterpreter
                return CESKInterpreter().run(program)

            # Override env
            override_env = {
                "value": 99,
            }

            @do
            def my_program():
                value = yield Dep("value")
                msg = yield Dep("msg")
                return f"{msg}: {value}"
        """))

        import sys
        sys.path.insert(0, str(tmpdir))

        try:
            with patch('pathlib.Path.home', return_value=fake_home):
                args = argparse.Namespace(
                    program="testpkg.my_program",
                    interpreter="testpkg.my_interpreter",
                    envs=["testpkg.override_env"],
                    apply=None,
                    transform=None,
                    format="text",
                    report=False,
                    report_verbose=False,
                )

                import io
                from contextlib import redirect_stdout

                f = io.StringIO()
                with redirect_stdout(f):
                    result = handle_run(args)

                output = f.getvalue().strip()
                assert result == 0
                # value should be overridden to 99, msg stays "default"
                assert output == "default: 99"

        finally:
            sys.path.remove(str(tmpdir))
            # Clean up sys.modules
            if "_doeff_config" in sys.modules:
                del sys.modules["_doeff_config"]
            if "testpkg" in sys.modules:
                del sys.modules["testpkg"]
