# The testing scene (docs/12-patterns.md, "Testing Patterns"): a test of a doeff program is the same program under a
# handler stack the test chooses. Three tests, run here as plain functions: the page's test_effects_executed; the
# same program with the test also reading the log and the store its handlers kept; and the retry of retry.py on a
# virtual clock. Prints each test's name, what it saw, and how long it took on the wall clock.
import time
from datetime import datetime, timezone

from doeff_core_effects import ReadWriterLog
from doeff_core_effects.handlers import state, try_handler, writer
from doeff_core_effects.scheduler import scheduled
from doeff_time import Delay, GetTime, sim_time_handler

from doeff import Get, Put, Tell, Try, do, run


@do
def program():
    yield Put("key", "value")
    yield Tell("Logged message")
    return "result"


def test_effects_executed():
    """The page's test: run() hands back the program's value under state and writer."""
    result = run(state()(writer(program())))
    assert result == "result"
    return f"run(...) == {result!r}"


@do
def program_and_what_its_handlers_kept():
    """Runs program() and then reads the log writer kept and the value the state handler holds."""
    value = yield program()
    return value, (yield ReadWriterLog()), (yield Get("key"))


def test_effects_seen():
    """The test reads what the effects did from the handlers it installed: nothing is patched."""
    value, log, key = run(state()(writer(program_and_what_its_handlers_kept())))
    assert (value, log, key) == ("result", ["Logged message"], "value")
    return f"value {value!r}, log {log}, store key = {key!r}"


@do
def retry_with_exponential_backoff(operation, max_attempts=5):
    for attempt in range(1, max_attempts + 1):
        safe_result = yield Try(operation())
        if safe_result.is_ok():
            return safe_result.value
        if attempt < max_attempts:
            yield Tell(f"Retry {attempt} failed, waiting {2**attempt}s")
            yield Delay(2**attempt)
        else:
            raise safe_result.error


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


def test_retry_on_a_virtual_clock():
    """The retry path waits 2 s and 4 s on the virtual clock sim_time_handler keeps; the test never sleeps."""
    calls = iter([ConnectionError("unreachable"), ConnectionError("unreachable"), "data"])

    @do
    def flaky():
        answer = next(calls)
        if isinstance(answer, Exception):
            raise answer
        return answer

    @do
    def retry_then_clock():
        value = yield retry_with_exponential_backoff(flaky)
        return value, (yield GetTime())

    value, now = run(scheduled(sim_time_handler(start_time=START)(state()(writer(try_handler(retry_then_clock()))))))
    assert (value, (now - START).total_seconds()) == ("data", 6.0)
    return f"value {value!r}, virtual clock moved {(now - START).total_seconds():.0f} s"


for test in [test_effects_executed, test_effects_seen, test_retry_on_a_virtual_clock]:
    started = time.perf_counter()
    seen = test()
    print(f"{test.__name__} PASSED in {(time.perf_counter() - started) * 1000:.2f} ms: {seen}")
