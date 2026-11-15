"""Writes the four click-through figures of docs/articles/record-and-replay.md (docs/assets/figures/<name>.html):
record-performs-outward (A recorder performs the effect again: record yields the request on and keeps the answer),
replay-throws-at-yield (A player answers from the tape: replay answers without going out, and throws LookupError into k for a
request it does not have), what-the-key-keeps (What makes two requests the same: the default key and method_and_url) and
five-limits (Limits, one per step). The code in the figures is the article's (examples/record_and_replay/), and the orders and
values are the ones record_and_replay.py in this directory prints.

Usage (from the repository root): uv run --no-project python tools/docs_site/figures_measure/record_and_replay_figures.py
"""

from __future__ import annotations

from yield_and_async_figures import co, end, fn, kw, line, ring, st, step, write

NL = "\n"
NONE = kw("None")
# Seconds of stillness after one move before the next move of the same step starts (the storyboard's pace), and how long
# step-figure.js takes a token from one stop to the next.
GAP = 0.9
LEG = 0.6
SERVER = "https://api.github.com/repos/acme/server/releases/latest"
SERVER_BODY = '{"tag_name": "v2.4.1"}'
CLIENT_BODY = '{"tag_name": "v1.9.0"}'
TAGS = "{'acme/server': 'v2.4.1', 'acme/client': 'v1.9.0'}"
NOT_RECORDED_DOCS = (
    "LookupError: not recorded: GET https://api.github.com/repos/acme/docs/releases/latest"
)
NOT_RECORDED_SERVER = f"LookupError: not recorded: GET {SERVER}"
# The default keys, as record_and_replay.py prints them (a SHA-256 of the pickled request).
KEY_SECRET = "f9af44c4711bb083c04c5bab73e3172247d30d13760c36b1767f693667200aa0"
KEY_DUMMY = "2d0282a4b4a245aaef0d57e83abb418c75c2c928da3bae68ac333ac1595891ca"
KEY_CLIENT = "8537d5d5f13e846463e90ed80817e294243df28952506b9d7317d25a3d30506e"
# Strings of the code that sit inside f-strings below (Python 3.11 cannot reuse an f-string's quote inside it).
GET = st('"GET"')
URL = st('f"{api}/repos/{repo}/releases/latest"')
AUTH = st('"Authorization"')
BEARER = st('f"Bearer {token}"')
TAG_NAME = st('"tag_name"')
GITHUB_PREFIX = st('f"{GITHUB}/repos/"')
LATEST = st('"/releases/latest"')
NOT_RECORDED = st('f"not recorded: {effect.method} {effect.url}"')
LOOKUP = st('f"LookupError: {error}"')
METHOD_URL = st('f"{effect.method} {effect.url}"')


def code(lines: list[str], indent: int = 8) -> str:
    """The lines of a block of code, one per line of the written HTML."""
    return (NL + " " * indent).join(lines)


def in_turn(trips: dict[str, dict]) -> dict[str, dict]:
    """Token trips of one step that run one after another: each starts GAP seconds after the one before it has stopped. A trip
    that already has a delay (a k that goes along with its effect) keeps it and does not count as a move of its own."""
    start = 0.0
    timed = {}
    for name, trip in trips.items():
        if "delay" in trip:
            timed[name] = trip
            continue
        timed[name] = {**trip, "delay": round(start, 2)} if start else trip
        start += LEG * (len(trip.get("via", [])) + 1) + GAP
    return timed


# --- latest_releases (releases.py 18-28), the program inside every figure ------------------------------------------------

PROGRAM = [
    line(None, kw("@do")),
    line(
        None,
        f"{kw('def')} {fn('latest_releases')}(repos: list[str], token: str, api: str = GITHUB):",
    ),
    line(None, "    tags = {}"),
    line(None, f"    {kw('for')} repo {kw('in')} repos:"),
    line("s-yield", f"        body = {kw('yield')} HttpRequest("),
    line(None, f"            {GET},"),
    line(None, f"            {URL},"),
    line("s-headers", f"            headers={{{AUTH}: {BEARER}}},"),
    line(None, "        )"),
    line("s-tag", f"        tags[repo] = json.loads(body)[{TAG_NAME}]"),
    line("s-return", f"    {kw('return')} tags"),
]


