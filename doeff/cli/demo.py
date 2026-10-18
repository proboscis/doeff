"""``doeff demo``: one program, answered first by a fake handler, then over real HTTP.

``python -m doeff demo`` (or ``doeff demo``) needs nothing beyond ``pip install doeff``. It
prints the program and the handlers below, then runs ``digest`` twice: under ``fake_world``
alone, and with ``live_world`` stacked in front so that ``Fetch`` goes over real HTTP.
``digest`` is the same in both runs; only the handler changes, and so does the answer.
``--url`` picks the page ``live_world`` fetches (a ``file://`` URL is read the same way,
without the network).

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
class Fetch(EffectBase):       # a request your code can make
    url: str


@dataclass(frozen=True)
class Summarize(EffectBase):
    text: str


@do
def digest(url):               # your logic: no I/O inside
    page = yield Fetch(url)
    return (yield Summarize(page))


@do
def fake_world(effect, k):     # answers the requests it knows
    if isinstance(effect, Fetch):
        return (yield Resume(k, "<h1>Hello</h1>"))
    if isinstance(effect, Summarize):
        return (yield Resume(k, f"{len(effect.text)} chars"))
    yield Pass(effect, k)      # anything else goes to the next handler


@do
def live_world(effect, k):     # answers Fetch over real HTTP
    if isinstance(effect, Fetch):
        with urlopen(effect.url, timeout=10) as response:
            page = response.read().decode(errors="replace")
        return (yield Resume(k, page))
    yield Pass(effect, k)      # Summarize goes on to fake_world


@dataclass(frozen=True)
class Step:
    """One run of ``digest``: why it runs, the handler it brings in, and the line it evaluates."""

    # the sentence printed above the handler's source
    title: str
    # the handler whose source the step prints
    brings_in: Callable[..., object]
    # the expression printed after ``>>>`` and evaluated, with ``url`` bound to the page
    expression: str


STEPS = (
    Step(
        "1. A fake handler, as in your tests: canned answers, no I/O.",
        fake_world,
        "run(handler(fake_world)(digest(url)))",
    ),
    Step(
        "2. Stack a live handler in front. Fetch now goes over real HTTP,\n"
        "   and Summarize passes on to fake_world.",
        live_world,
        "run(handler(fake_world)(handler(live_world)(digest(url))))",
    ),
)


def _source(*shown: Callable[..., object] | type) -> str:
    """The source of the shown classes and functions, indented, one blank line apart."""
    return "\n".join(textwrap.indent(inspect.getsource(item), "    ") for item in shown)


def main(url: str = DEFAULT_URL) -> int:
    """Print the program, run each step and print its answer.

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
        f"doeff {__version__} demo: one program, and the handlers decide "
        "what its effects mean.\n"
    )
    print(f"digest yields requests and does no I/O itself ({__file__}):\n")
    print(_source(Fetch, Summarize, digest))
    names = {
        "run": run,
        "handler": handler,
        "digest": digest,
        "fake_world": fake_world,
        "live_world": live_world,
        "url": url,
    }
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
                "The fake run above needed no network. Try --url with a page you can reach\n"
                "(a file:// URL is read the same way).",
                file=sys.stderr,
            )
            return 1
        print(f"{answer!r}\n")
    print("digest did not change between the runs. The handler did, and so did the answer.")
    print(f"Next: {README_URL}")
    return 0
