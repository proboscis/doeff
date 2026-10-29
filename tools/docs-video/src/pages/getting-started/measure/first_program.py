# The page's first program, counter_program, run on state() and writer: prints every effect it yields, in order,
# with the answer that comes back to it, then its result and the writer's log. Backs the first-program scene.
from doeff_core_effects import Get, Put, ReadWriterLog, Tell, state, writer

from doeff import EffectBase, Resume, do, handler, run


@do
def counter_program():
    yield Put("counter", 0)
    yield Tell("Counter initialized")

    count = yield Get("counter")
    yield Tell(f"Current count: {count}")

    yield Put("counter", count + 1)
    final_count = yield Get("counter")

    yield Tell(f"Final count: {final_count}")
    return final_count


TRACE = []


@handler
def traced(effect: EffectBase, k):  # notes the effect, asks the handlers outside for its answer, and resumes k
    answer = yield effect
    TRACE.append(f"{effect!r} -> {answer!r}")
    return (yield Resume(k, answer))


@do
def with_log():
    result = yield traced(counter_program())
    return result, (yield ReadWriterLog())


result, log = run(state()(writer(with_log())))
for line in TRACE:
    print(line)
print("result:", result)
print("log:", log)
