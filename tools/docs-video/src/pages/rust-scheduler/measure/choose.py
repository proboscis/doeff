# How scheduled() chooses its implementation: the argument, then $DOEFF_SCHEDULER, then DEFAULT_IMPLEMENTATION.
# $DOEFF_SCHEDULER is set for a whole process, so each case asks a child process started with `env` that sets it.
import subprocess
import sys

from doeff_core_effects import _scheduler as scheduler

# The child prints what resolve_implementation answers for the implementation in its arguments (none = no argument).
ASK = """
import sys
from doeff_core_effects._scheduler import resolve_implementation
try:
    print(resolve_implementation(*sys.argv[1:]))
except ValueError as exc:
    print(type(exc).__name__ + ":", exc)
"""


def chosen(*settings: str, implementation: str | None = None) -> str:
    """What a child process with only ``settings`` (NAME=VALUE) for DOEFF_SCHEDULER resolves."""
    argument = [] if implementation is None else [implementation]
    command = ["env", "-u", "DOEFF_SCHEDULER", *settings, sys.executable, "-c", ASK, *argument]
    return subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip()


print("DEFAULT_IMPLEMENTATION =", repr(scheduler.DEFAULT_IMPLEMENTATION))
print("scheduled(main())                          ->", chosen())
print('scheduled(main(), implementation="python") ->', chosen(implementation="python"))
print("DOEFF_SCHEDULER=python  scheduled(main())  ->", chosen("DOEFF_SCHEDULER=python"))
print('DOEFF_SCHEDULER=python  implementation="rust" ->', chosen("DOEFF_SCHEDULER=python", implementation="rust"))
print("DOEFF_SCHEDULER=go ->", chosen("DOEFF_SCHEDULER=go"))
