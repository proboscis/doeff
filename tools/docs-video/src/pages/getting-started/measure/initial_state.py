# The page's program run with initial state: reader(env=...) answers Ask and state(initial=...) answers Get.
# Runs the same program with two initial states and prints each result. Backs the initial-state scene.
from doeff_core_effects import Ask, Get
from doeff_core_effects.handlers import reader, state

from doeff import do, run


@do
def my_program():
    db_url = yield Ask("database_url")
    user_id = yield Get("user_id")
    return f"Connected to {db_url} as user {user_id}"


for user_id in (123, 7):
    prog = my_program()
    prog = state(initial={"user_id": user_id})(prog)
    prog = reader(env={"database_url": "postgresql://localhost/mydb"})(prog)
    print(f"user_id={user_id}:", run(prog))