def program_core(title: str = "the program") -> str:
    """latest_releases as the box the handlers are drawn around, with the tags it has so far."""
    return (
        '<div class="sf-core" data-anchor="core">'
        f'<div class="sf-core-head"><span>latest_releases(REPOS, "secret")</span><span>{title}</span></div>'
        '<div class="sf-vars"><span>tags = <span data-slot="tags"></span></span></div>'
        f'<div class="sf-code">{"".join(PROGRAM)}</div>'
        "</div>"
    )


def tape(title: str) -> str:
    """The dict record writes and replay reads: one row per request, the key and the body."""
    return (
        f'<div class="sf-tape" data-anchor="tape"><span>{title}</span>'
        '<div class="sf-tape-row" data-anchor="tape-1"><code data-slot="tape-1"></code></div>'
        '<div class="sf-tape-row" data-anchor="tape-2"><code data-slot="tape-2"></code></div>'
        "</div>"
    )


ROW_SERVER = f'("GET", ".../acme/server/...") → {SERVER_BODY}'
ROW_CLIENT = f'("GET", ".../acme/client/...") → {CLIENT_BODY}'

# --- record-performs-outward: github(recording(tape)(latest_releases(REPOS, "secret"))) ------------------------------------

RECORD_CODE = [
    line(None, kw("@handler")),
    line("g-def", f"{kw('def')} {fn('github')}(effect: HttpRequest, k):"),
    line("g-sent", "    sent[effect.url] += 1"),
    line(None, f"    repo = effect.url.removeprefix({GITHUB_PREFIX}).removesuffix({LATEST})"),
    line(
        "g-resume",
        f"    {kw('return')} ({kw('yield')} Resume(k, json.dumps({{{TAG_NAME}: TAGS[repo]}})))",
    ),
    line(None, ""),
    line(None, f"{kw('def')} {fn('recording')}(tape: dict):"),
    line(None, f"    {kw('@handler')}"),
    line("r-def", f"    {kw('def')} {fn('record')}(effect: HttpRequest, k):"),
    line(
        "r-yield", f"        answer = {kw('yield')} effect  {co('# the next handler out answers')}"
    ),
    line("r-tape", "        tape[(effect.method, effect.url)] = answer"),
    line("r-resume", f"        {kw('return')} ({kw('yield')} Resume(k, answer))"),
]
RECORD_STAGE = f"""<div class="sf-stage">
  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-panels is-rings-and-code">
    <div class="sf-rings">
      {ring("github", 'github · sent: <span data-slot="sent"></span>', ring("record", "record", program_core()))}
    </div>
    <section class="sf-panel is-handler is-wrap" data-panel="handlers">
      <div class="sf-head"><span>the handlers</span><span>from releases.py</span></div>
      <div class="sf-code">
        {code(RECORD_CODE)}
      </div>
      {tape("tape: the key → the answer")}
      <div class="sf-screen" data-anchor="left-out"><span>not in the key</span><code>headers={{"Authorization": "Bearer secret"}}</code></div>
    </section>
  </div>
  <div class="sf-token is-effect" data-token="req">HttpRequest(GET .../acme/server/...)</div>
  <div class="sf-token is-k" data-token="k">k <small>the rest of latest_releases</small></div>
  <div class="sf-token is-value" data-token="body">{SERVER_BODY}</div>
  <div class="sf-token is-effect" data-token="req2">HttpRequest(GET .../acme/client/...)</div>
  <div class="sf-token is-value" data-token="body2">{CLIENT_BODY}</div>
</div>"""

