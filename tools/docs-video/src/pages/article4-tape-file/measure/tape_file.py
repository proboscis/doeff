"""The runs behind the article4-tape-file video: every value and order of events it shows, from the example of the
article "Record and replay" (examples/record_and_replay/on_disk.py).

The stack is the one `recorded` builds, with a watcher between memo and storage and one in front of the network, so
the order of the effects can be written down.

Usage (from the repository root): uv run python tools/docs-video/src/pages/article4-tape-file/measure/tape_file.py > tools/docs-video/src/pages/article4-tape-file/measure/tape_file.log
"""

import json
import sys
import tempfile
from pathlib import Path

# The article's examples (on_disk.py, releases.py) are imported through this path.
sys.path.insert(0, str(Path(__file__).resolve().parents[6] / "examples" / "record_and_replay"))

from doeff_core_effects import (
    MemoExistsEffect,
    MemoGetEffect,
    MemoPutEffect,
    await_handler,
    make_memo_rewriter,
    scheduled,
    slog_discard_handler,
)
from on_disk import json_tape, offline
from releases import REPOS, HttpRequest, github, latest_releases, sent

from doeff import Resume, handler, run

seen: list[str] = []


@handler
def watch_storage(effect: MemoExistsEffect | MemoGetEffect | MemoPutEffect, k):
    answer = yield effect
    name = type(effect).__name__.removesuffix("Effect")
    seen.append(f"{name} -> {answer!r}" if isinstance(effect, MemoExistsEffect) else name)
    return (yield Resume(k, answer))


@handler
def watch_network(effect: HttpRequest, k):
    answer = yield effect
    seen.append(f"network answers {effect.url.removeprefix('https://api.github.com')}")
    return (yield Resume(k, answer))


def watched_recorded(program, tape, network):
    program = make_memo_rewriter(HttpRequest)(program)
    program = watch_storage(program)
    program = json_tape(tape)(program)
    program = watch_network(program)
    program = network(program)
    return run(scheduled(await_handler(slog_discard_handler(program))))


with tempfile.TemporaryDirectory() as directory:
    tape = Path(directory) / "releases.json"
    print("t5 first:", watched_recorded(latest_releases(REPOS, "secret"), tape, github))
    print("t5 order:", seen)
    print("t5 sent:", sent.total())
    stored = json.loads(tape.read_text())
    print("t5 file:", {key[:12] + "…": value for key, value in stored.items()})
    sent.clear()
    seen.clear()
    print("t6 again:", watched_recorded(latest_releases(REPOS, "secret"), tape, offline))
    print("t6 order:", seen)
    print("t6 sent:", sent.total())
