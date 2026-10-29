# The expansion scene: calling a @do function is pure expansion at Python call time, and no handler is consulted
# (docs/11-kleisli-arrows.md, "Call-Time Macro Expansion" and "Why Expand Is a Macro"). greeting() yields Ask, yet
# calling it with no handler anywhere succeeds; only running it needs a handler for Ask.
from doeff_core_effects import reader

from doeff import Ask, do, run


@do
def greeting(greet: str):
    print("  greeting's body runs")
    name = yield Ask("name")
    return f"{greet}, {name}"


print('program = greeting("Hello")   # no handler is installed anywhere')
program = greeting("Hello")
print("  built:", type(program).__name__, program.args)
print("run(program):")
try:
    run(program)
except Exception as error:  # noqa: BLE001 - the scene shows the error a run without a handler raises
    print(f"  raises {type(error).__name__}: {str(error).splitlines()[0]}")
print('run(reader(env={"name": "Ada"})(program)):')
print("  ->", repr(run(reader(env={"name": "Ada"})(program))))
