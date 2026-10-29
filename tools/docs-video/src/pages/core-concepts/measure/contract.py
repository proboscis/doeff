# The handler contract scene: one program, page(), under handlers that each do one thing with k
# (docs/02-core-concepts.md, "Effects, Handlers and Continuations" and "Handler Contract"), and k being one-shot.
# Prints what page() answers under each, and a line whenever the code after its yield runs. The throw run's
# doeff traceback (on stderr) points at the yield in page().
from dataclasses import dataclass

from doeff_core_effects import Spawn, Wait, scheduled

from doeff import EffectBase, Pass, Resume, ResumeThrow, do, handler, run


@dataclass(frozen=True)
class Fetch(EffectBase):
    url: str


url = "https://example.com"


@do
def page():
    body = yield Fetch(url)
    print("  page(): the code after the yield runs")
    return f"body={body}"


@handler
def resumes(effect: Fetch, k):
    return (yield Resume(k, f"got {effect.url}"))


@handler
def throws(effect: Fetch, k):
    return (yield ResumeThrow(k, TimeoutError("slow")))


@handler
def passes(effect: Fetch, k):
    yield Pass(effect, k)


@handler
def outer(effect: Fetch, k):
    return (yield Resume(k, "from the next handler out"))


@do
def other():
    print("  other(): runs while k is held")
    return "other task done"


@handler
@do(non_tail=True)
def holds(effect: Fetch, k):
    # Keeps page()'s k while another task runs under the scheduler, then resumes it with that task's value.
    task = yield Spawn(other())
    value = yield Wait(task)
    return (yield Resume(k, value))


@handler
def drops(effect: Fetch, k):
    return f"stopped at {effect.url}"


@handler
@do(non_tail=True)
def twice(effect: Fetch, k):
    yield Resume(k, "first")
    return (yield Resume(k, "second"))


def show(name, program):
    """Print what one run of page() answers, or the error it raises, under the name of what its handler does."""
    print(f"{name}:")
    try:
        answer = repr(run(program))
    except Exception as error:  # noqa: BLE001 - the scene shows the error a run raises
        answer = f"raises {type(error).__name__}: {error}"
    print(f"  answer: {answer}")


show("resume", resumes(page()))
show("throw", throws(page()))
show("pass", outer(passes(page())))
show("hold", scheduled(holds(page())))
show("drop", drops(page()))
show("resume twice", twice(page()))