HOLD_RECORD = {"m-record": "is-on"}
RECORD_STEPS = [
    step(
        "<code>latest_releases</code> yields a request and pauses. <code>record</code> gets it with <code>k</code>.",
        now=["s-yield"],
        marks=HOLD_RECORD,
        values={
            "call": 'run(github(recording(tape)(latest_releases(REPOS, "secret"))))',
            "result": "",
            "tags": "{}",
            "sent": "0",
            "tape-1": "",
            "tape-2": "",
        },
        tokens={
            "req": {"from": end("s-yield"), **end("n-record")},
            "k": {"from": end("s-yield"), "at": "m-record", "delay": 0.3},
        },
    ),
    step(
        "<code>record</code> yields the same request again, so it goes on out to <code>github</code>.",
        active="handlers",
        now=["r-def", "r-yield"],
        marks=HOLD_RECORD,
        tokens={"req": {"from": end("n-record"), **end("n-github")}, "k": {"at": "m-record"}},
    ),
    step(
        "<code>github</code> counts it as sent and answers with the body, back to <code>record</code>.",
        active="handlers",
        now=["g-def", "g-sent", "g-resume", "r-yield"],
        marks=HOLD_RECORD,
        values={"sent": "1"},
        tokens={"body": {"from": end("n-github"), **end("n-record")}, "k": {"at": "m-record"}},
    ),
    step(
        "<code>record</code> keeps the pair: the method and URL as the key, the body as the answer.",
        active="handlers",
        now=["r-tape", "tape-1"],
        marks=HOLD_RECORD,
        values={"tape-1": ROW_SERVER},
        tokens={"body": end("n-record"), "k": {"at": "m-record"}},
    ),
    step(
        "<code>record</code> resumes <code>k</code> with the same body. That <code>k</code> is now used.",
        active="handlers",
        now=["r-resume", "s-yield", "s-tag"],
        values={"tags": "{'acme/server': 'v2.4.1'}"},
        tokens={
            "body": {"from": end("n-record"), **end("s-yield"), "merge": True},
            "k": {"from": "m-record", **end("s-yield"), "merge": True, "delay": 0.3},
        },
    ),
    step(
        "The second request goes the same way: two rows in the tape, <code>sent: 2</code>.",
        active="handlers",
        now=["s-yield", "r-yield", "r-tape", "tape-2"],
        values={"sent": "2", "tape-2": ROW_CLIENT, "tags": TAGS, "result": TAGS},
        tokens=in_turn(
            {
                "req2": {"from": end("s-yield"), "via": [end("n-record")], **end("n-github")},
                "body2": {
                    "from": end("n-github"),
                    "via": [end("n-record")],
                    **end("s-yield"),
                    "merge": True,
                },
            }
        ),
    ),
    step(
        "The headers, and the token in them, are left out of the key.",
        active="handlers",
        now=["s-headers", "r-tape"],
        marks={"left-out": "is-on is-dead"},
    ),
]

# --- replay-throws-at-yield: replaying(tape)(latest_releases(REPOS, "secret")), then acme/docs under reported --------------

