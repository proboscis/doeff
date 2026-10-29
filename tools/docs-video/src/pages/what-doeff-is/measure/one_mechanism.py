# The one-mechanism scene: crawl fetches pages in parallel tasks, counts them in state, turns the error of one
# task into a value and collects the log lines of every task (docs/positioning/what-doeff-is.md, "One mechanism
# for state, exceptions, concurrency, observation, budgets and I/O"). The program and its handlers are the page's.
# Prints every effect the programs yield, in order, with the handler that gives it its meaning (a trace handler,
# innermost, sees each one and passes it on), then the results. The last run leaves out local_pages: Fetch then
# reaches no handler, and Try turns the UnhandledEffect of each task into an Err ("Swapping I/O is one use").
from dataclasses import dataclass

from doeff_core_effects import (
    Gather,
    Get,
    Listen,
    Put,
    Spawn,
    Tell,
    Try,
    listen_handler,
    scheduled,
    state,
    try_handler,
    writer,
)

from doeff import EffectBase, Pass, Resume, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


@do
def size(url):
    page = yield Fetch(url)
    if not page:
        raise ValueError(f"empty page: {url}")
    yield Put("fetched", (yield Get("fetched")) + 1)
    yield Tell(f"fetched {url}")
    return len(page)


@do
def crawl(urls):
    tasks = []
    for url in urls:
        tasks.append((yield Spawn(Try(size(url)))))
    return (yield Gather(*tasks))


@do
def report(urls):
    sizes, log = yield Listen(crawl(urls))
    return sizes, log, (yield Get("fetched"))


PAGES = {"https://a.example": "<p>hello</p>", "https://b.example": ""}


@do
def local_pages(effect: Fetch, k):
    return (yield Resume(k, PAGES[effect.url]))


MEANING = {
    "Fetch": "local_pages",
    "Try": "try_handler",
    "Listen": "listen_handler",
    "WriterTellEffect": "writer",                    # what Tell(...) builds
    "Get": "state",
    "Put": "state",
    "Spawn": "scheduled",
    "Gather": "scheduled",
}


@do
def trace(effect, k):                                # notes each effect and the handler it is for, and passes it on
    name = type(effect).__name__
    shown = {"Spawn": "Spawn(Try(size(url)))", "Try": "Try(size(url))", "Gather": "Gather(*tasks)", "Listen": "Listen(crawl(urls))"}
    print(f"  {shown.get(name, repr(effect)):<40} -> {MEANING.get(name, '?')}")
    return (yield Pass(effect, k))


def stacked(program, with_pages=True):              # the page's handlers around a program, local_pages optional
    if with_pages:
        program = handler(local_pages)(program)
    program = try_handler(program)
    program = listen_handler(program)
    program = writer(program)
    program = state({"fetched": 0})(program)
    return scheduled(program)


print("effects, in the order they were yielded:")
sizes, log, fetched = run(stacked(handler(trace)(report(list(PAGES)))))
print("sizes:  ", sizes)
print("log:    ", log)
print("fetched:", fetched)

print("without local_pages:")
sizes, log, fetched = run(stacked(report(list(PAGES)), with_pages=False))
for result in sizes:
    print("  ", type(result).__name__, type(result.error).__name__, str(result.error).splitlines()[0])
print("log:    ", log)
print("fetched:", fetched)
