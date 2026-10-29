# The discovery scenes: what doeff run's discovery (doeff/cli/discovery.py) sees for login_program. Prints the
# module hierarchy, every marked interpreter with its place in the hierarchy and the one chosen, the marked envs in
# merge order with what each holds, and the merged env. Run from project/ (run.sh does).
# The index is read through doeff-indexer's effect FindSymbols, answered by its handler rust_indexer (the Rust
# indexer), as doeff run's discovery program reads it.
from doeff_indexer import FindSymbols, rust_indexer

from doeff import run
from doeff.cli.discovery import StandardEnvMerger, discover, module_hierarchy
from doeff.cli.run_services import import_symbol

PROGRAM = "myapp.features.auth.login.login_program"
module = PROGRAM.rsplit(".", 1)[0]
hierarchy = module_hierarchy(module)
print("program:", PROGRAM)
print("hierarchy:", hierarchy)

marked = run(rust_indexer(FindSymbols(module, ("interpreter", "default"), "function")))
print("marked interpreters (# doeff: interpreter, default):")
for symbol in sorted(marked, key=lambda s: s.full_path):
    place = hierarchy.index(symbol.module_path) if symbol.module_path in hierarchy else None
    print(f"  {symbol.full_path}  index {place}")
found = run(rust_indexer(discover(PROGRAM)))
print("chosen:", found.interpreter)

envs = list(found.envs)
print("marked envs (# doeff: default), in merge order:")
for path in envs:
    print(f"  {path}  {run(import_symbol(path))}")
print("merged:", run(StandardEnvMerger().merge_envs(envs)))

# What doeff run then does with them: call the chosen interpreter with the program and the merged env, here with each
# effect the program yields noted on its way out.
from doeff import WithObserve  # noqa: E402

from myapp.features.auth import auth_interpreter  # noqa: E402
from myapp.features.auth.login import login  # noqa: E402

print("auth_interpreter(login(), env=merged):")
answer = auth_interpreter(WithObserve(lambda effect: print("  login() yields", effect), login()), env=StandardEnvMerger().merge_envs(envs))
print("  returns", repr(answer))