REPLAY_CODE = [
    line(None, f"{kw('def')} {fn('replaying')}(tape: dict):"),
    line(None, f"    {kw('@handler')}"),
    line("p-def", f"    {kw('def')} {fn('replay')}(effect: HttpRequest, k):"),
    line("p-key", "        key = (effect.method, effect.url)"),
    line("p-if", f"        {kw('if')} key {kw('not in')} tape:"),
    line("p-error", f"            error = LookupError({NOT_RECORDED})"),
    line(
        "p-throw",
        f"            {kw('return')} ({kw('yield')} ResumeThrow(k, error))  {co('# raised at the program' + chr(39) + 's yield')}",
    ),
    line("p-resume", f"        {kw('return')} ({kw('yield')} Resume(k, tape[key]))"),
    line(None, ""),
    line(None, kw("@do")),
    line("t-def", f"{kw('def')} {fn('reported')}(program):"),
    line("t-try", f"    {kw('try')}:"),
    line("t-yield", f"        {kw('return')} ({kw('yield')} program)"),
    line("t-except", f"    {kw('except')} LookupError {kw('as')} error:"),
    line("t-return", f"        {kw('return')} {LOOKUP}"),
]
REPORTED = (
    '<div class="sf-try" data-anchor="try"><span class="sf-try-name" data-anchor="try-name">'
    "reported · try: … except LookupError</span>"
    f"{program_core()}</div>"
)
NOTHING_OUT = (
    '<div class="sf-edge" data-anchor="outside"><span>out here: no handler of <code>HttpRequest</code> · sent: '
    '<span data-slot="sent"></span></span></div>'
)
REPLAY_STAGE = f"""<div class="sf-stage">
  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-panels is-rings-and-code">
    <div class="sf-rings">
      {NOTHING_OUT}
      {ring("replay", "replay", REPORTED)}
    </div>
    <section class="sf-panel is-handler is-wrap" data-panel="handlers">
      <div class="sf-head"><span>the player</span><span>from releases.py</span></div>
      <div class="sf-code">
        {code(REPLAY_CODE)}
      </div>
      {tape("tape: what record kept")}
      <div class="sf-screen" data-anchor="asked"><span>the key</span><code data-slot="key"></code></div>
    </section>
  </div>
  <div class="sf-token is-effect" data-token="req">HttpRequest(GET .../acme/server/...)</div>
  <div class="sf-token is-k" data-token="k">k <small>the rest of latest_releases</small></div>
  <div class="sf-token is-value" data-token="body">{SERVER_BODY}</div>
  <div class="sf-token is-effect" data-token="req2">HttpRequest(GET .../acme/client/...)</div>
  <div class="sf-token is-value" data-token="body2">{CLIENT_BODY}</div>
  <div class="sf-token is-effect" data-token="req3">HttpRequest(GET .../acme/docs/...)</div>
  <div class="sf-token is-k" data-token="k3">k <small>the rest of latest_releases</small></div>
  <div class="sf-token is-fail" data-token="error">LookupError</div>
</div>"""

NO_TRY = {"try": "is-off"}
HOLD_REPLAY = {**NO_TRY, "m-replay": "is-on"}
REPLAY_STEPS = [
    step(
        "Only the player is around the program. Nothing outside it answers <code>HttpRequest</code>.",
        now=["outside"],
        marks=NO_TRY,
        values={
            "call": 'run(replaying(tape)(latest_releases(REPOS, "secret")))',
            "result": "",
            "tags": "{}",
            "sent": "0",
            "tape-1": ROW_SERVER,
            "tape-2": ROW_CLIENT,
            "key": "",
        },
    ),
    step(
        "<code>replay</code> makes the key and finds it in the tape.",
        active="handlers",
        now=["s-yield", "p-def", "p-key", "p-if", "tape-1"],
        marks={**HOLD_REPLAY, "asked": "is-on"},
        values={"key": '("GET", ".../acme/server/...")'},
        tokens={
            "req": {"from": end("s-yield"), **end("n-replay")},
            "k": {"from": end("s-yield"), "at": "m-replay", "delay": 0.3},
        },
    ),
    step(
        "<code>replay</code> resumes that <code>k</code> with the recorded body. Nothing is sent.",
        active="handlers",
        now=["p-resume", "s-yield", "s-tag", "outside"],
        marks={**NO_TRY, "asked": "is-on"},
        values={"tags": "{'acme/server': 'v2.4.1'}"},
        tokens={
            "body": {"from": end("n-replay"), **end("s-yield"), "merge": True},
            "k": {"from": "m-replay", **end("s-yield"), "merge": True, "delay": 0.3},
        },
    ),
    step(
        "Both tags come back, and <code>sent</code> stays at 0.",
        active="handlers",
        now=["p-resume", "tape-2", "outside"],
        marks={**NO_TRY, "asked": "is-on"},
        values={"key": '("GET", ".../acme/client/...")', "tags": TAGS, "result": TAGS},
        tokens=in_turn(
            {
                "req2": {"from": end("s-yield"), **end("n-replay")},
                "body2": {"from": end("n-replay"), **end("s-yield"), "merge": True},
            }
        ),
    ),
    step(
        "A new run asks for <code>acme/docs</code>. The tape has no row for it.",
        active="handlers",
        now=["s-yield", "p-key", "p-if"],
        marks={"try": "is-new", "m-replay": "is-on", "asked": "is-on is-fail"},
        values={
            "call": 'run(replaying(tape)(reported(latest_releases(["acme/docs"], "secret"))))',
            "result": "",
            "tags": "{}",
            "key": '("GET", ".../acme/docs/...")',
        },
        tokens={
            "req3": {"from": end("s-yield"), **end("n-replay")},
            "k3": {"from": end("s-yield"), "at": "m-replay", "delay": 0.3},
        },
    ),
    step(
        "<code>replay</code> throws a <code>LookupError</code> into <code>k</code>. It is raised at the program's "
        "<code>yield</code>.",
        active="handlers",
        now=["p-error", "p-throw"],
        marks={"asked": "is-on is-fail", "s-yield": "is-fail"},
        tokens={
            "error": {"from": end("n-replay"), **end("s-yield")},
            "k3": {"from": "m-replay", **end("s-yield"), "merge": True, "delay": 0.3},
        },
    ),
    step(
        "It travels up like any exception, to the <code>try</code> in <code>reported</code>.",
        active="handlers",
        now=["t-except", "t-return", "try-name"],
        marks={"s-yield": "is-fail"},
        values={"result": NOT_RECORDED_DOCS},
        tokens={"error": {"from": end("s-yield"), **end("try-name"), "merge": True}},
    ),
]

