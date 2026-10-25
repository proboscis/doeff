# The title scene: what @do returns (docs/11-kleisli-arrows.md, "What @do Returns"). Calling add_one(41) builds a
# Call node, an Expand, and runs nothing; run() evaluates it, and only then does the body run. Prints the node, its
# arguments, that it has no map or flat_map, and a line whenever the body runs.
from doeff import Expand, do, run


@do
def add_one(x: int):
    print("  add_one's body runs")
    return x + 1


print("call_expr = add_one(41)")
call_expr = add_one(41)
print("type:", type(call_expr).__name__)
print("isinstance(call_expr, Expand):", isinstance(call_expr, Expand))
print("call_expr.args:", call_expr.args)
print("has .map / .flat_map:", hasattr(call_expr, "map"), hasattr(call_expr, "flat_map"))
print("run(call_expr):")
print("  ->", run(call_expr))
print("run(call_expr) again:")
print("  ->", run(call_expr))
