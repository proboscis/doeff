"""Regression for the bug where Hy's `ast.unparse` replacement fails to terminate when stringifying Python 3.14 annotations.

The mechanism and the fix are in doeff_hy/_ast_unparse.py. A runaway eats 150 MB of memory per second, so the check
runs inside a child process; the child watches its own max RSS and exits with code 3 if it exceeds 400 MB.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

needs_314 = pytest.mark.skipif(
    sys.version_info < (3, 14), reason="the annotationlib STRING format exists only from Python 3.14"
)

WATCHDOG = """
import os, resource, sys, threading, time
def _watch():
    scale = 1 if sys.platform == "darwin" else 1024  # ru_maxrss: bytes on macOS, KiB on Linux
    while True:
        if resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale > 400 * 1024 * 1024:
            os._exit(3)
        time.sleep(0.05)
threading.Thread(target=_watch, daemon=True).start()
"""


def _run_child(body: str, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    script = cwd / "child.py"
    script.write_text(WATCHDOG + textwrap.dedent(body), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(script), *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
        cwd=cwd,
    )


@needs_314
def test_signature_string_format_with_list_in_subscript_finishes(tmp_path: Path) -> None:
    done = _run_child(
        """
        import inspect
        import hy  # install the Hy replacement first
        import doeff_hy  # noqa: F401 - apply the replacement
        from annotationlib import Format
        from collections.abc import Callable

        class Widget: ...

        def fixture(x: Callable[[int], Widget]) -> Callable[[], dict[str, list[Widget]]]: ...

        print(inspect.signature(fixture, annotation_format=Format.STRING))
        """,
        tmp_path,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Callable[[int], Widget]" in done.stdout


@needs_314
def test_pytest_plugin_protects_a_conftest_that_imports_hy_first(tmp_path: Path) -> None:
    (tmp_path / "conftest.py").write_text(
        textwrap.dedent(
            """
            import pytest
            import hy  # noqa: F401  # situation where another plugin reads Hy first
            from collections.abc import Callable

            class Widget: ...

            @pytest.fixture
            def widget() -> Callable[[int], Widget]:
                return lambda n: Widget()
            """
        ),
        encoding="utf-8",
    )
    (tmp_path / "test_probe.py").write_text(
        "def test_probe(widget):\n    assert widget(1) is not None\n", encoding="utf-8"
    )
    done = _run_child(
        """
        import sys
        import pytest
        sys.exit(pytest.main(["-q", "-p", "doeff_hy_pytest", "-p", "no:doeff_hy",
                              "--rootdir", ".", "-c", "/dev/null", "test_probe.py"]))
        """,
        tmp_path,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_keyword_mincing_is_kept_and_constant_values_are_not_copied() -> None:
    import doeff_hy  # noqa: F401
    import hy.compat

    if "rewriting_unparse" not in vars(hy.compat):
        pytest.skip("this version of Hy does not replace ast.unparse")
    assert ast.unparse is hy.compat.rewriting_unparse
    # a name spelling the reserved word def with mathematical characters becomes "\U0001d41def" in Hy's copy
    minced = ast.unparse(ast.parse("x.\U0001d555\U0001d556\U0001d557 = 1"))
    assert "\U0001d41def" in minced

    class Probe:
        copies = 0

        def __deepcopy__(self, memo: dict[int, object]) -> Probe:
            Probe.copies += 1
            return Probe()

        def __repr__(self) -> str:
            return "probe"

    tree = ast.Expression(body=ast.Constant(value=[Probe()]))
    assert ast.unparse(tree) == "[probe]"
    assert Probe.copies == 0
