"""Failure cases where doeff-hy-check (main of doeff_hy._static_check) takes one machine check slot before expansion and pyright (agora-redesign).

Before the fix, a doeff-hy-check started directly by a conversation (`uv run doeff-hy-check …`, `python -m doeff_hy._static_check …`) ran without taking a slot --
only the children of agora-controllers' manual entry (the gate in scripts/hy_type_check.hy) were inside a slot.

The edge check copies the machine's canon (~/dotfiles/agent/tests/machine_check_slots.py) under the specimen's home and puts a declaration next to it (K = 1, location = the specimen's dir)
-- it does not fill the machine's real slots, and is not made to wait by real holders. The other process that holds a slot is the canon's command wrapper run. doeff-hy-check
fires the real entry (`python -m doeff_hy._static_check`) in a child process; only pyright is faked (a file records that it was fired). On hosts without the canon
(a machine that cloned only doeff) it is skipped.

The contract between the M-slot (doeff_hy._check_slot) and the canon is checked with a canon that has only its interface (INTERFACE_ONLY_CANON) -- the policy and holding are
passed to the canon's interface without being opened, the canon can read an environment variable of any name, a failure inside the canon's take is named and passed through,
the number of concurrent processes within one slot is answered by the canon's interface process_width, and a slot already taken is kept even if the interface is missing, fails, or answers in the wrong shape.
The mechanical check of the form that does not open the policy and holding is tests/test_check_slot_opacity.py.
"""

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import pytest
from doeff_hy._check_slot import CANON_UNDER_HOME, MARK, MODULE_KEY_PREFIX, SlotHeld, canon_module, take_slot
from doeff_hy._env_places import cache_home

#: The machine canon (under the check process's home -- the source copied to the specimen's home).
CANON = Path.home() / CANON_UNDER_HOME
#: A sample of an environment variable name the canon reads (a name doeff does not know -- checks that the answer arrives whatever name the canon reads).
PROBE_NAME = "DOEFF_HY_CHECK_SLOT_PROBE"
#: A canon with only the interface (placed at the canon's location in the specimen's home). read_policy returns a marker object, and take records in the holding whether the policy it received is the marker itself,
#: the result of reading the environment variable PROBE_NAME, and the announcement, and returns it; the holding counts how many times it was released. The records remain in the module's TAKES (what take received)
#: and WIDTHS (what process_width received) -- the check reads them from the canon in sys.modules (SlotHeld does not keep the holding). A line with __FAIL__ is a failure
#: inside take, a line with __WIDTH__ is the answer of process_width (the mutation that removes the interface itself is WITHOUT_WIDTH_MOUTH).
INTERFACE_ONLY_CANON = f"""
POLICY = object()
TAKES = []
WIDTHS = []


class PolicyUnreadable(Exception):
    pass


class SlotWaitExpired(Exception):
    pass


class Seen:
    def __init__(self, same, probe, label):
        self.same = same
        self.probe = probe
        self.label = label
        self.released = 0

    def release(self):
        self.released += 1


def read_policy():
    return POLICY


def take(policy, label, environ, out):
    __FAIL__
    seen = Seen(policy is POLICY, environ.get("{PROBE_NAME}"), label)
    TAKES.append(seen)
    return seen


def process_width(policy, holding, environ):
    WIDTHS.append((policy is POLICY, holding is TAKES[-1] if TAKES else holding, environ.get("{PROBE_NAME}")))
    return __WIDTH__
"""

@dataclass(frozen=True)
class Scene:
    """The specimen world -- the home where a copy of the canon is placed, the copy, the root to check, a fake pyright, and the file recording that it was fired."""

    home: Path
    canon: Path
    root: Path
    pyright: Path
    started: Path

    def check_command(self) -> list[str]:
        """The command that checks one file of the root through the same entry a conversation fires directly."""
        return [
            sys.executable,
            "-m",
            "doeff_hy._static_check",
            "--root",
            str(self.root),
            "--no-cache",
            "--pyright",
            str(self.pyright),
            str(self.root / "probe.hy"),
        ]

    def pyright_runs(self) -> list[str]:
        return (
            self.started.read_text(encoding="utf-8").splitlines() if self.started.exists() else []
        )