# --- what-the-key-keeps: the same request with two tokens, under the default key and under method_and_url -----------------


def request(side: str, token: str, accept: str) -> str:
    """One of the two requests, field by field, with the key it gets and what the file says about that key."""
    return (
        '<div class="sf-code">'
        + line(None, "HttpRequest(")
        + line(f"{side}-method", f"    method={GET},")
        + line(
            f"{side}-url",
            f"    url={st(chr(34) + '.../repos/acme/server/releases/latest' + chr(34))},",
        )
        + line(
            f"{side}-auth", f"    headers={{{AUTH}: {st(chr(34) + 'Bearer ' + token + chr(34))}}},"
        )
        + line(f"{side}-accept", f"    {co('# and if it also sent Accept: ' + accept)}")
        + line(None, ")")
        + "</div>"
        f'<div class="sf-keybox" data-anchor="{side}-key"><span>the key</span><code data-slot="{side}-key"></code></div>'
    )


KEY_CODE = [
    line("c-recorded", f"{kw('def')} {fn('recorded')}(program, tape, network, key={NONE}):"),
    line(
        "c-memo",
        f"    memo = make_memo_rewriter(HttpRequest, key_fn=key) {kw('if')} key {kw('else')} make_memo_rewriter(HttpRequest)",
    ),
    line(None, co("    # …")),
    line(None, ""),
    line("c-mu-def", f"{kw('def')} {fn('method_and_url')}(effect: HttpRequest) -&gt; str:"),
    line("c-mu-return", f"    {kw('return')} {METHOD_URL}"),
]
KEY_FILE = (
    '<div class="sf-tape" data-anchor="file"><span>the file: <span data-slot="file"></span></span>'
    '<div class="sf-tape-row" data-anchor="file-1"><code data-slot="file-1"></code></div>'
    '<div class="sf-tape-row" data-anchor="file-2"><code data-slot="file-2"></code></div>'
    '<div class="sf-badges"><span class="sf-badge" data-anchor="no-token">no "secret" in the file</span>'
    '<span class="sf-badge is-warn" data-anchor="whole">the bodies are kept whole: read them before you commit</span></div>'
    "</div>"
)
KEY_STAGE = f"""<div class="sf-stage">
  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-panels is-two-requests">
    <section class="sf-panel is-program is-wrap" data-panel="recorded">
      <div class="sf-head"><span>recorded with</span><span>token secret</span></div>
      {request("L", "secret", "application/json")}
    </section>
    <section class="sf-panel is-program is-wrap" data-panel="replayed">
      <div class="sf-head"><span>replayed with</span><span>token ci-dummy</span></div>
      {request("R", "ci-dummy", "text/html")}
    </section>
  </div>
  <div class="sf-panels is-rings-and-code">
    <section class="sf-panel is-handler is-wrap" data-panel="handlers">
      <div class="sf-head"><span>the key</span><span>from on_disk.py</span></div>
      <div class="sf-code">
        {code(KEY_CODE)}
      </div>
    </section>
    <div>
      {KEY_FILE}
      <div class="sf-edge" data-anchor="network"><span>the network: <code>offline</code> · a miss raises</span></div>
    </div>
  </div>
  <div class="sf-token is-effect" data-token="lookup">MemoExists(key)</div>
  <div class="sf-token is-value" data-token="no">False</div>
  <div class="sf-token is-fail" data-token="error">LookupError</div>
  <div class="sf-token is-effect" data-token="lookup2">MemoExists(key)</div>
  <div class="sf-token is-value" data-token="yes">True, then MemoGet</div>
  <div class="sf-token is-value" data-token="body">{SERVER_BODY}</div>
</div>"""

