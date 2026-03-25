
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TIME_PACKAGE_ROOT = ROOT / "packages" / "doeff-time" / "src"
EVENTS_PACKAGE_ROOT = ROOT / "packages" / "doeff-events" / "src"

for package_root in (TIME_PACKAGE_ROOT, EVENTS_PACKAGE_ROOT):
    if str(package_root) not in sys.path:
        sys.path.insert(0, str(package_root))

SIM_TIME_EPOCH = datetime(2024, 1, 1, tzinfo=timezone.utc)


def sim_time(seconds: float) -> datetime:
    return SIM_TIME_EPOCH + timedelta(seconds=seconds)


def sim_seconds(value: datetime) -> float:
    return (value - SIM_TIME_EPOCH).total_seconds()


def listen(program, types=None):
    """Local listen — collects effects while running program in place.

    OCaml 5 pattern: local match_with, not an effect.
    Returns DoExpr that evaluates to (result, collected_effects).
    """
    from doeff import WithHandler, Pass, Resume, do
    from doeff_core_effects import WriterTellEffect

    types_to_collect = types or (WriterTellEffect,)
    collected = []

    @do
    def collector(effect, k):
        if isinstance(effect, tuple(types_to_collect)):
            collected.append(effect)
        yield Pass(effect, k)

    @do
    def _listen():
        result = yield WithHandler(collector, program)
        return (result, collected)

    return _listen()


def run_with_handlers(program, *, env=None):
    from doeff import WithHandler, run
    from doeff_core_effects.handlers import writer, listen_handler, try_handler, slog_handler, reader, await_handler
    from doeff_core_effects.scheduler import scheduled

    wrapped = program
    if env is not None:
        wrapped = WithHandler(reader(env=env), wrapped)
    # await_handler uses scheduler effects (CreateExternalPromise, Wait),
    # so it must be inside the scheduler.
    wrapped = WithHandler(await_handler(), wrapped)
    wrapped = scheduled(wrapped)
    wrapped = WithHandler(slog_handler(), wrapped)
    wrapped = WithHandler(try_handler(), wrapped)
    wrapped = WithHandler(listen_handler(), wrapped)
    wrapped = WithHandler(writer(), wrapped)
    return run(wrapped)
