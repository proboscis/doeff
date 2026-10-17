"""The Python scheduler's waiters index and ready counts stay in step with the structures.

card ki-1294af4fa525 made Cancel and the external-wait shield read an index and
counts kept beside ``waiters`` and ``ready`` instead of walking them. Those are
kept only by the entry functions; a change that moves ``waiters`` or ``ready``
some other way (the design check's blind B: an effect re-prioritising queued
entries rewrote ``ready`` in place) passes the behaviour tests and then stalls
a run whose live-work count went wrong. Two guards:

- static: inside ``_scheduled_python``, ``waiters`` and ``ready`` are mutated
  only in their entry functions (an AST walk of scheduler.py);
- dynamic: ``_INDEX_SELF_CHECK`` recomputes the index and counts on every step
  and raises SchedulerIndexDriftError — it also sees a mutation through an
  alias, which the AST walk cannot.
"""

from __future__ import annotations

import ast
import gc
import heapq
import types
from pathlib import Path

import doeff_core_effects.scheduler as scheduler_module
import pytest
from doeff_core_effects.scheduler import (
    PRIORITY_IDLE,
    PRIORITY_NORMAL,
    SchedulerIndexDriftError,
    Spawn,
    Wait,
    scheduled,
)

from doeff import do
from doeff import run as doeff_run

pytestmark = pytest.mark.usefixtures("scheduler_index_self_check")

# The functions allowed to mutate each structure (design.md §2 of the design check).
ENTRY_FUNCTIONS = {
    "waiters": frozenset({"park_waiter", "unpark_key", "unpark_at", "keep_waiters"}),
    "ready": frozenset({"ready_push", "ready_pop", "ready_remove_at", "drop_claimed_marks"}),
}
MUTATING_METHODS = frozenset(
    {
        "append",
        "extend",
        "insert",
        "pop",
        "popitem",
        "remove",
        "clear",
        "setdefault",
        "update",
        "sort",
        "reverse",
        "__setitem__",
        "__delitem__",
    }
)
HEAPQ_MUTATORS = frozenset({"heappush", "heappop", "heapify", "heapreplace", "heappushpop"})
SCHEDULER_FUNCTION = "_scheduled_python"


def _root_name(node: ast.AST) -> str | None:
    """The name a subscript chain hangs from (``waiters[k][i]`` → waiters)."""
    while isinstance(node, ast.Subscript):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _mutated_name(node: ast.AST, inner: bool) -> str | None:
    """The guarded structure ``node`` mutates (None when it does not)."""
    names = ENTRY_FUNCTIONS.keys()
    if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete)):
        targets = node.targets if isinstance(node, (ast.Assign, ast.Delete)) else [node.target]
        flat = [
            t
            for target in targets
            for t in ast.walk(target)
            if isinstance(t, (ast.Subscript, ast.Name))
        ]
        hit = next(
            (
                name
                for t in flat
                if (name := _root_name(t)) in names and (isinstance(t, ast.Subscript) or inner)
            ),
            None,
        )
        return hit
    if isinstance(node, ast.Nonlocal):
        return next((name for name in node.names if name in names), None)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        func = node.func
        if func.attr in MUTATING_METHODS and _root_name(func.value) in names:
            return _root_name(func.value)
        if (
            isinstance(func.value, ast.Name)
            and func.value.id == "heapq"
            and func.attr in HEAPQ_MUTATORS
            and node.args
            and _root_name(node.args[0]) in names
        ):
            return _root_name(node.args[0])
    return None


def guarded_mutations(source: str) -> tuple[tuple[str, str, int], ...]:
    """Every mutation of ``waiters``/``ready`` inside _scheduled_python: (structure, function, line)."""
    tree = ast.parse(source)
    scheduler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == SCHEDULER_FUNCTION
    )

    def walk(node: ast.AST, function: str) -> tuple[tuple[str, str, int], ...]:
        found: tuple[tuple[str, str, int], ...] = ()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found = (*found, *walk(child, child.name))
                continue
            name = _mutated_name(child, inner=function != SCHEDULER_FUNCTION)
            if name is not None:
                found = (*found, (name, function, child.lineno))
            found = (*found, *walk(child, function))
        return found

    return walk(scheduler, SCHEDULER_FUNCTION)


def _scheduler_source() -> str:
    return Path(scheduler_module.__file__).read_text(encoding="utf-8")


def test_waiters_and_ready_move_only_through_their_entry_functions() -> None:
    outside = [
        (name, function, line)
        for name, function, line in guarded_mutations(_scheduler_source())
        if function != SCHEDULER_FUNCTION and function not in ENTRY_FUNCTIONS[name]
    ]
    assert outside == [], (
        "waiters / ready mutated outside their entry functions (structure, function, line): "
        f"{outside} — move them through park_waiter/unpark_key/unpark_at/keep_waiters or "
        "ready_push/ready_pop/ready_remove_at/drop_claimed_marks, which keep the index and counts"
    )


def test_the_entry_functions_named_here_exist() -> None:
    # A renamed entry function would leave the allow-list naming nothing.
    defined = {
        node.name
        for node in ast.walk(ast.parse(_scheduler_source()))
        if isinstance(node, ast.FunctionDef)
    }
    assert set().union(*ENTRY_FUNCTIONS.values()) <= defined


