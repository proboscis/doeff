"""The orders and counts that the figures of docs/articles/yield-and-async.md show, measured with doeff: the requests that drive
receives in await-passes-through, the handlers a ReadName reaches in yield-nearest-handler, and the Awaits that Listen hears in
await-per-step. The programs are the article's (examples/yield_and_async), with a print at each event.

Usage (from the repository root): uv run python tools/docs_site/figures_measure/yield_and_async.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "examples" / "yield_and_async"))

from drive import twice_async
from greet import ReadName, greet
from stream import bridged, collect, collect_in_one, counted, tokens

from doeff import Resume, do, handler, run


def drive_printing(coroutine, answers: dict[str, str]):
    """drive from drive.py, with a print for each request it receives."""
    value = None
    while True:
        try:
            request = coroutine.send(value)
        except StopIteration as stop:
            print(f"drive: StopIteration, returns {stop.value!r}")
            return stop.value
        print(f"drive: receives {request!r}, answers {answers[request.key]!r}")
        value = answers[request.key]


def answering(name: str, answer: str):
    """A handler like ada and grace in greet.py, with a print when it receives ReadName and when its Resume returns."""

    @handler
    @do(non_tail=True)  # it prints after its Resume returns
    def on_read_name(effect: ReadName, k):
        print(f"{name}: receives {effect!r}, resumes k with {answer!r}")
        out = yield Resume(k, answer)
        print(f"{name}: its Resume returns {out!r}")
        return out

    return on_read_name


ada = answering("ada", "Ada")
grace = answering("grace", "Grace")


@handler
@do(non_tail=True)  # it prints after its Resume returns
def titled(effect: ReadName, k):
    """titled from greet.py, with a print at each event."""
    print(f"titled: receives {effect!r}, performs it again")
    name = yield effect
    print(f"titled: gets {name!r}, resumes k with {'Dr. ' + name!r}")
    out = yield Resume(k, f"Dr. {name}")
    print(f"titled: its Resume returns {out!r}")
    return out


@do
def twice():
    """twice from greet.py, with this file's grace."""
    first = yield greet()
    print(f"twice: first = {first!r}")
    second = yield grace(greet())
    print(f"twice: second = {second!r}")
    return first, second


if __name__ == "__main__":
    print("-- await-passes-through: drive(twice_async(), {'name': 'Ada'})")
    print(repr(drive_printing(twice_async(), {"name": "Ada"})))
    try:
        asyncio.run(twice_async())
    except RuntimeError as error:
        print(f"asyncio.run(twice_async()): RuntimeError: {error}")

    print("-- yield-nearest-handler: run(ada(titled(twice())))")
    print(repr(run(ada(titled(twice())))))
    print("-- yield-nearest-handler: run(ada(twice()))")
    print(repr(run(ada(twice()))))

    print("-- await-per-step: Listen around collect and collect_in_one")
    for program in (collect, collect_in_one):
        text, waits = run(bridged(counted(program(tokens()))))
        print(f"{program.__name__}: {text!r}, Listen heard {len(waits)} Await")
