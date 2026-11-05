"""Failure cases of type checking for a worker that uses the event-loop macro with a state type written (#3080).

The macro expansion used to bundle clause values with an annotation of "the state type or the exit marker LoopStop". The caller's pyright strict reported red the author cannot fix
(Expected type arguments for generic class "LoopStop"; the expansion variable and the state type are Unknown) (named by the commit hook when the automation worker's model was
written with the macro). Even adding the type arguments, the annotated type flows back into inference of the Program's answer type and Unknown remains.
-> The expansion bundles clause values without annotation and checks them at runtime with assert (statically they are read as the state type where they are written back to the state name).
If the fix is removed, the check here (reading a worker with a state type under strict) goes red.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

needs_pyright = pytest.mark.skipif(shutil.which("pyright") is None, reason="pyright is not available")

MODULE = """\
(require doeff-hy.macros [defk])
(require doeff-events.macros [event-loop])
(import doeff_events [TimerFired])

(defk counted []
  {:pre [] :post [(: % int)] :tags {:context "probe" :role "program"}}
  "A worker that counts deadline events and exits on the final deadline (the form with state type int written)."
  (event-loop [count int 0]
    (:stop _reason)       count
    (TimerFired :tag tag) (if (= tag "end")
                              (stop count)
                              (+ count 1))))
"""

# The shape of the durable keeper (the shape c3-w48 hit in #3102): state is a record, clause bodies are (do (<- ...) ...), the stop clause body is do.
RECORD_MODULE = """\
(require doeff-hy.macros [defk <-])
(require doeff-hy.record [defrecord])
(require doeff-events.macros [event-loop])
(import dataclasses [dataclass])
(import doeff_core_effects._effects [slog])
(import doeff_events [TimerFired])

(defrecord Lap
  "Lap state (number of laps run)."
  (#^ int passes))

(defk stepped [lap]
  {:pre [(: lap Lap)] :post [(: % Lap)] :tags {:context "probe" :role "program"}}
  "Run one lap and return the next state."
  (Lap :passes (+ lap.passes 1)))

(defk keeper []
  {:pre [] :post [(: % Lap)] :tags {:context "probe" :role "program"}}
  "A worker that runs one lap per deadline and exits after 10 laps or on the final deadline (record state, do bodies, do stop clause)."
  (event-loop [lap Lap (Lap :passes 0)]
    (:stop reason)        (do (<- (slog "stop" :reason reason :passes lap.passes))
                              lap)
    (TimerFired :tag tag) (if (= tag "end")
                              (stop lap)
                              (do (<- timed Lap (stepped lap))
                                  (if (> timed.passes 9) (stop timed) timed)))))
"""


def _errors(root: Path) -> list[tuple[str, int, str]]:
    # The type-checking tool is run with `python -m doeff_hy._static_check`, the same as the hook.
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "doeff_hy._static_check",
            "--root",
            str(root),
            "--json",
            "--strict",
            "--no-cache",
            str(root / "probe.hy"),
        ],
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    text = done.stdout
    diagnostics = json.loads(text) if text.strip() else []
    return [
        (str(d["rule"]), int(str(d["line"])), str(d["message"]))
        for d in diagnostics
        if d["severity"] == "error"
    ]


@needs_pyright
@pytest.mark.parametrize("source", [MODULE, RECORD_MODULE], ids=["int-state", "record-state-do-bodies"])
def test_a_loop_with_a_typed_state_gets_no_type_errors_from_the_expansion(tmp_path: Path, source: str) -> None:
    (tmp_path / "probe.hy").write_text(source, encoding="utf-8")
    errors = _errors(tmp_path)
    assert not [e for e in errors if e[0] == "hy-compile"], errors
    assert errors == [], errors
