"""inspect.stack の軽い版(doeff_hy.light_stack)の契約(agora-redesign #1293)。"""
import importlib.util
import inspect
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

import doeff_hy.light_stack as light_stack

_MODULE_PATH = Path(light_stack.__file__)


@pytest.fixture
def installed():
    light_stack.install()
    yield
    light_stack.install()  # 他のテストのため、もとの状態(入った状態)に戻す


def test_importing_doeff_hy_installs_the_shim():
    assert inspect.stack is light_stack._light_stack


def test_install_twice_does_not_wrap_twice(installed):
    light_stack.install()
    light_stack.install()
    assert inspect.stack is light_stack._light_stack
    light_stack.uninstall()
    assert inspect.stack is light_stack._original_stack


def test_non_hy_caller_gets_the_original_result(installed):
    calls = []
    original = light_stack._original_stack

    def spy(context=1):
        calls.append(context)
        return original(context)

    light_stack._original_stack = spy
    try:
        result = inspect.stack(0)
    finally:
        light_stack._original_stack = original
    assert calls == [0]
    assert sys._getframe(0) in [info.frame for info in result]
    assert hasattr(result[0], "filename")


def test_hy_caller_does_not_call_the_original(installed):
    def boom(context=1):
        raise AssertionError("元の inspect.stack が呼ばれた")

    original = light_stack._original_stack
    light_stack._original_stack = boom
    try:
        namespace = {"__name__": "hy.macros", "inspect": inspect}
        exec("def f():\n    return inspect.stack()\n", namespace)
        frames = namespace["f"]()
    finally:
        light_stack._original_stack = original
    assert frames[0][0].f_code.co_name == "f"
    assert frames[1][0].f_code.co_name == "test_hy_caller_does_not_call_the_original"


def test_require_result_is_the_same_before_and_after(tmp_path):
    (tmp_path / "shimmacros.hy").write_text("(defmacro twice [x] `(+ ~x ~x))\n")
    (tmp_path / "shimuser.hy").write_text(
        "(require shimmacros [twice])\n(setv value (twice 21))\n"
    )
    script = textwrap.dedent(
        """
        import sys, inspect
        sys.path.insert(0, {tmp!r})
        import hy
        import doeff_hy.light_stack as ls
        if sys.argv[1] == "off":
            ls.uninstall()
        else:
            ls.install()
        import shimuser
        print(shimuser.value, "shimmacros" in sys.modules)
        """
    ).format(tmp=str(tmp_path))
    outputs = {
        mode: subprocess.run(
            [sys.executable, "-c", script, mode], capture_output=True, text=True, check=True
        ).stdout
        for mode in ("off", "on")
    }
    assert outputs["off"] == outputs["on"] == "42 True\n"


def test_shim_on_real_pypi_hy_131(tmp_path):
    (tmp_path / "shimmacros.hy").write_text("(defmacro twice [x] `(+ ~x ~x))\n")
    (tmp_path / "shimuser.hy").write_text(
        "(require shimmacros [twice])\n(setv value (twice 21))\n"
    )
    script = textwrap.dedent(
        """
        import importlib.util, inspect, sys, time
        sys.path.insert(0, {tmp!r})
        import hy
        assert hy.__version__ == "1.3.1", hy.__version__
        spec = importlib.util.spec_from_file_location("light_stack_alone", {path!r})
        ls = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ls)
        ls.install()
        assert "doeff" not in sys.modules
        started = time.perf_counter()
        import shimuser
        print(shimuser.value, round(time.perf_counter() - started, 3))
        """
    ).format(tmp=str(tmp_path), path=str(_MODULE_PATH))
    completed = subprocess.run(
        ["uv", "run", "--no-project", "--python", "3.12", "--with", "hy==1.3.1", "python", "-c", script],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.split()[0] == "42"
