# agent.py — a candidate example: one small "research agent" program, run under seven stacks of handlers.
import asyncio
import heapq
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import count

from doeff import Await, EffectBase, Err, Gather, Ok, Pass, Resume, Spawn, Transfer, Try, do, handler, run
from doeff_core_effects.handlers import await_handler, try_handler
from doeff_core_effects.scheduler import PRIORITY_IDLE, CompletePromise, CreatePromise, Wait, scheduled


# ---- The program: an agent that plans, searches in parallel, drafts, and asks a person.
@dataclass(frozen=True)
class Complete(EffectBase):  # "ask the language model"
    prompt: str


@dataclass(frozen=True)
class Search(EffectBase):  # "call the search tool"
    query: str


@dataclass(frozen=True)
class Approve(EffectBase):  # "show this to a person; yes or no?"
    text: str


@dataclass(frozen=True)
class Spend(EffectBase):  # "the next step costs this many tokens"
    tokens: int


@dataclass(frozen=True)
class Delay(EffectBase):  # "wait this many seconds"
    seconds: float


@dataclass(frozen=True)
class GetTime(EffectBase):  # "what time is it?"
    pass


@dataclass(frozen=True)
class Log(EffectBase):  # "write this line"
    line: str


@do
def search(query: str):
    return (yield Search(query))


@do
def lookup(step: str):
    yield Spend(1)
    found = yield Try(search(step))  # a failing tool becomes a value, not a crash
    now = yield GetTime()
    text = found.value if isinstance(found, Ok) else f"no result ({found.error})"
    yield Log(f"{now:%H:%M:%S} {step}: {text}")
    return text


@do
def research(question: str):
    yield Spend(5)
    steps = yield Complete(f"Plan: {question}")  # the model plans
    tasks = []
    for step in steps:
        tasks.append((yield Spawn(lookup(step))))  # one task per step, in parallel
    facts = yield Gather(*tasks)
    yield Spend(10)
    draft = yield Complete(f"Answer {question!r} from {facts}")
    while not (yield Approve(draft)):  # a person says yes or no
        draft = yield Complete(f"Revise: {draft}")
    return draft


# ---- Layers. Each gives one effect its meaning; none of them knows about the others.
PLAN = ["tides", "moons"]


def fake_model(calls: list[str]):  # a stand-in for the model; counts its calls
    @handler
    def model(effect: Complete, k):
        calls.append(effect.prompt)
        if effect.prompt.startswith("Plan:"):
            answer = PLAN
        elif effect.prompt.startswith("Revise:"):
            answer = effect.prompt.removeprefix("Revise: ") + " (revised)"
        else:
            answer = f"answer #{len(calls)}"
        return (yield Resume(k, answer))

    return model


@handler
def fake_search(effect: Search, k):  # the tool, answered from a dict
    return (yield Resume(k, {"tides": "twice a day", "moons": "one"}[effect.query]))


def approvals(decisions: list[bool]):  # the person, scripted
    @handler
    def approve(effect: Approve, k):
        return (yield Resume(k, decisions.pop(0)))

    return approve


@handler
def printed(effect: Log, k):
    print("   ", effect.line)
    return (yield Resume(k, None))


@handler
def free(effect: Spend, k):  # tokens cost nothing
    yield Transfer(k, None)


def budget(limit: int):  # the run stops where the budget runs out
    left = limit

    @handler
    def on_spend(effect: Spend, k):
        nonlocal left
        if effect.tokens > left:
            return f"over budget after {limit - left} tokens"
        left -= effect.tokens
        return (yield Resume(k, None))

    return on_spend


class RateLimited(Exception):
    pass


def flaky_search(failures: int):  # the first few tool calls fail with 429
    left = failures

    @handler
    def on_search(effect: Search, k):
        nonlocal left
        if left > 0:
            left -= 1
            raise RateLimited(f"429 for {effect.query!r}")
        return (yield Pass(effect, k))

    return on_search


def retrying(times: int):  # wait 1 s, 2 s, 4 s … on the clock, then ask again
    @handler
    def retry(effect: Search, k):
        for attempt in range(times):
            try:
                result = yield effect  # the next handler out answers
                break
            except RateLimited as error:
                if attempt == times - 1:
                    raise
                yield Log(f"retry in {2**attempt} s: {error}")
                yield Delay(2**attempt)
        return (yield Resume(k, result))

    return retry


