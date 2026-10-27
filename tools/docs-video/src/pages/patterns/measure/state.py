# The state scene: the page's State Machine and Snapshot and Restore (docs/12-patterns.md, "State Management
# Patterns"). The order's state lives in the store the state handler keeps, not in the program; each run of
# order_state_machine moves it one step. Then a failing operation under Try, without and with the snapshot:
# Try does not roll the store back, the snapshot pattern does.
from doeff_core_effects import ReadWriterLog
from doeff_core_effects.handlers import state, try_handler, writer

from doeff import Get, Put, Tell, Try, do, run


@do
def step_done(name, order_id):
    """Stands for the payment, shipping and completion work of one transition."""
    return f"{name} {order_id}"


@do
def order_state_machine(order_id):
    state_key = f"order_{order_id}_state"
    current = yield Get(state_key)

    if current == "pending":
        yield step_done("process_payment", order_id)
        yield Put(state_key, "paid")
        yield Tell(f"Order {order_id}: pending -> paid")

    elif current == "paid":
        yield step_done("ship_order", order_id)
        yield Put(state_key, "shipped")
        yield Tell(f"Order {order_id}: paid -> shipped")

    elif current == "shipped":
        yield step_done("complete_order", order_id)
        yield Put(state_key, "completed")
        yield Tell(f"Order {order_id}: shipped -> completed")

    else:
        raise ValueError(f"Invalid state: {current}")

    return (yield Get(state_key))


@do
def three_steps_then_one_more(order_id):
    """Runs the machine three times, then once more past the last state, keeping the fourth failure as a value."""
    first = yield order_state_machine(order_id)
    second = yield order_state_machine(order_id)
    third = yield order_state_machine(order_id)
    fourth = yield Try(order_state_machine(order_id))
    return (first, second, third, fourth), (yield ReadWriterLog())


answers, log = run(state({"order_7_state": "pending"})(writer(try_handler(three_steps_then_one_more(7)))))
print("order_state_machine(7), four times from 'pending':")
print("  returns:", answers)
print("  log:", log)


@do
def with_state_snapshot(operation):
    # Snapshot state
    current = yield Get("_state")
    snapshot = current.copy() if current else {}

    safe_result = yield Try(operation())

    if safe_result.is_err():
        # Restore state and raise
        yield Put("_state", snapshot)
        yield Tell("State restored from snapshot")
        raise safe_result.error

    return safe_result.value


@do
def drain_then_fail():
    """An operation that changes the store and then fails."""
    yield Put("_state", {"balance": 0})
    raise RuntimeError("card declined")


@do
def attempt(wrapped):
    """Tries the failing operation, bare or inside the snapshot, and reads the store afterwards."""
    result = yield Try(with_state_snapshot(drain_then_fail) if wrapped else drain_then_fail())
    return result, (yield Get("_state"))


for wrapped in [False, True]:
    result, after = run(state({"_state": {"balance": 100}})(writer(try_handler(attempt(wrapped)))))
    print(f"{'with_state_snapshot' if wrapped else 'Try alone'}: {result!r}, _state after = {after}")
