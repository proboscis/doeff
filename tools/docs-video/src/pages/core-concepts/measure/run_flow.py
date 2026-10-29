# The run scene: fetch_user(42) from docs/02-core-concepts.md ("@do and Macro Expansion").
# Calling a @do function runs nothing: it returns an Expand node. run() evaluates it. Prints the node, what the
# generator yields and is sent back, and the answers.
from doeff_core_effects import Ask, reader

from doeff import Expand, Perform, do, run


@do
def fetch_user(user_id: int):
    print("fetch_user starts")
    db = yield Ask("db")
    print(f"sent back: {db!r}")
    return db[user_id]


program = fetch_user(42)
print("fetch_user(42) is", type(program).__name__, "| isinstance Expand:", isinstance(program, Expand))
print("(nothing has run yet)")
print("Perform(Ask('db')) is", type(Perform(Ask("db"))).__name__)
answer = run(reader(env={"db": {42: "Ada"}})(program))
print("answer:", repr(answer))
