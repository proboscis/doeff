"""Failure cases for expanding the dependencies of doeff-hy-check in parallel in child processes.

- The parallel result (expansion text, position mapping, findings, list of failures) is the same as serial -- even the Hy gensym names in the expansion text.
- Even if one file fails to expand, the results of the other files in the same stage are complete.
- If a child process dies without returning an answer, the file with no answer is named and made a tool failure (not silently dropped).
- A run where everything can be taken from the store does not start a pool (does not add seconds when the store works).
- The entry launched with `python -m doeff_hy._static_check` (this file is read as __main__ in the parent and __mp_main__ in expansion children) also: the next run's parent takes the store that the expansion children
  wrote.
"""

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from doeff_hy import ExpansionWorkerLostError
from doeff_hy import _static_check as static_check
from doeff_hy._check_slot import SlotHeld
from doeff_hy._static_check import Closure, project_closure

#: Three modules the root file imports. In each, a macro in the file uses gensym, and each has a finding for an outermost setv.
LEAF = """\
(defmacro kept-twice [x]
  (setv g (hy.gensym "kept"))
  `(do (setv ~g ~x) (+ ~g ~g)))
(setv {name} (kept-twice {value}))
"""

ROOT = """\
(import alpha beta gamma)
(setv total (+ alpha.alpha beta.beta gamma.gamma))
"""


def _tree(root: Path, broken: str | None = None, crashing: str | None = None) -> Path:
    """Place the root file (main.hy) and the alpha, beta and gamma it imports. broken is a module that fails to expand, and crashing is a module that kills the child process
    partway through expansion."""
    for number, name in enumerate(("alpha", "beta", "gamma"), 1):
        text = LEAF.format(name=name, value=number)
        if name == broken:
            text = "(defmacro)\n"
        if name == crashing:
            text = "(defmacro gone [] (import os) (os._exit 3))\n(gone)\n"
        (root / f"{name}.hy").write_text(text, encoding="utf-8")
    main = root / "main.hy"
    main.write_text(ROOT, encoding="utf-8")
    return main


@dataclass(frozen=True)
class Shape:
    """The shape to compare: per-file expansion text, position mapping, findings, and the names of failed files (independent of order)."""

    projections: dict[str, tuple[object, ...]]
    failures: list[str]


def _shape(closure: Closure) -> Shape:
    return Shape(
        {
            path.name: (projection.text, projection.spans, projection.findings)
            for path, projection in closure.projections.items()
        },
        sorted(failure.source.name for failure in closure.failures),
    )


def test_expanding_in_child_processes_gives_the_serial_answer(tmp_path: Path) -> None:
    main = _tree(tmp_path)
    serial = project_closure(tmp_path, [tmp_path], [main], None, limit=1)
    parallel = project_closure(tmp_path, [tmp_path], [main], None, limit=3)
    assert len(serial.projections) == 4, serial
    assert _shape(parallel) == _shape(serial)


def test_a_failed_expansion_does_not_drop_the_others_of_its_wave(tmp_path: Path) -> None:
    main = _tree(tmp_path, broken="beta")
    closure = project_closure(tmp_path, [tmp_path], [main], None, limit=3)
    shape = _shape(closure)
    assert shape.failures == ["beta.hy"]
    assert sorted(shape.projections) == ["alpha.hy", "gamma.hy", "main.hy"]


def test_a_lost_child_names_the_files_without_an_answer(tmp_path: Path) -> None:
    main = _tree(tmp_path, crashing="gamma")
    with pytest.raises(ExpansionWorkerLostError, match=r"gamma\.hy"):
        project_closure(tmp_path, [tmp_path], [main], None, limit=3)
    assert issubclass(
        ExpansionWorkerLostError, RuntimeError
    )  # main makes a RuntimeError a tool failure (exit 2)


def test_a_run_that_reads_everything_from_the_store_starts_no_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    main = _tree(tmp_path)
    cache = tmp_path / ".cache"
    first = project_closure(tmp_path, [tmp_path], [main], cache, limit=3)
    started: list[int] = []
    real = static_check._start_pool

    def counting(workers: int) -> object:
        started.append(workers)
        return real(workers)

    monkeypatch.setattr(static_check, "_start_pool", counting)
    second = project_closure(tmp_path, [tmp_path], [main], cache, limit=3)
    assert started == [], "a run where everything can be taken from the store started a pool"
    assert _shape(second) == _shape(first)


