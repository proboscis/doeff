"""A small script that plays the starter side in the StartProcess test of "when the started process ends, its child ends too".

Starts one child with StartProcess using the real handler (subprocess-handler) (process-group True -- a new session), and writes the child's pid on one line to out-path.
  * mode = hold: after writing, keeps waiting without ending -- the test SIGKILLs it from outside (the starter dies without running cleanup)
  * mode = exit: after writing, ends normally without calling StopProcess
  * mode = outlive: starts the child to "outlive the starter" (ChildLifetime.OUTLIVES-STARTER) and, after writing, ends normally
The child's command is `sleep 600` if child-out is "-"; otherwise it starts this script itself with mode hold and out-path = child-out (the child in turn starts a grandchild in yet another
session).
Usage: python lifeline_starter.py <mode> <out-path> <child-out>
"""

from __future__ import annotations

import signal
import sys

import hy  # noqa: F401  - to load the Hy modules of doeff_core_effects
from doeff_core_effects import (
    ChildLifetime,
    ProcessStarted,
    StartProcess,
    scheduled,
    subprocess_handler,
)

from doeff import run, with_handlers


def child_argv(child_out: str) -> tuple[str, ...]:
    """The command of the child to start -- a child that only sleeps if child-out is "-", otherwise a child that starts a grandchild (this script's hold)."""
    if child_out == "-":
        return ("sleep", "600")
    return (sys.executable, __file__, "hold", child_out, "-")


def start_of(mode: str, child_out: str) -> StartProcess:
    """The start request -- default (no lifetime written) = ends together with the starter. Only outlive writes OUTLIVES-STARTER."""
    match mode:
        case "outlive":
            return StartProcess(
                argv=child_argv(child_out),
                process_group=True,
                lifetime=ChildLifetime.OUTLIVES_STARTER,
            )
        case "hold" | "exit":
            return StartProcess(argv=child_argv(child_out), process_group=True)
        case _:
            raise SystemExit(f"unknown mode: {mode}")


def main(mode: str, out_path: str, child_out: str) -> None:
    started = run(scheduled(with_handlers([subprocess_handler], start_of(mode, child_out))))
    if not isinstance(started, ProcessStarted):
        raise SystemExit(f"could not start the child: {started}")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"{started.pid}\n")
    if mode == "hold":
        signal.pause()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