def _scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, wait_s: float, processes: int = 1) -> Scene:
    """Copy the machine canon into the specimen's home, place a declaration next to it (K = 1, location tmp_path/slots, wait limit wait_s seconds), and make the child process's
    HOME the specimen's home. The cache roots such as Hy bytecode stay those of the current machine (so startup does not pay minutes on a cold cache)."""
    if not CANON.is_file():
        pytest.skip(f"host without the machine canon {CANON} (a machine that does not place dotfiles)")
    home = tmp_path / "home"
    canon = home / CANON_UNDER_HOME
    canon.parent.mkdir(parents=True)
    shutil.copy2(CANON, canon)
    # The declaration places every key the canon's policy_of asks for -- once the canon started asking for processesPerSlot (dotfiles 142108b9f),
    # a declaration without it was refused as unreadable, and the check ran without taking a slot.
    canon.with_name("machine_check_slots.json").write_text(
        json.dumps(
            {
                "slots": 1,
                "processesPerSlot": processes,
                "dir": str(tmp_path / "slots"),
                "waitLimitSeconds": wait_s,
            }
        ),
        encoding="utf-8",
    )
    root = tmp_path / "repo"
    root.mkdir()
    (root / "probe.hy").write_text("(setv answer 1)\n", encoding="utf-8")
    started = tmp_path / "pyright-started.log"
    pyright = tmp_path / "pyright"
    pyright.write_text(
        f"#!/bin/sh\necho started >> {shlex.quote(str(started))}\necho '{{\"generalDiagnostics\": []}}'\n",
        encoding="utf-8",
    )
    pyright.chmod(0o755)
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home()))
    monkeypatch.setenv("HOME", str(home))
    return Scene(home, canon, root, pyright, started)


