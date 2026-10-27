# Reader, State and Writer in one program: retries with max_retries from the env, the attempt in state, and
# each step told to the log. Backs the combining scene: which handler each effect of the program reaches, in
# order, the log and the result.
from doeff_core_effects import Ask, Get, Put, ReadWriterLog, Tell, WriterTellEffect
from doeff_core_effects.handlers import reader, state, writer

from doeff import Pass, do, handler, run

TRIPS = []


@do
def try_operation():
    attempt = yield Get("attempt")
    return attempt >= 2  # succeeds on the second attempt


@do
def application_workflow():
    max_retries = yield Ask("max_retries")
    yield Tell(f"Config: max_retries = {max_retries}")
    yield Put("attempt", 0)
    for _ in range(max_retries):
        attempt = yield Get("attempt")
        yield Put("attempt", attempt + 1)
        yield Tell(f"Attempt {attempt + 1}/{max_retries}")
        if (yield try_operation()):
            yield Tell("Operation succeeded")
            return "success"
        yield Tell(f"Attempt {attempt + 1} failed")
    yield Tell("All attempts failed")
    return "failed"


@do
def with_log():
    result = yield application_workflow()
    return result, (yield ReadWriterLog())


@handler
def watch(effect: Ask | Get | Put | WriterTellEffect, k):  # right around the program: records each effect it yields
    TRIPS.append("Tell" if isinstance(effect, WriterTellEffect) else type(effect).__name__)
    yield Pass(effect, k)


def stack(prog):
    return reader(env={"max_retries": 3})(state()(writer(prog)))


result, log = run(stack(with_log()))
print("result:", result)
for line in log:
    print("log:", line)
run(stack(watch(application_workflow())))
print("effects the program yields, in order:", TRIPS)
