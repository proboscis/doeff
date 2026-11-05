"""Test that the compile of the source of the functions dataclass builds per type (``__create_fn__``) is remembered in a store (code_store) keyed by the source content, and that the next process does not
compile again.

Measurement (2026-10-11, loading agora-controllers' test_emulated_screen.hy): almost all of the cost of creating 3798 dataclass types was the exec (compile) of function
source. There were 769 distinct sources, and using the previous process's code cut the load CPU median from 3.71 to 3.02 s.
Compile is determined only by the source text, the Python version, and the optimization level, so the remembered code is the same as freshly compiled code.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

#: Child process — install the wrapper, count the function-source compiles (the sum of execs of source as text and of compiles), and print the type's behavior.
#: dataclasses passes text to exec and compiles inside, so both exec and compile are counted.
_CHILD = """\
import builtins
import collections
import dataclasses
import doeff_hy_bytecode_guard

doeff_hy_bytecode_guard.install()
compiled = collections.Counter()
plain_compile, plain_exec = builtins.compile, builtins.exec


def is_function_source(source):
    return isinstance(source, str) and "__create_fn__" in source


def counting_compile(source, *args, **kwargs):
    compiled.update(["source"] if is_function_source(source) else [])
    return plain_compile(source, *args, **kwargs)


def counting_exec(source, *args, **kwargs):
    compiled.update(["source"] if is_function_source(source) else [])
    return plain_exec(source, *args, **kwargs)


builtins.compile, builtins.exec = counting_compile, counting_exec
Pair = dataclasses.make_dataclass("Pair", [("left", int), ("right", str)], frozen=True, order=True)
one, same, other = Pair(1, "a"), Pair(1, "a"), Pair(2, "b")
try:
    one.left = 3
    frozen = False
except dataclasses.FrozenInstanceError:
    frozen = True
print(compiled["source"], repr(one), one == same, hash(one) == hash(same), one < other, frozen)
"""


def _run_child(tmp_path: Path, store: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Run one child process and return the output fields (compile count, repr, equality, hash, ordering, frozen). The child inherits the test process's environment
    and only the two below are replaced (monkeypatch restores them after the test)."""
    script = tmp_path / "child.py"
    script.write_text(_CHILD)
    # The child also does not write .pyc under the checkout (the root conftest check — if it did, the wrapper module's .pyc would remain in the editable checkout).
    # The store is named explicitly, so add it even with the setting that writes no .pyc (the code_store convention).
    monkeypatch.setenv("DOEFF_HY_CODE_STORE", store)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    completed = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120, check=False
    )
    assert completed.returncode == 0, completed.stderr
    count, *rest = completed.stdout.rsplit(maxsplit=4)
    head, _, representation = count.partition(" ")
    return [head, representation, *rest]


def test_a_second_process_reuses_the_dataclass_functions_without_compiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Add the function code the first process compiled to the store, and the second process uses it without compiling. Behavior (repr, equality,
    hash, ordering, frozen) is the same as compiled code."""
    store = tmp_path / "store"
    first = _run_child(tmp_path, str(store), monkeypatch)
    assert int(first[0]) >= 1, f"the first process did not compile the function source: {first}"
    assert list(store.rglob("*.code")), "the first process did not add to the store"
    second = _run_child(tmp_path, str(store), monkeypatch)
    assert second[0] == "0", f"the second process recompiled the function source {second[0]} times"
    assert second[1:] == first[1:] == ["Pair(left=1, right='a')", "True", "True", "True", "True"]


def test_an_off_store_compiles_in_every_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the setting that does not use the store (``off``), nothing is remembered and every process compiles (behavior is the same)."""
    first = _run_child(tmp_path, "off", monkeypatch)
    second = _run_child(tmp_path, "off", monkeypatch)
    assert int(first[0]) >= 1 and int(second[0]) >= 1
    assert second[1:] == first[1:]
