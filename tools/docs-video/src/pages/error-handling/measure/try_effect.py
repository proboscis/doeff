# Try is an effect: try_handler runs the sub-program and resumes the caller with Ok(value) or Err(exception).
# Runs the page's parse_count example on a good and a bad input, printing the Try result, the log and the return.
from doeff import Err, Ok, Tell, Try, do, run
from doeff_core_effects import ReadWriterLog
from doeff_core_effects import state, try_handler, writer


@do
def parse_count(raw: str):
    return int(raw)


SEEN = []


@do
def app(raw: str):
    parsed = yield Try(parse_count(raw))
    SEEN.append(parsed)
    match parsed:
        case Ok(value=count):
            yield Tell(f"parsed count={count}")
            return count
        case Err(error=exc):
            yield Tell(f"parse failed: {exc}")
            return 0


@do
def with_log(raw: str):
    value = yield app(raw)
    return value, (yield ReadWriterLog())


for raw in ["42", "not-a-number"]:
    SEEN.clear()
    value, log = run(state()(writer(try_handler(with_log(raw)))))
    print(f"app({raw!r}): Try -> {SEEN[0]!r} log {log} return {value!r}")

try:
    run(app("42"))
except Exception as error:
    print("without try_handler:", type(error).__name__)
