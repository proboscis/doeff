# How scheduled() chooses its implementation: the argument, then $DOEFF_SCHEDULER, then DEFAULT_IMPLEMENTATION.
import os

from doeff_core_effects import scheduler
from doeff_core_effects.scheduler import resolve_implementation

os.environ.pop("DOEFF_SCHEDULER", None)
print("DEFAULT_IMPLEMENTATION =", repr(scheduler.DEFAULT_IMPLEMENTATION))
print("scheduled(main())                          ->", resolve_implementation())
print('scheduled(main(), implementation="python") ->', resolve_implementation("python"))
os.environ["DOEFF_SCHEDULER"] = "python"
print("DOEFF_SCHEDULER=python  scheduled(main())  ->", resolve_implementation())
print('DOEFF_SCHEDULER=python  implementation="rust" ->', resolve_implementation("rust"))
os.environ["DOEFF_SCHEDULER"] = "go"
try:
    resolve_implementation()
except ValueError as exc:
    print("DOEFF_SCHEDULER=go ->", type(exc).__name__ + ":", exc)
