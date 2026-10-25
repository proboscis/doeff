# The composition scene: update_counter() from docs/02-core-concepts.md ("Core Example") under
# state(writer(reader(...))), and under the reversed order writer(state(...)). Prints which handler each effect
# reached, in order, the answer and the log, and the error of the reversed order.
from doeff_core_effects import Ask, Get, Put, Tell
from doeff_core_effects.handlers import reader, state, writer

from doeff import Pass, WithObserve, do, handler, run


@do
def update_counter():
    counter_key = yield Ask("counter_key")
    current = yield Get(counter_key)
    yield Put(counter_key, current + 1)
    yield Tell(f"counter updated: {current} -> {current + 1}")
    return current + 1


def spy(name):
    """A handler that notes each effect passing it on its way out, and passes it on."""

    @handler
    def noted(effect, k):
        print(f"  {type(effect).__name__} passes {name}")
        yield Pass(effect, k)

    return noted


def seen(effect):
    print(f"program yields {type(effect).__name__}")


program = WithObserve(seen, update_counter())
inner = reader(env={"counter_key": "count"})(program)
# The spies sit just outside reader and just outside writer: what passes them was not answered inside.
print("state(writer(reader(update_counter()))):")
answer = run(state(initial={"count": 0})(spy("writer")(writer(spy("reader")(inner)))))
print("answer:", answer)

print("writer(state(reader(update_counter()))):")
try:
    run(writer(state(initial={"count": 0})(reader(env={"counter_key": "count"})(update_counter()))))
except Exception as error:  # noqa: BLE001 - the scene shows the error the reversed order raises
    print(f"raises {type(error).__name__}:")
    print(str(error).splitlines()[0])