def test_the_walk_sees_each_mutating_form_and_no_read() -> None:
    source = """
def _scheduled_python(body):
    import heapq
    waiters = {}
    ready = []

    def mutates():
        ready[:] = []
        heapq.heapify(ready)
        heapq.heappush(ready, (0, 0, ("new", 1)))
        waiters[("task", 1)] = []
        waiters[("task", 1)].append(("wait", 1, None, None))
        waiters.setdefault(("task", 2), []).append(("wait", 2, None, None))
        del waiters[("task", 1)]
        ready.sort()

    def rebinds():
        nonlocal ready

    def reads():
        n = len(ready)
        entries = waiters.get(("task", 1))
        keys = list(waiters)
        live = [e for e in ready]
        return n, entries, keys, live
"""
    found = guarded_mutations(source)
    assert [(name, function) for name, function, _line in found] == [
        ("ready", "mutates"),
        ("ready", "mutates"),
        ("ready", "mutates"),
        ("waiters", "mutates"),
        ("waiters", "mutates"),
        ("waiters", "mutates"),
        ("waiters", "mutates"),
        ("ready", "mutates"),
        ("ready", "rebinds"),
    ]


def _scheduler_functions(name: str) -> tuple[types.FunctionType, ...]:
    gc.collect()
    return tuple(
        obj
        for obj in gc.get_objects()
        if type(obj) is types.FunctionType
        and obj.__name__ == name
        and obj.__code__.co_filename == scheduler_module.__file__
    )


def _cells(function: types.FunctionType) -> dict[str, types.CellType]:
    return dict(zip(function.__code__.co_freevars, function.__closure__ or (), strict=True))


def _run_holding(program: object) -> dict[str, types.CellType]:
    """The closure cells of pick_ready_entry of the scheduler run whose task holds ``program``."""
    for pick_ready_entry in _scheduler_functions("pick_ready_entry"):
        cells = _cells(pick_ready_entry)
        if any(row.get("program") is program for row in cells["tasks"].cell_contents.values()):
            return cells
    raise LookupError("no scheduler run holds the program")


def _park_waiter_of(run: dict[str, types.CellType]) -> types.FunctionType:
    """park_waiter of the same run (it shares the ``waiters`` cell with pick_ready_entry)."""
    return next(
        function
        for function in _scheduler_functions("park_waiter")
        if _cells(function)["waiters"] is run["waiters"]
    )


def _raise_a_queued_idle_start_through_an_alias():
    """A task re-prioritises a queued IDLE start to NORMAL by rewriting ``ready`` through an alias (as the blind B's
    re-prioritising effect did in place): the start is counted as live work below the shield, and stops being there."""

    @do
    def noop():
        return None

    @do
    def body():
        program = noop()
        task = yield Spawn(program, priority=PRIORITY_IDLE)
        heap = _run_holding(program)["ready"].cell_contents
        heap[:] = [
            (-PRIORITY_NORMAL, seq, entry)
            if entry[0] == "new" and entry[1] == task.task_id
            else (neg, seq, entry)
            for neg, seq, entry in heap
        ]
        heapq.heapify(heap)
        yield Wait(task)
        return "done"

    return body()


def test_self_check_catches_ready_moved_outside_its_entry_functions(monkeypatch) -> None:
    monkeypatch.setattr(scheduler_module, "_INDEX_SELF_CHECK", True)
    with pytest.raises(SchedulerIndexDriftError, match="live ready counts"):
        doeff_run(scheduled(_raise_a_queued_idle_start_through_an_alias(), implementation="python"))


def test_without_the_self_check_the_same_drift_goes_unnoticed(monkeypatch) -> None:
    # The control: the run ends normally — the drifted count would only matter at a later shield decision.
    monkeypatch.setattr(scheduler_module, "_INDEX_SELF_CHECK", False)
    assert (
        doeff_run(scheduled(_raise_a_queued_idle_start_through_an_alias(), implementation="python"))
        == "done"
    )


def _refusal(entry_function, argument) -> str | None:
    try:
        entry_function(argument)
    except TypeError as error:
        return str(error)
    return None


def test_the_entry_functions_refuse_an_undeclared_or_misdeclared_kind() -> None:
    # The blind A's changes: a new ready kind taught only to dispatch (Cancel dropped the task without its finally
    # blocks) and a multi-key waiter kind (Cancel threw its state dict in as the continuation). Each is refused where
    # it would enter, before anything moves — the run then ends normally under the self-check.
    @do
    def noop():
        return None

    @do
    def body():
        program = noop()
        task = yield Spawn(program, priority=PRIORITY_IDLE)  # still queued while body looks
        run = _run_holding(program)
        ready_push = run["ready_push"].cell_contents
        park_waiter = _park_waiter_of(run)
        tid, state = task.task_id, {"resolved": False, "waiter_k": None}
        refusals = tuple(
            _refusal(entry_function, argument)
            for entry_function, argument in (
                (ready_push, (-PRIORITY_NORMAL, -1, ("reschedule", tid, None))),
                (ready_push, (-PRIORITY_NORMAL, -1, ("resume", tid, state))),
                (lambda entry: park_waiter(("task", tid), entry), ("some", tid, state, None)),
                (lambda entry: park_waiter(("task", tid), entry), ("wait", tid, state, None)),
            )
        )
        yield Wait(task)
        return refusals

    refusals = doeff_run(scheduled(body(), implementation="python"))
    assert [message and message.split(":", 1)[0] for message in refusals] == [
        "ready entry kind 'reschedule' is not declared",
        "ready entry 'resume' is declared in _CONTINUATION_ENTRY_KINDS but holds dict at [2], not the task's continuation",
        "waiter entry kind 'some' is not declared",
        "waiter entry 'wait' is declared in _SINGLE_KEY_WAITER_KINDS but holds dict at [2], not the task's continuation",
    ], refusals