HASHED = {
    "file": "releases.json, the default key",
    "file-1": f"{KEY_SECRET} → …",
    "file-2": f"{KEY_CLIENT} → …",
}
KEYED_ROW_1 = f"GET {SERVER} → {SERVER_BODY}"
KEYED_ROW_2 = f"GET https://api.github.com/repos/acme/client/releases/latest → {CLIENT_BODY}"
KEYED = {"file": "keyed.json, method_and_url", "file-1": KEYED_ROW_1, "file-2": KEYED_ROW_2}
NO_ACCEPT = {"L-accept": "is-off", "R-accept": "is-off"}
LEFT_OUT = {**NO_ACCEPT, "L-auth": "is-dead", "R-auth": "is-dead"}
KEY_STEPS = [
    step(
        "Same method and URL. Only the token in the header differs.",
        now=["L-auth", "R-auth"],
        marks=NO_ACCEPT,
        values={
            "call": "the request of acme/server, recorded and then replayed",
            "result": "",
            "L-key": "",
            "R-key": "",
            "file": "",
            "file-1": "",
            "file-2": "",
        },
    ),
    step(
        "The default key hashes every field, the headers too. So the keys differ.",
        active="handlers",
        now=["c-memo", "L-method", "L-url", "L-auth", "R-method", "R-url", "R-auth"],
        marks={**NO_ACCEPT, "L-key": "is-on", "R-key": "is-on is-fail"},
        values={"L-key": KEY_SECRET, "R-key": KEY_DUMMY},
    ),
    step(
        "The replay's key is not in the file, and <code>offline</code> raises <code>LookupError</code>.",
        active="replayed",
        now=["file"],
        marks={**NO_ACCEPT, "L-key": "is-on", "R-key": "is-on is-fail", "network": "is-fail"},
        values={
            **HASHED,
            "result": "LookupError: not recorded: GET .../acme/server/releases/latest",
        },
        tokens=in_turn(
            {
                "lookup": {"from": end("R-key"), **end("file")},
                "no": {"from": end("file"), **end("R-key")},
                "error": {"from": end("network"), **end("R-key")},
            }
        ),
    ),
    step(
        "<code>method_and_url</code> leaves the headers out, so both get the same key.",
        active="handlers",
        now=["c-mu-def", "c-mu-return", "L-method", "L-url", "R-method", "R-url"],
        marks={**LEFT_OUT, "L-key": "is-on is-same", "R-key": "is-on is-same"},
        values={"L-key": f"GET {SERVER}", "R-key": f"GET {SERVER}", "result": "", **KEYED},
    ),
    step(
        "Recorded with <code>secret</code>, replayed with <code>ci-dummy</code>: both tags come back.",
        active="replayed",
        now=["file-1"],
        marks={**LEFT_OUT, "L-key": "is-on is-same", "R-key": "is-on is-same"},
        values={"result": TAGS},
        tokens=in_turn(
            {
                "lookup2": {"from": end("R-key"), **end("file")},
                "yes": {"from": end("file"), **end("R-key")},
                "body": {"from": end("file-1"), **end("R-key"), "merge": True},
            }
        ),
    ),
    step(
        "The token is not in the file. The bodies are kept whole: read them before you commit.",
        now=["file-1", "file-2"],
        marks={
            **LEFT_OUT,
            "L-key": "is-on is-same",
            "R-key": "is-on is-same",
            "no-token": "is-on",
            "whole": "is-on",
        },
    ),
    step(
        "The key cannot tell apart what it leaves out. If answers depend on <code>Accept</code>, put it in.",
        now=["L-accept", "R-accept", "c-mu-return"],
        marks={
            "L-auth": "is-dead",
            "R-auth": "is-dead",
            "L-key": "is-on is-same",
            "R-key": "is-on is-same",
        },
        values={"result": "one key for both: the HTML request gets the JSON body"},
    ),
]

