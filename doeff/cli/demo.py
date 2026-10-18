"""``doeff demo``: one program under four handlers, each giving its effects another meaning.

``python -m doeff demo`` (or ``doeff demo``) needs nothing beyond ``pip install doeff``. It
prints the program ``digest``, then runs it four times, each under another handler, which
receives every effect together with ``k``, the rest of ``digest`` from that ``yield`` on:
``fake_world`` resumes ``k`` with made-up values, ``trace`` sees every effect and passes it on,
``budget(1)`` lets one effect through and then drops ``k``, so ``digest`` stops midway, and
``live_world`` answers ``Fetch`` over real HTTP. ``digest`` is the same in every run; what the
handler does with ``k`` changes, and so does the answer. ``--url`` picks the page ``live_world``
fetches (a ``file://`` URL is read the same way, without the network).

What the demo prints is what it runs: the code is this module's own source, and each ``>>>``
line is the expression it evaluates.
"""

import inspect
import json
import sys
import textwrap
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.request import urlopen

from doeff import EffectBase, Pass, Resume, __version__, do, handler, run

DEFAULT_URL = "https://example.com"
README_URL = "https://github.com/proboscis/doeff#readme"


@dataclass(frozen=True)
class Fetch(EffectBase):       # an effect: "get the page at url"
    url: str


@dataclass(frozen=True)
class Summarize(EffectBase):   # an effect: "summarize this text"
    text: str


@do
def digest(url):               # the program: it only yields effects
    page = yield Fetch(url)
    return (yield Summarize(page))


@do
def fake_world(effect, k):     # answers the effects it knows
    if isinstance(effect, Fetch):
        return (yield Resume(k, "<h1>Hello</h1>"))
    if isinstance(effect, Summarize):
        return (yield Resume(k, f"{len(effect.text)} chars"))
    yield Pass(effect, k)      # anything else goes to the next handler


@do
def trace(effect, k):          # sees every effect first, then passes it on
    print(f"trace: {effect!r}")
    return (yield Pass(effect, k))


def budget(effects):           # drops k after this many effects
    left = effects

    @do
    def spend(effect, k):
        nonlocal left
        if left == 0:          # return, and k is never resumed
            return f"over budget at {type(effect).__name__}"
        left -= 1
        return (yield Pass(effect, k))

    return handler(spend)


@do
def live_world(effect, k):     # answers Fetch over real HTTP
    if isinstance(effect, Fetch):
        with urlopen(effect.url, timeout=10) as response:
            page = response.read().decode(errors="replace")
        return (yield Resume(k, page))
    yield Pass(effect, k)      # Summarize goes on to fake_world


@dataclass(frozen=True)
class Step:
    """One run of ``digest``: why it runs, the handler it brings in, the line it evaluates."""

    # the sentences printed above the handler's source
    title: str
    # the handler whose source the step prints
    brings_in: Callable[..., object]
    # the expression printed after ``>>>`` and evaluated, with ``url`` bound to the page
    expression: str
    # the handler as the closing lines name it, and what it did with k
    handler: str
    did: str


STEPS = (
    Step(
        "1. Answer each effect: fake_world resumes k, the rest of digest,\n"
        "   with a made-up value.",
        fake_world,
        "run(handler(fake_world)(digest(url)))",
        "fake_world",
        "resumed k with made-up values",
    ),
    Step(
        "2. Watch it run: trace sees every effect first and passes it on.\n"
        "   It knows nothing about Fetch or Summarize, so it works on any program.",
        trace,
        "run(handler(fake_world)(handler(trace)(digest(url))))",
        "trace",
        "saw each effect, passed it on",
    ),
    Step(
        "3. Stop it midway: budget(1) lets one effect through, then returns\n"
        "   without resuming k. The rest of digest never runs, and nothing raises.",
        budget,
        "run(handler(fake_world)(budget(1)(digest(url))))",
        "budget(1)",
        "let one effect through, dropped k",
    ),
    Step(
        "4. Do real I/O: live_world answers Fetch over HTTP, and Summarize\n"
        "   passes on to fake_world. Swapping I/O is one meaning among these.",
        live_world,
        "run(handler(fake_world)(handler(live_world)(digest(url))))",
        "live_world",
        "answered Fetch over real HTTP",
    ),
)


def _source(*shown: Callable[..., object] | type) -> str:
    """The source of the shown classes and functions, indented, one blank line apart."""
    return "\n".join(textwrap.indent(inspect.getsource(item), "    ") for item in shown)


def main(url: str = DEFAULT_URL) -> int:
    """Print the program, run each step and print its answer, then what changed between them.

    Returns 0, 1 when ``live_world`` could not fetch ``url`` (the reason goes to stderr), or 2
    when ``url`` has no scheme for ``urlopen`` to read it by.
    """
    if not urlsplit(url).scheme:
        print(
            f"--url needs a full URL, such as https://{url} or file:///path/to/page.html",
            file=sys.stderr,
        )
        return 2
    print(
        f"doeff {__version__} demo: one program, four handlers, four meanings of its effects.\n"
    )
    print(f"digest yields two effects and does nothing else ({__file__}):\n")
    print(_source(Fetch, Summarize, digest))
    names = {
        "run": run,
        "handler": handler,
        "digest": digest,
        "fake_world": fake_world,
        "trace": trace,
        "budget": budget,
        "live_world": live_world,
        "url": url,
    }
    answers: tuple[object, ...] = ()
    for step in STEPS:
        print(f"{step.title}\n")
        print(_source(step.brings_in))
        if step is STEPS[0]:  # every run reads the same url, bound once before the first
            print(f">>> url = {json.dumps(url)}")
        print(f">>> {step.expression}")
        try:
            # The line printed above is the line that runs: no name beyond those of the demo.
            answer = eval(step.expression, {"__builtins__": {}}, names)
        except OSError as error:  # urlopen: no network, an HTTP error status, a missing file
            print(
                f"\nlive_world could not fetch {url}: {error}\n"
                "The runs above needed no network. Try --url with a page you can reach\n"
                "(a file:// URL is read the same way).",
                file=sys.stderr,
            )
            return 1
        print(f"{answer!r}\n")
        answers = (*answers, answer)
    print(f"Same in all {len(STEPS)} runs: digest, not one line of it changed.")
    print("Changed: the handler, what it did with k, and so the answer.")
    for step, answer in zip(STEPS, answers, strict=True):
        print(f"  {step.handler:<11} {step.did:<34} {answer!r}")
    print("\nState, exceptions and concurrency are effects with handlers of the same kind.")
    print(f"Next: {README_URL}")
    return 0