def _blocker(scene: Scene) -> "subprocess.Popen[str]":
    """Another process that holds the copy's slot through the canon's command wrapper run (holds until stdin is closed)."""
    blocker = subprocess.Popen(
        [
            sys.executable,
            str(scene.canon),
            "run",
            "--label",
            "blocker-holds",
            "--",
            sys.executable,
            "-c",
            "import sys; print('held', flush=True); sys.stdin.read()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    assert blocker.stdout is not None
    assert blocker.stdout.readline() == "held\n", "the other process could not take the copy's slot"
    return blocker


def _end(blocker: "subprocess.Popen[str]") -> None:
    if blocker.stdin is not None and not blocker.stdin.closed:
        blocker.stdin.close()
    if blocker.poll() is None:
        blocker.kill()
    blocker.wait(timeout=15)


def test_a_full_slot_holds_a_directly_started_doeff_hy_check_until_it_frees(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Acceptance c07865153: while the slot (the copy's declaration K = 1) is full, a doeff-hy-check started directly by a conversation waits without firing pyright, names the holder
    # (pid and announcement), and takes the slot on a free beat and checks. Before the fix it checked without looking at the slot and finished without printing a single announcement line.
    scene = _scene(tmp_path, monkeypatch, 60.0)
    blocker = _blocker(scene)
    try:
        check = subprocess.Popen(
            scene.check_command(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        assert check.stderr is not None
        waiting = next((line for line in check.stderr if MARK in line), "")
        runs_while_full = scene.pyright_runs()
        assert blocker.stdin is not None
        blocker.stdin.close()
        _, rest = check.communicate(timeout=120)
        assert f"pid {blocker.pid} blocker-holds" in waiting, (waiting, rest)
        assert "待つのは doeff-hy-check repo" in waiting, waiting
        assert runs_while_full == [], "pyright was fired while the slot was full"
        assert check.returncode == 0, rest
        assert "を取った(待ち" in rest, rest
        assert scene.pyright_runs() == ["started"]
        assert blocker.wait(timeout=15) == 0, "the process that held the slot does not finish"
    finally:
        _end(blocker)


def test_doeff_hy_check_runs_nothing_when_the_slot_wait_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # If the wait exceeds the limit in the canon's declaration, it ends with "could not run" (exit 2) without starting expansion or pyright -- the callers (hy_type_check.hy and
    # hy_type_trend.hy in agora-controllers) read anything other than 0 and 1 as "could not measure". It is neither a pass (0) nor red (1).
    scene = _scene(tmp_path, monkeypatch, 1.0)
    blocker = _blocker(scene)
    try:
        done = subprocess.run(
            scene.check_command(), capture_output=True, text=True, timeout=120, check=False
        )
        assert done.returncode == 2, done.stderr
        assert f"doeff-hy-check: could not run: {MARK}" in done.stderr, done.stderr
        assert scene.pyright_runs() == [], "pyright was fired although the wait limit was exceeded"
    finally:
        _end(blocker)


def test_doeff_hy_check_inside_a_slot_holder_takes_no_second_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Descendants of a process that holds a slot (the doeff-hy-check the gate starts, calls inside pytest) do not take a second one -- even with K = 1 and an ancestor holding the only slot, they
    # check without waiting (waiting would end with exit 2 at the 5-second limit).
    scene = _scene(tmp_path, monkeypatch, 5.0)
    done = subprocess.run(
        [
            sys.executable,
            str(scene.canon),
            "run",
            "--label",
            "outer-holds",
            "--",
            *scene.check_command(),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert MARK not in done.stderr, done.stderr
    assert scene.pyright_runs() == ["started"]


def test_a_host_without_the_canon_takes_no_slot_and_says_nothing(tmp_path: Path) -> None:
    # On a host without the canon (a machine that cloned only doeff), silently pass through -- take no slot, announce nothing.
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert held == SlotHeld(None)
    assert out.getvalue() == ""


def _interface_canon(home: Path, fail: str = "pass", width: str = "5") -> Path:
    target = home / CANON_UNDER_HOME
    target.parent.mkdir(parents=True)
    target.write_text(
        INTERFACE_ONLY_CANON.replace("__FAIL__", fail).replace("__WIDTH__", width), encoding="utf-8"
    )
    return target


def _without_width_mouth(home: Path) -> None:
    """A canon without the process_width interface (the dotfiles tip before the width interface was added)."""
    target = home / CANON_UNDER_HOME
    target.parent.mkdir(parents=True)
    source = INTERFACE_ONLY_CANON.replace("__FAIL__", "pass")
    target.write_text(source[: source.index("def process_width")], encoding="utf-8")


def _canon_of(target: Path) -> ModuleType:
    """A canon with only the interface that take_slot read (the same object in sys.modules -- with the records TAKES and WIDTHS)."""
    return sys.modules[MODULE_KEY_PREFIX + os.path.realpath(target)]


def test_the_declared_policy_and_any_environment_name_reach_the_canon_take(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The policy reaches take as the canon's read_policy answer unchanged (if there were an interface that shrinks or rebuilds values it would be something else), and the canon can read environment
    # variables of names doeff does not know. Counterexample of blind test A: the env passed to the canon was narrowed to a copy of just the holder's one marker, so once the canon started reading the conversation id,
    # only doeff-hy-check ran silently outside the per-conversation counting.
    canon = _interface_canon(tmp_path / "home")
    monkeypatch.setenv(PROBE_NAME, "seen-by-the-canon")
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert isinstance(held, SlotHeld), out.getvalue()
    assert held.let_go is not None, out.getvalue()
    (seen,) = _canon_of(canon).TAKES
    assert vars(seen) == {
        "same": True,
        "probe": "seen-by-the-canon",
        "label": "doeff-hy-check repo",
        "released": 0,
    }
    assert out.getvalue() == ""
    held.release()
    held.release()
    assert seen.released == 2, "SlotHeld.release calls the canon's release interface as is (ignoring the second call is the canon's job)"


def test_the_canon_width_is_asked_with_the_same_opaque_values_and_reaches_the_caller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The number of concurrent processes in one slot is answered by the canon's process_width interface. What is passed to the interface is the policy obtained before take, the answer of take, and the environment itself (the nested rule and
    # the env names are inside the canon -- the counterexample of blind test B was that doeff read fields from the holding and held a different rule). The answer arrives unchanged as SlotHeld.width.
    canon = _interface_canon(tmp_path / "home", width="7")
    monkeypatch.setenv(PROBE_NAME, "seen-by-the-canon")
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert isinstance(held, SlotHeld) and held.width == 7, (held, out.getvalue())
    assert _canon_of(canon).WIDTHS == [(True, True, "seen-by-the-canon")]
    assert out.getvalue() == ""


def test_a_canon_without_the_width_mouth_still_holds_the_slot_and_says_so(tmp_path: Path) -> None:
    # Counterexample of blind test A: the interface set was all-or-nothing, and adding an interface the canon lacks lost the slot itself. The width interface is optional -- even with an old canon the slot is taken and
    # the width does not arrive (None; the caller runs with the default for a machine with no declaration), which is announced in one line.
    _without_width_mouth(tmp_path / "home")
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert isinstance(held, SlotHeld), out.getvalue()
    assert held.let_go is not None and held.width is None, held
    assert out.getvalue().startswith(MARK) and "process_width" in out.getvalue(), out.getvalue()


def test_a_failing_width_mouth_keeps_the_slot_that_was_taken(tmp_path: Path) -> None:
    # If a failure of the width interface were put in the same enclosure as a failure of take, it would announce "run without taking a slot" while still holding the fd of the slot taken (a side danger of blind test A).
    # Even if the width interface fails, the slot taken is kept (the release interface remains) and only the width does not arrive.
    canon = _interface_canon(tmp_path / "home", width="1 / 0")
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert isinstance(held, SlotHeld) and held.let_go is not None and held.width is None, (held, out.getvalue())
    assert "process_width" in out.getvalue(), out.getvalue()
    held.release()
    assert _canon_of(canon).TAKES[0].released == 1


@pytest.mark.parametrize("answer", ["0", "-1", "True", "'2'", "2.0"])
def test_a_width_answer_that_is_not_a_positive_integer_is_named_and_dropped(tmp_path: Path, answer: str) -> None:
    # A check of the interface shape (not a judgment on the number): an answer that is not a positive integer is not used as the width. The slot is kept.
    _interface_canon(tmp_path / "home", width=answer)
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert isinstance(held, SlotHeld) and held.let_go is not None and held.width is None, (held, out.getvalue())
    assert "process_width" in out.getvalue(), out.getvalue()


def test_a_failing_canon_take_lets_the_check_run_and_says_why(tmp_path: Path) -> None:
    # A failure inside the canon's take (such as an OSError for being unable to create the location) is named in one line and passed through -- if it escaped as a traceback it could not be told apart from main's exit 1 (a new red)
    # (side measurement of blind test A: with a location that cannot be created, exit 1 and empty stdout).
    _interface_canon(tmp_path / "home", "raise OSError('cannot create the location')")
    out = io.StringIO()
    held = take_slot(tmp_path / "home", "doeff-hy-check repo", out)
    assert held == SlotHeld()
    assert out.getvalue().startswith(
        MARK
    ), out.getvalue()


def _width_probe(
    scene: Scene,
    env_name: str,
    *,
    hand_down: str | None = None,
    wrapped: bool = True,
) -> dict[str, object]:
    """Receive in JSON the width answered by the slot taker (doeff's take_slot) and the value of the width env the child saw. Fire the child inside the canon's command wrapper
    (wrapped) or directly. hand_down = the number of concurrent processes the outer entry hands to the child before wrapping once more inside the wrapper (a check within the check -- the interface that hands it out is the canon's
    public constant env_name)."""
    show = [
        sys.executable,
        "-c",
        "import json, os, sys; from pathlib import Path; from doeff_hy._check_slot import take_slot;"
        " held = take_slot(Path.home(), 'width-probe', sys.stderr);"
        " print(json.dumps({'env': os.environ.get(sys.argv[1]), 'width': held.width})); held.release()",
        env_name,
    ]
    command = show
    if wrapped:
        command = [sys.executable, str(scene.canon), "run", "--label", "inner", "--", *show]
    if hand_down is not None:
        relay = [
            sys.executable,
            "-c",
            "import os, subprocess, sys; env = dict(os.environ, **{sys.argv[1]: sys.argv[2]});"
            " sys.exit(subprocess.run(sys.argv[3:], env=env).returncode)",
            env_name,
            hand_down,
            *command,
        ]
        command = [sys.executable, str(scene.canon), "run", "--label", "outer", "--", *relay]
    done = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    answer: dict[str, object] = json.loads(done.stdout.strip().splitlines()[-1])
    return answer


def _canon_answer(scene: Scene, env_name: str, seen_env: object) -> int:
    """The answer from pulling the same interface directly in the canon's module (the entry inside the wrapper = holding None, the env value the child saw) -- the expected values are not
    written in doeff's check but asked of the canon's rule seat (even if the rule or env name changes, doeff's check only looks for agreement with the canon's answer)."""
    module = canon_module(scene.home, io.StringIO())
    assert module is not None
    environ = {} if seen_env is None else {env_name: str(seen_env)}
    return int(module.process_width(module.read_policy(), None, environ))


@pytest.mark.parametrize("processes", [1, 2, 3])
def test_the_width_follows_the_declaration_for_a_direct_check_and_for_a_check_inside_the_wrapper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, processes: int
) -> None:
    # The real canon: a direct entry that takes the slot itself gets the declared number (contract: an entry that took a slot gets the declared value). An entry inside the command wrapper (an ancestor holds the slot -- take is None)
    # gets the same number as the canon interface's answer to the value the wrapper handed to the child (the expectation is asked of the canon), and does not exceed the declaration. It follows
    # just by changing the declared number (the code does not change).
    scene = _scene(tmp_path, monkeypatch, 60.0, processes=processes)
    module = canon_module(scene.home, io.StringIO())
    if module is None or not callable(vars(module).get("process_width")):
        pytest.skip("the machine canon has no process_width interface for the number of concurrent processes (a machine that has not yet followed dotfiles)")
    name = module.PROCESSES_ENV
    # The child inherits the check process's environment (do not build os.environ as a value -- DOEFF004). Whether a previous value exists is decided by monkeypatch.
    monkeypatch.delenv(name, raising=False)
    direct = _width_probe(scene, name, wrapped=False)
    assert direct["width"] == processes, direct
    monkeypatch.setenv(name, "1")  # the caller's previous value is a value outside the slot -- an entry that took a slot itself gets the declared number
    stale = _width_probe(scene, name, wrapped=False)
    assert stale["width"] == processes, stale
    monkeypatch.delenv(name, raising=False)
    inside = _width_probe(scene, name)
    assert inside["width"] == _canon_answer(scene, name, inside["env"]), inside
    assert isinstance(inside["width"], int) and inside["width"] <= processes, inside


def test_a_check_inside_the_wrapper_keeps_the_smaller_width_the_outer_entry_handed_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The remaining width (1) the outer entry handed to the child is not returned to the declaration (2) by the inner entry -- concurrency of the whole tree <= declaration (contract: at most the handed value). The number the inner entry
    # receives is the same as the canon interface's answer to the value the child saw, and does not exceed the number handed. The value of the rule itself is held by the canon's check.
    scene = _scene(tmp_path, monkeypatch, 60.0, processes=2)
    module = canon_module(scene.home, io.StringIO())
    if module is None or not callable(vars(module).get("process_width")):
        pytest.skip("the machine canon has no process_width interface for the number of concurrent processes (a machine that has not yet followed dotfiles)")
    name = module.PROCESSES_ENV
    done = _width_probe(scene, name, hand_down="1")
    assert done["env"] is not None, "the number the outer entry handed did not reach the child"
    assert done["width"] == _canon_answer(scene, name, done["env"]), done
    assert isinstance(done["width"], int) and done["width"] <= 1, done