# --- five-limits: the stack of on_disk.py, from the inside out memo, storage and the network, one limit per step ---------

LIMIT_CORE = (
    '<div class="sf-core" data-anchor="core">'
    '<div class="sf-core-head"><span>latest_releases(REPOS, "secret")</span><span>the program</span></div>'
    "</div>"
)
LIMIT_RINGS = ring(
    "network",
    'network: <span data-slot="network"></span> · sent: <span data-slot="sent"></span>',
    ring(
        "storage",
        "json_tape(tape) · storage",
        ring("memo", "make_memo_rewriter(HttpRequest)", LIMIT_CORE),
    ),
)


def limit(n: int, title: str, rows: list[str]) -> str:
    """The box of one limit: what it is, and the lines that show it."""
    body = "".join(line(f"l{n}-{i}", text) for i, text in enumerate(rows, 1))
    return (
        f'<div class="sf-aside" data-anchor="limit-{n}"><div class="sf-head"><span>{n}. {title}</span></div>'
        f'<div class="sf-code">{body}</div></div>'
    )


LIMITS = [
    limit(
        1,
        "JSON holds text",
        [
            f"the file keeps: {st(chr(39) + SERVER_BODY + chr(39))}  {co('# a body, a string')}",
            f"a response object: Response(status=200, headers={{…}}, body=…)  {co('# needs a storage that encodes it')}",
        ],
    ),
    limit(
        2,
        "the default key is a hash of a pickle",
        [
            f"Python 3.13: pickle protocol 4 → one key  {co('# made here')}",
            f"Python 3.14: pickle protocol 5 by default → another key  {co('# not found here')}",
        ],
    ),
    limit(
        3,
        "no storage, no error",
        [
            f"github, twice: sent 2 → 4  {co('# every request a miss, silently')}",
            f"offline: {st(NOT_RECORDED_SERVER)}",
        ],
    ),
    limit(
        4,
        "every HttpRequest is recorded, POST too",
        [
            f"HttpRequest({st(chr(34) + 'POST' + chr(34))}, …)  {co('# recorded, and not sent on replay')}",
            f"{kw('if')} effect.method != {GET}: {kw('return')} ({kw('yield')} Pass(effect, k))  "
            f"{co('# in a recorder like record')}",
        ],
    ),
    limit(
        5,
        "a replay shows your code, not the API",
        [
            f"the tape: {st(chr(39) + SERVER_BODY + chr(39))}  {co('# what the API answered then')}",
            f"the API today: {co('# may answer otherwise; record again when it changes')}",
        ],
    ),
]
LIMIT_STAGE = f"""<div class="sf-stage">
  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-panels is-rings-and-code">
    <div class="sf-rings">
      {LIMIT_RINGS}
    </div>
    <section class="sf-panel is-handler is-wrap" data-panel="limits">
      <div class="sf-head"><span>the limits</span><span>from the section Limits</span></div>
      {"".join(LIMITS)}
    </section>
  </div>
  <div class="sf-token is-value" data-token="text">a string</div>
  <div class="sf-token is-fail" data-token="object">Response(…)</div>
  <div class="sf-token is-fail" data-token="other">another key on 3.14</div>
  <div class="sf-token is-effect" data-token="miss">HttpRequest(GET …)</div>
  <div class="sf-token is-fail" data-token="error">LookupError</div>
  <div class="sf-token is-effect" data-token="post">HttpRequest(POST …)</div>
  <div class="sf-token is-value" data-token="old">{SERVER_BODY}</div>
</div>"""

