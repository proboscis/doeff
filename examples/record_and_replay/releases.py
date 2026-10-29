# releases.py
import json
from collections import Counter

from doeff import Resume, ResumeThrow, do, handler, run
from doeff_core_effects import HttpRequest, HttpResponse

GITHUB = "https://api.github.com"


@do
def latest_releases(repos: list[str], token: str, api: str = GITHUB):  # asks; never sends
    tags = {}
    for repo in repos:
        response = yield HttpRequest(
            "GET",
            f"{api}/repos/{repo}/releases/latest",
            headers={"Authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        tags[repo] = json.loads(response.text)["tag_name"]
    return tags


# Answers the way the GitHub API does, from a dict, so this file runs offline.
# live.py sends the same requests over HTTP.
TAGS = {"acme/server": "v2.4.1", "acme/client": "v1.9.0"}
sent: Counter[str] = Counter()  # how many times each URL was sent


@handler
def github(effect: HttpRequest, k):  # every request that reaches it counts as sent
    sent[effect.url] += 1
    repo = effect.url.removeprefix(f"{GITHUB}/repos/").removesuffix("/releases/latest")
    body = json.dumps({"tag_name": TAGS[repo]})
    return (yield Resume(k, HttpResponse(200, {}, body.encode(), body, effect.url, 0.0)))


def recording(tape: dict):  # perform each request outward and keep its answer
    @handler
    def record(effect: HttpRequest, k):
        answer = yield effect  # the next handler out answers
        tape[(effect.method, effect.url)] = answer
        return (yield Resume(k, answer))

    return record


def replaying(tape: dict):  # answer from the tape and never perform outward
    @handler
    def replay(effect: HttpRequest, k):
        key = (effect.method, effect.url)
        if key not in tape:
            error = LookupError(f"not recorded: {effect.method} {effect.url}")
            return (yield ResumeThrow(k, error))  # raised at the program's yield
        return (yield Resume(k, tape[key]))

    return replay


@do
def reported(program):  # a caller's own try catches what a handler throws in
    try:
        return (yield program)
    except LookupError as error:
        return f"LookupError: {error}"


REPOS = ["acme/server", "acme/client"]

if __name__ == "__main__":
    tape = {}
    print("-- record: the API answers, and the recorder keeps each answer")
    print(run(github(recording(tape)(latest_releases(REPOS, "secret")))))
    print(f"sent: {sent.total()}")
    sent.clear()
    print("-- replay: the same program, with only the player around it")
    print(run(replaying(tape)(latest_releases(REPOS, "secret"))))
    print(f"sent: {sent.total()}")
    print("-- unknown: a repository the tape does not have")
    print(run(replaying(tape)(reported(latest_releases(["acme/docs"], "secret")))))
