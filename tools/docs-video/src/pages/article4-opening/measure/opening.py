"""The runs behind the article4-opening video: every value and order of events it shows, from the example of the
article "Record and replay" (examples/record_and_replay/releases.py).

Usage (from the repository root): uv run python tools/docs-video/src/pages/article4-opening/measure/opening.py > tools/docs-video/src/pages/article4-opening/measure/opening.log
"""

import sys
from pathlib import Path

# The article's example (releases.py) is imported through this path.
sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "examples" / "record_and_replay"))

from releases import REPOS, HttpRequest, github, latest_releases, recording, replaying, sent

from doeff import Pass, handler, run

seen: list[str] = []


def watched(
    name: str,
):  # writes down each request that reaches this point of the stack, then passes it on
    @handler
    def watch(effect: HttpRequest, k):
        seen.append(f"{name}: {effect.method} {effect.url.removeprefix('https://api.github.com')}")
        return (yield Pass(effect, k))

    return watch


# o2: the first request latest_releases yields.
first: list[HttpRequest] = []


@handler
def kept(effect: HttpRequest, k):
    first.append(effect)
    return (yield Pass(effect, k))


run(github(kept(latest_releases(REPOS, "secret"))))
print("o2 first request:", first[0])
sent.clear()

# o4-o5: under github alone.
tags = run(github(latest_releases(REPOS, "secret")))
print("o5 under github:", tags)
print("o5 sent:", sent.total())
sent.clear()

# o6: recording(tape) between them: the requests reach github through record, and the tape keeps the answers.
tape: dict = {}
print(
    "o6 recorded:",
    run(github(watched("github gets")(recording(tape)(latest_releases(REPOS, "secret"))))),
)
print("o6 order:", seen)
print("o6 sent:", sent.total())
for key, answer in tape.items():
    print("o6 tape:", key, "->", answer)
sent.clear()
seen.clear()

# o7: github taken away, replaying(tape) alone.
print(
    "o7 replayed:",
    run(watched("outside replay")(replaying(tape)(latest_releases(REPOS, "secret")))),
)
print("o7 reached outside replay:", seen)
print("o7 sent:", sent.total())
