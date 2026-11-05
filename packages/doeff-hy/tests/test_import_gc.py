"""Check that the GC threshold is raised only during the outermost load (running a module body, including the imports inside it) and restored when it ends (agora-redesign).

Measurement (2026-10-12, agora-controllers test_emulated_screen.hy, CPython 3.14.3 free-threaded): of the file's total seconds, GC was 7 runs and
0.28-0.38 seconds, all inside the load of a single test module. The free-threaded GC sweeps the whole heap when RSS has grown by more than 10% since the previous GC,
so even with about 1% garbage it sweeps every time loading grows the heap. Raising the threshold (the number of objects allowed to net-increase since the previous GC
- also the upper bound on the garbage that can pile up) to a finite value only during loading gave 2 runs and 0.15 seconds.

In a child process, install the wrapper, load a temporary package, and output the threshold the module body saw and the threshold after loading.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

#: Child process - install the wrapper, load the temporary package (argv[1]) for each scenario (argv[2]), and output the threshold seen as JSON. The module body
#: puts the threshold it saw in its own name ``threshold`` (a module that fails midway is put into the exception).
_CHILD = """\
import gc
import json
import os
import sys
import threading

import __main__
import doeff_hy_bytecode_guard

doeff_hy_bytecode_guard.install()
sys.path.insert(0, sys.argv[1])
scene = sys.argv[2]
ENTERED, RELEASE = threading.Event(), threading.Event()
before = gc.get_threshold()
result = {"before": before}


def seen(*names):
    return {name: sys.modules[f"pkg.{name}"].threshold for name in names}


def import_in_thread(name):
    worker = threading.Thread(target=__import__, args=(name,))
    worker.start()
    assert ENTERED.wait(30)
    return worker


if scene == "nested":
    import pkg.outer
    result["seen"] = {**seen("outer", "inner"), "outer-after-inner": pkg.outer.threshold_after_inner}
elif scene == "raises":
    try:
        import pkg.bad
    except RuntimeError as failure:
        result["seen"] = {"bad": failure.args[0]}
elif scene == "own":
    import pkg.own
elif scene == "off":
    gc.set_threshold(0, *before[1:])
    import pkg.inner
    result["seen"] = seen("inner")
elif scene == "two-threads":
    worker = import_in_thread("pkg.slow")
    import pkg.inner
    result["while_other_imports"] = gc.get_threshold()
    RELEASE.set()
    worker.join()
    result["seen"] = seen("slow", "inner")
elif scene == "fork":
    worker = import_in_thread("pkg.slow")
    read_end, write_end = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.write(write_end, json.dumps(gc.get_threshold()).encode())
        os._exit(0)
    os.close(write_end)
    os.waitpid(pid, 0)
    result["child"] = json.loads(os.read(read_end, 1000))
    RELEASE.set()
    worker.join()
    result["seen"] = seen("slow")
result["after"] = gc.get_threshold()
print(json.dumps(result))
"""

#: Modules of the temporary package (name -> body).
_MODULES = {
    "outer": "import gc\nthreshold = gc.get_threshold()\nimport pkg.inner\nthreshold_after_inner = gc.get_threshold()\n",
    "inner": "import gc\nthreshold = gc.get_threshold()\n",
    "bad": "import gc\nraise RuntimeError(gc.get_threshold())\n",
    "own": "import gc\ngc.set_threshold(500)\n",
    "slow": "import gc\nimport __main__\nthreshold = gc.get_threshold()\n__main__.ENTERED.set()\nassert __main__.RELEASE.wait(30)\n",
}


def _run(tmp_path: Path, scene: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Run one scenario in a child process and return the seen thresholds (before, seen, after, etc.). The child inherits the check process's environment and adds only the setting that does not write .pyc
    (monkeypatch restores it after the check)."""
    package = tmp_path / "pkg"
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("")
    for name, body in _MODULES.items():
        (package / f"{name}.py").write_text(body)
    script = tmp_path / "child.py"
    script.write_text(_CHILD)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    completed = subprocess.run(
        [sys.executable, "-W", "ignore::DeprecationWarning", str(script), str(tmp_path), scene],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout.splitlines()[-1])


def test_the_threshold_is_raised_while_the_outermost_import_runs_and_restored_after(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Both the outermost load body and the inner import bodies see the raised threshold (the lower two fields are unchanged). When it ends, the previous threshold is restored."""
    result = _run(tmp_path, "nested", monkeypatch)
    before = result["before"]
    seen = result["seen"]
    raised = seen["outer"]
    assert raised[0] > before[0], f"the threshold during loading did not go up: before {before} -> during loading {raised}"
    assert raised[1:] == before[1:]
    assert seen["inner"] == seen["outer-after-inner"] == raised
    assert result["after"] == before


def test_the_threshold_is_restored_when_the_import_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Even if loading ends with an exception, the previous threshold is restored."""
    result = _run(tmp_path, "raises", monkeypatch)
    assert result["seen"]["bad"][0] > result["before"][0]
    assert result["after"] == result["before"]


def test_a_threshold_set_by_the_imported_module_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A threshold that the loaded module set itself is not overwritten at the end."""
    result = _run(tmp_path, "own", monkeypatch)
    assert result["after"][0] == 500


def test_a_zero_threshold_is_left_off_during_the_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Threshold 0 (automatic GC turned off) stays 0 during loading too (a turned-off GC is not started)."""
    result = _run(tmp_path, "off", monkeypatch)
    assert result["seen"] == {"inner": [0, *result["before"][1:]]}
    assert result["after"][0] == 0


def test_the_threshold_stays_raised_until_the_last_of_two_concurrent_imports_ends(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The threshold is one per process. If the first load ends while another thread's load is still going, it is not restored, and when the last load
    ends, it returns to the threshold from before either load."""
    result = _run(tmp_path, "two-threads", monkeypatch)
    raised = result["seen"]["slow"]
    assert raised[0] > result["before"][0]
    assert result["while_other_imports"] == result["seen"]["inner"] == raised
    assert result["after"] == result["before"]


def test_a_child_forked_while_another_thread_imports_gets_the_threshold_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A child forked in the middle of another thread's load does not have that thread (the load does not end inside the child), so the previous threshold is restored."""
    result = _run(tmp_path, "fork", monkeypatch)
    assert result["seen"]["slow"][0] > result["before"][0]
    assert result["child"] == result["before"]
    assert result["after"] == result["before"]
