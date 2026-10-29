# on_disk.py
import tempfile
from pathlib import Path

from doeff import handler, run
from doeff_core_effects import HttpRequest
from doeff_core_effects.handlers import await_handler, slog_discard_handler
from doeff_core_effects.memo_handlers import content_address, make_memo_rewriter, sqlite_memo_handler
from doeff_core_effects.scheduler import scheduled

from releases import REPOS, github, latest_releases, reported, sent


def recorded(program, database, network, key=content_address):
    program = make_memo_rewriter(HttpRequest, key_fn=key)(program)  # a hit from storage; a miss performed outward and kept
    program = sqlite_memo_handler(database)(program)  # storage: MemoExists, MemoGet and MemoPut on a SQLite file
    program = network(program)  # only the misses get this far
    return run(scheduled(await_handler()(slog_discard_handler(program))))


@handler
def offline(effect: HttpRequest, k):  # in the network's place: a miss fails, and nothing is sent
    raise LookupError(f"not recorded: {effect.method} {effect.url}")


def method_and_url(effect: HttpRequest) -> str:  # a key without the headers, so without the token
    return f"{effect.method} {effect.url}"


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as directory:
        tape = Path(directory) / "releases.sqlite"
        print("-- first: nothing in the file, so the API answers and the file keeps it")
        print(recorded(latest_releases(REPOS, "secret"), tape, github))
        print(f"sent: {sent.total()}")
        sent.clear()
        print("-- again: a new stack, the same file, offline in the network's place")
        print(recorded(latest_releases(REPOS, "secret"), tape, offline))
        print(f"sent: {sent.total()}")
        print("-- token: the default key hashes every field, the token too")
        print(recorded(reported(latest_releases(REPOS, "ci-dummy")), tape, offline))
        keyed = Path(directory) / "keyed.sqlite"
        recorded(latest_releases(REPOS, "secret"), keyed, github, method_and_url)
        print("-- keyed: recorded with one token, replayed with another")
        print(recorded(latest_releases(REPOS, "ci-dummy"), keyed, offline, method_and_url))
