"""Hy の `ast.unparse` の差し替えが Python 3.14 の注記の文字列化で止まらなくなる不具合の回帰。

仕組みと直し方は doeff_hy/ast_unparse.py。暴走するとメモリを秒 150 MB で食うので、確かめは
子 process の中で行い、子は自分の最大 RSS を見張って 400 MB を超えたら exit 3 で終わる。
"""

from __future__ import annotations

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

needs_314 = pytest.mark.skipif(
    sys.version_info < (3, 14), reason="annotationlib の STRING 形式は Python 3.14 から"
)

WATCHDOG = """
import os, resource, sys, threading, time
def _watch():
    scale = 1 if sys.platform == "darwin" else 1024  # ru_maxrss: macOS は byte・Linux は KiB
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
        import hy  # Hy の差し替えを先に入れる
        import doeff_hy  # noqa: F401 - 置き換えを当てる
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
            import hy  # noqa: F401 - 他の plugin が先に Hy を読む状況
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
        sys.exit(pytest.main(["-q", "-p", "doeff_hy.pytest_plugin", "-p", "no:doeff_hy",
                              "--rootdir", ".", "-c", "/dev/null", "test_probe.py"]))
        """,
        tmp_path,
    )
    assert done.returncode == 0, done.stdout + done.stderr


def test_keyword_mincing_is_kept_and_constant_values_are_not_copied() -> None:
    import doeff_hy  # noqa: F401
    import hy.compat

    if "rewriting_unparse" not in vars(hy.compat):
        pytest.skip("この版の Hy は ast.unparse を差し替えない")
    assert ast.unparse is hy.compat.rewriting_unparse
    # 予約語 def を数学用の文字で綴った名前は、Hy の写しで "\U0001d41def" になる
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