def recording(tape: dict):  # record every model answer
    @handler
    def record(effect: Complete, k):
        tape[effect.prompt] = yield effect
        return (yield Resume(k, tape[effect.prompt]))

    return record


def replaying(tape: dict):  # answer from the tape; the model is never called
    @handler
    def replay(effect: Complete, k):
        return (yield Resume(k, tape[effect.prompt]))

    return replay


def tracing(seen: list[str]):  # a flight recorder: every effect, in order
    @handler
    def trace(effect, k):
        seen.append(type(effect).__name__)
        return (yield Pass(effect, k))

    return trace


def virtual_clock(start: datetime):  # waits take no time
    now = start
    sleepers = []
    order = count()
    advancing = False

    @do
    def advance():
        nonlocal now, advancing
        while sleepers:
            now, _, promise = heapq.heappop(sleepers)
            yield CompletePromise(promise, None)
        advancing = False

    @handler
    def clock(effect: Delay | GetTime, k):
        nonlocal advancing
        if isinstance(effect, GetTime):
            return (yield Resume(k, now))
        promise = yield CreatePromise()
        heapq.heappush(sleepers, (now + timedelta(seconds=effect.seconds), next(order), promise))
        if not advancing:
            advancing = True
            yield Spawn(advance(), priority=PRIORITY_IDLE, daemon=True)
        yield Wait(promise.future)
        return (yield Resume(k, None))

    return clock


@handler
def wall_clock(effect: Delay | GetTime, k):  # waits on asyncio
    if isinstance(effect, GetTime):
        return (yield Resume(k, datetime.now(timezone.utc)))
    yield Await(asyncio.sleep(effect.seconds))
    return (yield Resume(k, None))


async def slow_search(query: str) -> str:  # stand-in for an async HTTP client
    await asyncio.sleep(0.05)
    return {"tides": "twice a day", "moons": "one"}[query]


@handler
def http_search(effect: Search, k):
    return (yield Resume(k, (yield Await(slow_search(effect.query)))))


START = datetime(2026, 11, 2, 9, 0, tzinfo=timezone.utc)
Q = "why does the sea rise and fall?"


def simulated(program):  # scheduler + virtual clock
    return scheduled(virtual_clock(START)(try_handler(program)))


def real(program):  # scheduler + asyncio + wall clock
    return scheduled(await_handler()(wall_clock(try_handler(program))))


if __name__ == "__main__":
    print("1. test: fake model, fake tool, virtual clock, the person says yes")
    calls: list[str] = []
    print("  ->", run(simulated(printed(free(approvals([True])(fake_search(fake_model(calls)(research(Q)))))))))

    print("2. budget: 12 tokens, then stop")
    print("  ->", run(simulated(printed(budget(12)(approvals([True])(fake_search(fake_model([])(research(Q)))))))))

    print("3. retry: the tool answers 429 twice; the back-off runs on the virtual clock")
    print("  ->", run(simulated(printed(free(approvals([True])(fake_search(flaky_search(2)(retrying(3)(fake_model([])(research(Q)))))))))))

    print("4. the person says no once; the model revises")
    print("  ->", run(simulated(printed(free(approvals([False, True])(fake_search(fake_model([])(research(Q)))))))))

    print("5. record, then replay: the second run never calls the model")
    tape: dict = {}
    calls = []
    first = run(simulated(printed(free(approvals([True])(fake_search(fake_model(calls)(recording(tape)(research(Q)))))))))
    again = run(simulated(printed(free(approvals([True])(fake_search(replaying(tape)(research(Q))))))))
    print("  ->", first == again, "model calls:", len(calls), "tape:", len(tape))

    print("6. trace: every effect, in order")
    seen: list[str] = []
    run(simulated(printed(free(approvals([True])(fake_search(fake_model([])(tracing(seen)(research(Q)))))))))
    print("  ->", seen)

    print("7. production: wall clock, asyncio, a real-looking tool")
    print("  ->", run(real(printed(free(approvals([True])(http_search(fake_model([])(research(Q)))))))))
