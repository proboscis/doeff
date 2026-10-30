# on_disk.py
import json
import tempfile
from pathlib import Path

from doeff import Resume, handler, run
from doeff_core_effects.handlers import await_handler, slog_discard_handler
from doeff_core_effects.memo_effects import MemoExistsEffect, MemoGetEffect, MemoPutEffect
from doeff_core_effects.memo_handlers import content_address, make_memo_rewriter
from doeff_core_effects.scheduler import scheduled

from releases import REPOS, HttpRequest, github, latest_releases, reported, sent


def json_tape(path: Path):  # storage: MemoExists, MemoGet and MemoPut on a JSON file
    @handler
    def storage(effect: MemoExistsEffect | MemoGetEffect | MemoPutEffect, k):
        tape = json.loads(path.read_text()) if path.exists() else {}
        if isinstance(effect, MemoExistsEffect):
            return (yield Resume(k, effect.key in tape))
        if isinstance(effect, MemoGetEffect):
            return (yield Resume(k, tape[effect.key]))
        tape[effect.key] = effect.value
        path.write_text(json.dumps(tape, indent=2))
        return (yield Resume(k, None))

    return storage


def recorded(program, tape, network, key=content_address):
    program = make_memo_rewriter(HttpRequest, key_fn=key)(program)  # a hit from storage; a miss performed outward and kept
    program = json_tape(tape)(program)  # storage: the answers kept in a JSON file
    program = network(program)  # only the misses get this far
    return run(scheduled(await_handler(slog_discard_handler(program))))


@handler
def offline(effect: HttpRequest, k):  # in the network's place: a miss fails, and nothing is sent
    raise LookupError(f"not recorded: {effect.method} {effect.url}")


def method_and_url(effect: HttpRequest) -> str:  # a key without the headers, so without the token
    return f"{effect.method} {effect.url}"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        tape = Path(directory) / "releases.json"
        print("-- first: nothing in the file, so the API answers and the file keeps it")
        print(recorded(latest_releases(REPOS, "secret"), tape, github))
        print(f"sent: {sent.total()}")
        sent.clear()
        print("-- again: a new stack, the same file, offline in the network's place")
        print(recorded(latest_releases(REPOS, "secret"), tape, offline))
        print(f"sent: {sent.total()}")
        print("-- token: the default key hashes every field, the token too")
        print(recorded(reported(latest_releases(REPOS, "ci-dummy")), tape, offline))
        keyed = Path(directory) / "keyed.json"
        recorded(latest_releases(REPOS, "secret"), keyed, github, method_and_url)
        print("-- keyed: recorded with one token, replayed with another")
        print(recorded(latest_releases(REPOS, "ci-dummy"), keyed, offline, method_and_url))
