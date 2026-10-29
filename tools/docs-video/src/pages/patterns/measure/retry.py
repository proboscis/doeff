# The retry scene: the page's retry_with_exponential_backoff (docs/12-patterns.md, "Retry with Backoff") around an
# operation that fails twice and then answers. The same program runs under two time handlers: sim_time_handler moves a
# virtual clock, sync_time_handler waits on the wall clock. Prints when each attempt happens on the handler's clock,
# the log, the answer, and how long each run took on the wall clock.
import time
from datetime import datetime, timezone

from doeff_core_effects import ReadWriterLog, scheduled, state, try_handler, writer
from doeff_time import Delay, GetTime, sim_time_handler, sync_time_handler

from doeff import Tell, Try, do, run

START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)


class FlakyApi:
    """An operation that fails its first `failures` calls, to drive the retry path."""

    def __init__(self, failures):
        self.left = failures
        self.first = None

    @do
    def fetch(self):
        now = yield GetTime()
        self.first = self.first or now
        at = (now - self.first).total_seconds()
        if self.left > 0:
            self.left -= 1
            print(f"  t={at:.0f}s attempt fails: ConnectionError")
            raise ConnectionError("unreachable")
        print(f"  t={at:.0f}s attempt answers")
        return f"data after {at:.0f}s"


@do
def retry_with_exponential_backoff(operation, max_attempts=5):
    for attempt in range(1, max_attempts + 1):
        safe_result = yield Try(operation())

        if safe_result.is_ok():
            return safe_result.value

        if attempt < max_attempts:
            delay = 2**attempt  # Exponential backoff
            yield Tell(f"Retry {attempt} failed, waiting {delay}s")
            yield Delay(delay)
        else:
            raise safe_result.error


@do
def with_log(program):
    """The program's answer together with what it logged, to print both."""
    value = yield program
    return value, (yield ReadWriterLog())


for name, clock in [("sim_time_handler", sim_time_handler(start_time=START)), ("sync_time_handler", sync_time_handler())]:
    api = FlakyApi(failures=2)
    print(f"{name}:")
    started = time.perf_counter()
    value, log = run(scheduled(clock(state()(writer(try_handler(with_log(retry_with_exponential_backoff(api.fetch))))))))
    seconds = time.perf_counter() - started
    print("  log:", log)
    print("  returns:", value)
    print(f"  wall clock: {seconds * 1000:.2f} ms" if seconds < 1 else f"  wall clock: {seconds:.2f} s")