LIMIT_STEPS = [
    step(
        "<code>json_tape</code> keeps text. An answer that is an object needs a storage that encodes it.",
        active="limits",
        now=["l1-1", "l1-2"],
        marks={"limit-1": "is-on", "m-storage": "is-on"},
        values={
            "call": "recorded(latest_releases(REPOS, …), tape, network)",
            "result": "",
            "network": "github",
            "sent": "0",
        },
        tokens=in_turn(
            {
                "text": {"from": end("n-network"), **end("n-storage")},
                "object": {"from": end("n-network"), "at": "m-storage", "drop": True},
            }
        ),
    ),
    step(
        "The default key hashes a pickle, so a key made on 3.13 is not found on 3.14.",
        active="limits",
        now=["l2-1", "l2-2", "n-memo"],
        marks={"limit-2": "is-on", "m-memo": "is-on"},
        tokens={"other": {"from": end("n-memo"), "at": "m-memo"}},
    ),
    step(
        "With no storage, every request is a miss, silently. <code>offline</code> makes the miss raise.",
        active="limits",
        now=["l3-1", "l3-2"],
        marks={"limit-3": "is-on", "r-storage": "is-off"},
        values={"sent": "2 → 4", "network": "github, then offline", "result": NOT_RECORDED_SERVER},
        tokens=in_turn(
            {
                "miss": {"from": end("n-memo"), **end("n-network")},
                "error": {"from": end("n-network"), **end("n-memo")},
            }
        ),
    ),
    step(
        "Every <code>HttpRequest</code> is recorded, POST too. Pass on the ones that must go out.",
        active="limits",
        now=["l4-1", "l4-2", "n-memo"],
        marks={"limit-4": "is-on", "m-memo": "is-on"},
        values={"sent": "0", "network": "offline", "result": ""},
        tokens={"post": {"from": end("n-memo"), "at": "m-memo"}},
    ),
    step(
        "A replay shows how your code reads the answers you kept. Record again when the API changes.",
        active="limits",
        now=["l5-1", "l5-2", "core"],
        marks={"limit-5": "is-on"},
        values={"result": TAGS},
        tokens={"old": {"from": end("n-storage"), **end("n-memo")}},
    ),
]

if __name__ == "__main__":
    write(
        "record-performs-outward",
        'The section "A recorder performs the effect again" of docs/articles/record-and-replay.md, one step per click: '
        "record yields each request on to github, keeps the answer in the tape and resumes k with it.\n"
        "     Written by tools/docs_site/figures_measure/record_and_replay_figures.py; the orders are what "
        "record_and_replay.py prints.",
        RECORD_STAGE,
        RECORD_STEPS,
    )
    write(
        "replay-throws-at-yield",
        'The section "A player answers from the tape" of docs/articles/record-and-replay.md, one step per click: replay '
        "answers from the tape without going out, and throws LookupError into k for a request it does not have.\n"
        "     Written by tools/docs_site/figures_measure/record_and_replay_figures.py; the orders are what "
        "record_and_replay.py prints.",
        REPLAY_STAGE,
        REPLAY_STEPS,
    )
    write(
        "what-the-key-keeps",
        'The section "What makes two requests the same" of docs/articles/record-and-replay.md, one step per click: the '
        "default key hashes the token too and misses, and method_and_url leaves the headers out and hits.\n"
        "     Written by tools/docs_site/figures_measure/record_and_replay_figures.py; the keys are what "
        "record_and_replay.py prints.",
        KEY_STAGE,
        KEY_STEPS,
    )
    write(
        "five-limits",
        'The section "Limits" of docs/articles/record-and-replay.md, one limit per click, on the stack of on_disk.py.\n'
        "     Written by tools/docs_site/figures_measure/record_and_replay_figures.py; the counts of limit 3 are what "
        "record_and_replay.py prints.",
        LIMIT_STAGE,
        LIMIT_STEPS,
    )
