"""The orders and values that the figures of docs/articles/record-and-replay.md show, measured with doeff: what record does with
each request in record-performs-outward, what replay answers and throws in replay-throws-at-yield, the keys and the file of
what-the-key-keeps, and the run with no storage of five-limits. The programs are the article's (examples/record_and_replay/),
with listening handlers put between their layers.

Usage (from the repository root): uv run python tools/docs_site/figures_measure/record_and_replay.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "examples" / "record_and_replay"))

from doeff_core_effects import (
    MemoExistsEffect,
    MemoGetEffect,
    MemoPutEffect,
    await_handler,
    make_memo_rewriter,
    scheduled,
    slog_discard_handler,
)
from doeff_core_effects._memo_handlers import content_address
from on_disk import json_tape, method_and_url, offline, recorded
from releases import (
    REPOS,
    HttpRequest,
    github,
    latest_releases,
    recording,
    replaying,
    reported,
    sent,
)

from doeff import Resume, handler, run


@dataclass
class Heard:
    """What the listening handlers heard, in order."""

    mut_events: tuple[str, ...] = ()

    def add(self, event: str) -> None:
        self.mut_events = (*self.mut_events, event)


def requests(where: str, heard: Heard):
    """A handler that writes down each HttpRequest that passes it and the answer that comes back."""

    @handler
    def listen(effect: HttpRequest, k):
        heard.add(f"{where}: {effect.method} {effect.url.rsplit('/repos/', 1)[-1]}")
        answer = yield effect
        heard.add(f"{where}: back {answer}")
        return (yield Resume(k, answer))

    return listen


def memo_effects(heard: Heard):
    """A handler between the rewriter and the storage that writes down each MemoExists, MemoGet and MemoPut and its answer."""

    @handler
    def listen(effect: MemoExistsEffect | MemoGetEffect | MemoPutEffect, k):
        answer = yield effect
        shown = {MemoExistsEffect: "MemoExists", MemoGetEffect: "MemoGet", MemoPutEffect: "MemoPut"}
        heard.add(
            f"{shown[type(effect)]} -> {answer!r}" if answer is not None else shown[type(effect)]
        )
        return (yield Resume(k, answer))

    return listen


def listened(program, tape, network, heard: Heard, key=None):
    """recorded from on_disk.py, with memo_effects() between the rewriter and json_tape."""
    memo = make_memo_rewriter(HttpRequest, key_fn=key) if key else make_memo_rewriter(HttpRequest)
    program = json_tape(tape)(memo_effects(heard)(memo(program)))
    return run(scheduled(await_handler(slog_discard_handler(network(program)))))


if __name__ == "__main__":
    print("-- record-performs-outward: github(recording(tape)(latest_releases(REPOS, 'secret')))")
    heard, tape = Heard(), {}
    program = requests("inside record", heard)(latest_releases(REPOS, "secret"))
    result = run(github(requests("outside record", heard)(recording(tape)(program))))
    for event in heard.mut_events:
        print(" ", event)
    print(f"  tape: {tape}")
    print(f"  result {result}, sent: {sent.total()}")

    print("-- replay-throws-at-yield: replaying(tape)(latest_releases(REPOS, 'secret'))")
    sent.clear()
    outer = Heard()
    print(
        " ",
        run(requests("outside replay", outer)(replaying(tape)(latest_releases(REPOS, "secret")))),
    )
    print(f"  sent: {sent.total()}; a request outside replay: {bool(outer.mut_events)}")
    print(" ", run(replaying(tape)(reported(latest_releases(["acme/docs"], "secret")))))

    with tempfile.TemporaryDirectory() as directory:
        print("-- what-the-key-keeps: the default key and method_and_url")
        hashed = Path(directory) / "releases.json"
        recorded(latest_releases(REPOS, "secret"), hashed, github)
        secret = HttpRequest(
            "GET",
            "https://api.github.com/repos/acme/server/releases/latest",
            {"Authorization": "Bearer secret"},
        )
        dummy = HttpRequest(secret.method, secret.url, {"Authorization": "Bearer ci-dummy"})
        print(f"  default key, secret: {content_address(secret)}")
        print(f"  default key, ci-dummy: {content_address(dummy)}")
        print(f"  the keys in the file: {list(json.loads(hashed.read_text()))}")
        print(
            f"  in the file: {content_address(secret) in json.loads(hashed.read_text())}, "
            f"{content_address(dummy) in json.loads(hashed.read_text())}"
        )
        heard = Heard()
        print(" ", listened(reported(latest_releases(REPOS, "ci-dummy")), hashed, offline, heard))
        print(f"  memo effects: {heard.mut_events}")
        keyed = Path(directory) / "keyed.json"
        recorded(latest_releases(REPOS, "secret"), keyed, github, method_and_url)
        print(f"  method_and_url keys: {list(json.loads(keyed.read_text()))}")
        heard = Heard()
        print(
            " ", listened(latest_releases(REPOS, "ci-dummy"), keyed, offline, heard, method_and_url)
        )
        print(f"  memo effects: {heard.mut_events}")
        print(
            f"  'secret' in the files: {'secret' in hashed.read_text()}, {'secret' in keyed.read_text()}"
        )
        print(f"  a stored body: {json.loads(keyed.read_text())[method_and_url(secret)]!r}")

        print("-- five-limits: no storage handler, twice with github, then offline")
        sent.clear()
        memo = make_memo_rewriter(HttpRequest)
        for _ in range(2):
            run(
                scheduled(
                    await_handler(
                        slog_discard_handler(github(memo(latest_releases(REPOS, "secret"))))
                    )
                )
            )
            print(f"  sent: {sent.total()}")
        program = memo(reported(latest_releases(REPOS, "secret")))
        print("  offline:", run(scheduled(await_handler(slog_discard_handler(offline(program))))))
