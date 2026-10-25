# Writer: Tell appends to the log, writer_log() reads it. The writer handler keeps its log in state: to handle a
# Tell it performs Get (and, the first time, Put) itself, which travel out to state. Backs the Writer scene.
from doeff_core_effects import Get, Put, Tell
from doeff_core_effects.handlers import state, writer, writer_log

from doeff import Pass, do, handler, run

SEEN = []


@do
def with_logging():
    yield Tell("Starting operation")
    yield Tell("Processing data")
    count = yield Get("count")
    yield Tell(f"Count: {count}")
    yield Tell("Operation complete")
    return "done"


@do
def main_program():
    result = yield with_logging()
    return result, (yield writer_log())


@handler
def watch(effect: Get | Put, k):  # between writer and state: records what reaches state, passes it on
    SEEN.append(type(effect).__name__ + ("(count)" if effect.key == "count" else "(log)"))
    yield Pass(effect, k)


print(run(state(initial={"count": 0})(watch(writer(main_program())))))
print("reached state:", SEEN)