#: A child process that launches doeff_hy._static_check under the name __main__, like the daily command (same as `python -m` -- expansion children read it under the name __mp_main__
#: ). It prints the number of store hits (hit) and misses (miss) as JSON on the last line. pyright is a fake that returns an answer with no diagnostics (named by an argument).
MAIN_CHILD = """\
import json, runpy, sys
import doeff_hy._static_cache as cache
seen = {"hit": 0, "miss": 0}
real = cache.load
def load(cache_dir, name):
    answer = real(cache_dir, name)
    seen["hit" if isinstance(answer, cache.CachedProjection) else "miss"] += 1
    return answer
cache.load = load
# A warmed-up run takes from the closure bundle row and does not call the entry load -- the number taken by the bundle row is also counted. A row is, when the same as when stat wrote it,
# taken by row_is_current, and a row just written whose stat is not trusted is taken by the name of the content location and checking the record (record_is_current_here, which the entry calls directly).
# Both check the macro record against the current environment (it cannot be taken if the entry's name and the expansion child's name disagree).
import doeff_hy_bytecode_guard._loader_hooks as guard
real_row = cache.row_is_current
def row_is_current(row, module, stat):
    current = real_row(row, module, stat)
    seen["hit"] += current
    return current
cache.row_is_current = row_is_current
real_record = guard.record_is_current_here
def record_is_current_here(used):
    current = real_record(used)
    seen["hit"] += current
    return current
guard.record_is_current_here = record_is_current_here
root, cache_dir, pyright = sys.argv[1:4]
sys.argv = ["doeff-hy-check", "--root", root, "--cache-dir", cache_dir, "--pyright", pyright, root]
try:
    runpy.run_module("doeff_hy._static_check", run_name="__main__", alter_sys=True)
except SystemExit:
    pass
print(json.dumps(seen))
"""


def test_the_entry_run_as_main_reads_the_store_its_expansion_children_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Red before the fix: the record named the post-processing POST_PROCESSING under the module of __name__.
    # In the `python -m` entry, the expansion child wrote a digest that walked the project of __mp_main__, and the next run's parent walked and checked
    # the project of __main__ (the function owner's name and the value of __name__ differ), so even with the same tree and same location not one store entry could be taken
    # (agora-controllers' daily hy-type-trend was re-expanding 592 of 593 even in the same dir).
    project = tmp_path / "proj"
    project.mkdir()
    _tree(project)
    pyright = tmp_path / "pyright"
    pyright.write_text('#!/bin/sh\necho \'{"generalDiagnostics": []}\'\n', encoding="utf-8")
    pyright.chmod(0o755)
    monkeypatch.setenv("PYTHONPYCACHEPREFIX", str(tmp_path / "pycache"))

    def run() -> dict[str, int]:
        done = subprocess.run(
            [sys.executable, "-c", MAIN_CHILD, str(project), str(tmp_path / ".cache"), str(pyright)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        return json.loads(done.stdout.strip().splitlines()[-1])

    assert run() == {"hit": 0, "miss": 4}
    assert run() == {"hit": 4, "miss": 0}, "the next run's parent could not take the store written by the expansion children"


def test_the_expander_limit_is_the_slot_width_and_defaults_to_four_when_none_reaches() -> None:
    """The number of expansion children is the parallel count within the one slot answered by the authoritative source of the machine's check slot (SlotHeld.width). On a machine it cannot reach (a host with no authoritative source, an old source)
    the default is 4 (not the number of cores -- on a 36-core machine two checks started more than 30 children, 2026-10-11). There is no hook to change it with an environment variable
    (with a hook, only the check of the conversation that changed the value would count with a different number, and the way slots are counted would split)."""
    assert static_check.expander_limit(None) == static_check.EXPANDER_LIMIT == 4
    assert static_check.expander_limit(2) == 2
    assert static_check.expander_limit(1) == 1
    assert static_check.expander_limit(8) == 8, "if the declaration goes up, follow it (the declaration owns the value)"


def test_the_entry_hands_the_slot_width_to_the_expanders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entry's main passes the width answered by the slot taker to the upper limit of project_closure (neither the default 4 of a machine with no declaration nor an environment variable)."""
    monkeypatch.setattr(sys, "path", list(sys.path))  # main adds the root to the head of sys.path
    seen: list[int] = []

    def fake_closure(*args: object, **_: object) -> static_check.Closure:
        seen.append(int(args[4]))  # type: ignore[call-overload]
        return static_check.Closure({}, [])

    monkeypatch.setattr(static_check, "take_slot", lambda *_: SlotHeld(None, 2))
    monkeypatch.setattr(static_check, "project_closure", fake_closure)
    monkeypatch.setattr(static_check, "run_pyright", lambda *_args, **_kwargs: static_check.Checked([], []))
    assert static_check.main(["--root", str(tmp_path), "--no-cache"]) == 0
    monkeypatch.setattr(static_check, "take_slot", lambda *_: SlotHeld())
    assert static_check.main(["--root", str(tmp_path), "--no-cache"]) == 0
    assert seen == [2, 4], seen
