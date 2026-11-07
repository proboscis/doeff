"""Writes the three click-through figures of docs/articles/yield-and-async.md (docs/assets/figures/<name>.html):
await-passes-through (where an await goes), yield-nearest-handler (where a yield goes) and await-per-step (where the Awaits of
an async library go). The code in the figures is the article's (examples/yield_and_async), and the orders and counts are the
ones yield_and_async.py in this directory prints. A figure is written here, not by hand, because a dozen JSON states are easy to
get wrong by hand.

Usage (from the repository root): uv run --no-project python tools/docs_site/figures_measure/yield_and_async_figures.py
"""

from __future__ import annotations

import json
from pathlib import Path

FIGURES = Path(__file__).resolve().parents[3] / "docs" / "assets" / "figures"


def step(caption: str, **state: object) -> str:
    """One <li> of .sf-steps: the caption, and the state as JSON in a single-quoted attribute."""
    data = json.dumps(state, ensure_ascii=False).replace("&", "&amp;").replace("'", "&#39;")
    return f"  <li data-state='{data}'>{caption}</li>"


def line(anchor: str | None, code: str) -> str:
    """One line of code; an anchor lets a step light it up or put a token at its end."""
    named = f' data-anchor="{anchor}"' if anchor else ""
    return f'<div class="sf-line"{named}>{code}</div>'


# The colours of code, as the classes of step-figure.css: a keyword, a function's name, a string and a comment.
def kw(word: str) -> str:
    return f'<span class="kw">{word}</span>'


def fn(name: str) -> str:
    return f'<span class="fn">{name}</span>'


def st(text: str) -> str:
    return f'<span class="st">{text}</span>'


def co(text: str) -> str:
    return f'<span class="co">{text}</span>'


# Strings of the code that sit inside f-strings below (Python 3.11 cannot reuse an f-string's quote inside it).
HELLO = st('f"Hello, {name}"')
DR_NAME = st('f"Dr. {name}"')
EMPTY = st('""')
NL = "\n"
ADA = st('"Ada"')
GRACE = st('"Grace"')
CHUNKS = ", ".join(st(chunk) for chunk in ('"Hello"', '","', '""', '" world"'))


def write(name: str, comment: str, stage: str, steps: list[str]) -> None:
    """docs/assets/figures/<name>.html: a comment that says where the figure comes from, the stage and the steps."""
    body = f'<!-- {comment} -->\n{stage}\n<ol class="sf-steps">\n' + "\n".join(steps) + "\n</ol>\n"
    (FIGURES / f"{name}.html").write_text(body, encoding="utf-8")


# --- await-passes-through: drive(twice_async(), ...) and then asyncio's Task in drive's place -----------------------------

DRIVE = [
    line("d-def", f"{kw('def')} {fn('drive')}(coroutine, answers):"),
    line(None, "    value = None"),
    line(None, f"    {kw('while')} True:"),
    line(None, f"        {kw('try')}:"),
    line("d-send", "            request = coroutine.send(value)"),
    line("d-stop", f"        {kw('except')} StopIteration {kw('as')} stop:"),
    line("d-return", f"            {kw('return')} stop.value"),
    line("d-answer", "        value = answers[request.key]"),
]
TASK = [
    line(None, co("# asyncio's Task, in outline")),
    line("k-send", "result = coroutine.send(None)"),
    line("k-future", f"{kw('if')} isinstance(result, Future):  {co('# wait for it')}"),
    line("k-callback", "    result.add_done_callback(wakeup)  " + co("# then send(None)")),
    line(
        "k-none", f"{kw('elif')} result {kw('is')} None:  {co('# a bare yield: the others first')}"
    ),
    line(None, "    loop.call_soon(step)"),
    line(None, f"{kw('else')}:"),
    line("k-bad", "    error = RuntimeError(" + st('f"Task got bad yield: {result!r}"') + ")"),
]
TWICE_ASYNC = [
    line("t-def", kw("async def") + " " + fn("twice_async") + "():"),
    line(
        "t-call",
        "    "
        + kw("return")
        + " ("
        + kw("await")
        + " greet_async(), "
        + kw("await")
        + " greet_async())",
    ),
]
GREET_ASYNC = [
    line("g-def", kw("async def") + " " + fn("greet_async") + "():"),
    line("g-ask", "    name = " + kw("await") + ' <span data-slot="awaited"></span>'),
    line("g-return", "    " + kw("return") + " " + HELLO),
    line("g-note", '<span class="co" data-slot="note"></span>'),
]
AWAIT_STAGE = f"""<div class="sf-stage">
  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-chain">
    <section class="sf-panel is-handler" data-panel="drive">
      <div class="sf-head"><span data-slot="driver"></span><span>calls send(), and gets what comes up</span></div>
      <div class="sf-vars"><span>value = <span data-slot="value"></span></span></div>
      <div class="sf-split">
        <div>
          <div class="sf-code" data-anchor="drive-code">
            {(NL + " " * 12).join(DRIVE)}
          </div>
          <div class="sf-code sf-alt" data-anchor="task-code">
            {(NL + " " * 12).join(TASK)}
          </div>
        </div>
        <div class="sf-inbox is-one"><div class="sf-slot"><span>request</span><div data-anchor="in-request"></div></div></div>
      </div>
    </section>
    <section class="sf-panel is-program" data-panel="twice">
      <div class="sf-head"><span>twice_async</span><span>one call down from drive</span></div>
      <div class="sf-vars"><span>returns <span data-slot="pair"></span></span></div>
      <div class="sf-code">
        {(NL + " " * 8).join(TWICE_ASYNC)}
      </div>
    </section>
    <section class="sf-panel is-program" data-panel="greet">
      <div class="sf-head"><span>greet_async</span><span>two calls down from drive</span></div>
      <div class="sf-vars"><span>name = <span data-slot="name"></span></span></div>
      <div class="sf-code">
        {(NL + " " * 8).join(GREET_ASYNC)}
      </div>
    </section>
  </div>
  <div class="sf-token is-effect" data-token="ask">Ask("name")</div>
  <div class="sf-token is-effect" data-token="future">a Future</div>
  <div class="sf-token is-value" data-token="answer">"Ada"</div>
  <div class="sf-token is-value" data-token="stop">StopIteration</div>
  <div class="sf-token is-fail" data-token="bad">RuntimeError</div>
  <div class="sf-token is-value" data-token="wake">send(None)</div>
</div>"""

UP = {"via": [{"at": "t-call", "place": "end"}], "at": "in-request"}
DOWN = {"via": [{"at": "t-call", "place": "end"}], "at": "g-ask", "place": "end"}
G_ASK = {"at": "g-ask", "place": "end"}
TASK_ON = {"drive-code": "is-off", "task-code": "is-on"}

AWAIT_STEPS = [
    step(
        "<code>drive</code> starts <code>twice_async</code>. <code>await greet_async()</code> is a plain call: the child runs "
        "as part of the caller.",
        active="twice",
        now=["d-send", "t-call"],
        values={
            "call": 'drive(twice_async(), {"name": "Ada"})',
            "result": "",
            "driver": "drive, in drive.py",
            "value": "None",
            "pair": "",
            "name": "",
            "awaited": 'Ask("name")',
            "note": "    # Ask.__await__ is: return (yield self)",
        },
    ),
    step(
        '<code>greet_async</code> awaits <code>Ask("name")</code>, whose <code>__await__</code> yields the <code>Ask</code> '
        "itself.",
        active="greet",
        now=["g-ask", "g-note"],
        tokens={"ask": G_ASK},
    ),
    step(
        "<code>await</code> passes it straight through <code>twice_async</code>, out to whoever called <code>send()</code>: "
        "<code>drive</code>.",
        active="drive",
        now=["d-send"],
        tokens={"ask": {"from": G_ASK, **UP}},
    ),
    step(
        '<code>drive</code> answers with <code>send("Ada")</code>, and the value goes straight back to the <code>await</code> '
        "that asked.",
        active="greet",
        now=["d-answer", "d-send", "g-ask"],
        values={"value": '"Ada"', "name": '"Ada"'},
        tokens={"answer": {"from": {"at": "d-answer", "place": "end"}, **DOWN, "merge": True}},
    ),
    step(
        "The first call returns <code>'Hello, Ada'</code>, and the second call asks again. <code>twice_async</code> never "
        "sees either request.",
        active="drive",
        now=["g-ask", "d-send"],
        values={"pair": "('Hello, Ada', …)", "name": ""},
        tokens={"ask": {"from": G_ASK, **UP}},
    ),
    step(
        "<code>drive</code> answers it the same way. The coroutine ends with <code>StopIteration</code>, and "
        "<code>drive</code> returns its value.",
        active="drive",
        now=["d-stop", "d-return"],
        values={
            "pair": "('Hello, Ada', 'Hello, Ada')",
            "name": '"Ada"',
            "result": "('Hello, Ada', 'Hello, Ada')",
        },
        tokens={
            "answer": {"from": {"at": "d-answer", "place": "end"}, **DOWN, "merge": True},
            "stop": {
                "from": {"at": "t-call", "place": "end"},
                "at": "d-stop",
                "place": "end",
                "delay": 1.2,
            },
        },
    ),
    step(
        "Put asyncio's <code>Task</code> in <code>drive</code>'s place. It understands a <code>Future</code> and a bare "
        "<code>yield</code>. Anything else is a bad yield: <code>RuntimeError: Task got bad yield: Ask(key='name')</code>.",
        active="drive",
        now=["k-send", "k-bad"],
        marks={**TASK_ON, "result": "is-fail", "k-bad": "is-fail"},
        values={
            "call": "asyncio.run(twice_async())",
            "result": "RuntimeError: Task got bad yield",
            "driver": "asyncio's Task, under asyncio.run",
            "value": "None",
            "pair": "",
            "name": "",
        },
        tokens={
            "ask": {"from": G_ASK, **UP},
            "bad": {"from": "in-request", "at": "k-bad", "place": "end", "delay": 1.3},
        },
    ),
    step(
        "At the bottom of every real chain is a <code>Future</code>: <code>asyncio.sleep(1)</code> awaits one. The "
        "<code>Task</code> adds a callback and calls <code>send(None)</code> once it is done. One receiver at the top "
        "answers every request in the program.",
        active="greet",
        now=["k-future", "k-callback", "g-ask"],
        marks=TASK_ON,
        values={
            "result": "",
            "awaited": "asyncio.sleep(1)",
            "note": "    # sleep(1) awaits a Future, which yields itself",
        },
        tokens={
            "future": {"from": G_ASK, **UP},
            "wake": {
                "from": {"at": "k-callback", "place": "end"},
                **DOWN,
                "merge": True,
                "delay": 1.4,
            },
        },
    ),
]

# --- yield-nearest-handler: run(ada(titled(twice()))), then run(ada(twice())) ----------------------------------------------


def callee(name: str, title: str) -> str:
    """One call of greet inside twice: its two lines, with name as a slot."""
    return (
        f'<div class="sf-callee" data-anchor="{name}">'
        f'<div class="sf-callee-head">{title}</div>'
        f'<div class="sf-vars"><span>name = <span data-slot="{name}-name"></span></span></div>'
        '<div class="sf-code">'
        + line(f"{name}-yield", f"name = {kw('yield')} ReadName()")
        + line(f"{name}-return", f"{kw('return')} {HELLO}")
        + "</div></div>"
    )


def ring(name: str, label: str, inside: str, extra: str = "") -> str:
    """A handler drawn around what it handles; the room under its name is where it keeps k."""
    classes = f"sf-ring {extra}".strip()
    return (
        f'<div class="{classes}" data-anchor="r-{name}"><span class="sf-ring-name" data-anchor="n-{name}">{label}</span>'
        f'<div class="sf-ring-room" data-anchor="m-{name}"></div>{inside}</div>'
    )


TWICE_CORE = (
    '<div class="sf-core" data-anchor="core">'
    '<div class="sf-core-head"><span>twice()</span><span class="sf-unchanged" data-anchor="unchanged">greet: unchanged</span></div>'
    '<div class="sf-vars"><span>first = <span data-slot="first"></span></span>'
    '<span>second = <span data-slot="second"></span></span></div>'
    '<div class="sf-code">'
    + line(None, kw("@do"))
    + line(None, f"{kw('def')} {fn('twice')}():")
    + line("t-first", f"    first = {kw('yield')} greet()")
    + line("t-second", f"    second = {kw('yield')} grace(greet())")
    + line("t-return", f"    {kw('return')} first, second")
    + "</div>"
    '<div class="sf-pair">'
    + callee("g1", "greet(), first call")
    + ring("grace", "grace", callee("g2", "greet(), second call"))
    + "</div></div>"
)
HANDLERS = [
    line("h-ada-def", f"{kw('def')} {fn('ada')}(effect: ReadName, k):"),
    line("h-ada", f"    {kw('return')} ({kw('yield')} Resume(k, {ADA}))"),
    line(None, ""),
    line("h-titled-def", f"{kw('def')} {fn('titled')}(effect: ReadName, k):"),
    line("h-titled-yield", f"    name = {kw('yield')} effect"),
    line("h-titled-resume", f"    {kw('return')} ({kw('yield')} Resume(k, {DR_NAME}))"),
    line(None, ""),
    line("h-grace-def", f"{kw('def')} {fn('grace')}(effect: ReadName, k):"),
    line("h-grace", f"    {kw('return')} ({kw('yield')} Resume(k, {GRACE}))"),
]
NEAREST_STAGE = f"""<div class="sf-stage">
  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-panels is-rings-and-code">
    <div class="sf-rings">
      {ring("ada", "ada", ring("titled", "titled", TWICE_CORE))}
    </div>
    <section class="sf-panel is-handler" data-panel="handlers">
      <div class="sf-head"><span>the handlers</span><span>each one is a @handler</span></div>
      <div class="sf-code">
        {(NL + " " * 8).join(HANDLERS)}
      </div>
    </section>
  </div>
  <div class="sf-token is-effect" data-token="read">ReadName()</div>
  <div class="sf-token is-k" data-token="k">k <small>the rest of greet</small></div>
  <div class="sf-token is-value" data-token="ada">"Ada"</div>
  <div class="sf-token is-value" data-token="dr">"Dr. Ada"</div>
  <div class="sf-token is-effect" data-token="read2">ReadName()</div>
  <div class="sf-token is-k" data-token="k2">k <small>the rest of greet</small></div>
  <div class="sf-token is-value" data-token="grace">"Grace"</div>
  <div class="sf-token is-value" data-token="out">('Hello, Dr. Ada', 'Hello, Grace')</div>
</div>"""


def end(anchor: str) -> dict[str, str]:
    """A token's place just past the end of an anchor's text."""
    return {"at": anchor, "place": "end"}


NO_GRACE = {"r-grace": "is-off"}
NEAREST_STEPS = [
    step(
        "<code>yield greet()</code> runs the child as part of <code>twice</code>, like <code>await greet_async()</code>.",
        now=["t-first", "g1-yield", "g1-return"],
        marks=NO_GRACE,
        values={
            "call": "run(ada(titled(twice())))",
            "result": "",
            "first": "",
            "second": "",
            "g1-name": "",
            "g2-name": "",
        },
    ),
    step(
        "<code>greet</code> pauses at <code>yield ReadName()</code>. The rest of <code>greet</code> is now a value: "
        "<code>k</code>.",
        now=["g1-yield"],
        marks={**NO_GRACE, "g1-return": "is-k"},
        tokens={"read": end("g1-yield"), "k": end("g1-return")},
    ),
    step(
        "The nearest handler of <code>ReadName</code> is <code>titled</code>, so it receives the effect and <code>k</code>.",
        active="handlers",
        now=["h-titled-def"],
        marks={**NO_GRACE, "g1-return": "is-k", "m-titled": "is-on"},
        tokens={
            "read": {"from": end("g1-yield"), **end("n-titled")},
            "k": {"from": end("g1-return"), "at": "m-titled"},
        },
    ),
    step(
        "<code>titled</code> performs the effect again. It goes on out, to <code>ada</code>.",
        active="handlers",
        now=["h-titled-yield"],
        marks={**NO_GRACE, "g1-return": "is-k", "m-titled": "is-on"},
        tokens={"read": {"from": end("n-titled"), **end("n-ada")}, "k": {"at": "m-titled"}},
    ),
    step(
        '<code>ada</code> answers <code>"Ada"</code> to <code>titled</code>, the code that asked it. <code>greet</code> is '
        "still waiting.",
        active="handlers",
        now=["h-ada"],
        marks={**NO_GRACE, "g1-return": "is-k", "m-titled": "is-on"},
        tokens={"ada": {"from": end("n-ada"), **end("n-titled")}, "k": {"at": "m-titled"}},
    ),
    step(
        "<code>titled</code> resumes <code>k</code> with <code>Dr. </code> in front. <code>greet</code> returns "
        '<code>"Hello, Dr. Ada"</code>.',
        active="handlers",
        now=["h-titled-resume", "g1-return"],
        marks=NO_GRACE,
        values={"g1-name": '"Dr. Ada"', "first": "'Hello, Dr. Ada'"},
        tokens={
            "dr": {"from": end("n-titled"), **end("g1-yield"), "merge": True},
            "k": {"from": "m-titled", **end("g1-yield"), "merge": True, "delay": 0.3},
        },
    ),
    step(
        "This call alone runs with <code>grace</code> around it, so its <code>ReadName</code> stops at <code>grace</code> "
        "and never reaches <code>titled</code>.",
        now=["t-second", "g2-yield"],
        marks={"r-grace": "is-new", "m-grace": "is-on", "g2-return": "is-k"},
        tokens={
            "read2": {"from": end("g2-yield"), **end("n-grace")},
            "k2": {"from": end("g2-return"), "at": "m-grace"},
        },
    ),
    step(
        '<code>grace</code> resumes <code>k</code> with <code>"Grace"</code>, and <code>twice</code> returns. Each '
        "handler's <code>Resume</code> returns what the rest of the program returned: <code>grace</code>'s first, then "
        "<code>titled</code>'s and <code>ada</code>'s: <code>('Hello, Dr. Ada', 'Hello, Grace')</code>.",
        active="handlers",
        now=["h-grace", "t-return"],
        values={
            "g2-name": '"Grace"',
            "second": "'Hello, Grace'",
            "result": "('Hello, Dr. Ada', 'Hello, Grace')",
        },
        tokens={
            "grace": {"from": end("n-grace"), **end("g2-yield"), "merge": True},
            "k2": {"from": "m-grace", **end("g2-yield"), "merge": True, "delay": 0.3},
            "out": {
                "from": end("t-return"),
                "via": [end("n-titled"), end("n-ada")],
                "at": "result",
                "merge": True,
                "delay": 1.1,
            },
        },
    ),
    step(
        "Take <code>titled</code> out, and the first greeting is plain Ada: <code>('Hello, Ada', 'Hello, Grace')</code>. "
        "<code>greet</code> is the same function in every run.",
        now=["g1-yield", "g1-return", "g2-yield", "g2-return"],
        marks={"r-titled": "is-off", "unchanged": "is-on"},
        values={
            "call": "run(ada(twice()))",
            "result": "('Hello, Ada', 'Hello, Grace')",
            "g1-name": '"Ada"',
            "first": "'Hello, Ada'",
        },
    ),
]

# --- await-per-step: Listen around collect, then around collect_in_one -----------------------------------------------------

LOOP = [
    line("l-def", f"{kw('async def')} {fn('tokens')}():"),
    line("l-for", f"    {kw('for')} chunk {kw('in')} ({CHUNKS}):"),
    line("l-sleep", f"        {kw('await')} asyncio.sleep(0.01)"),
    line("l-yield", f"        {kw('yield')} chunk"),
]
LOOP_IN_ONE = [
    line("lc-def", f"{kw('async def')} {fn('collect_async')}(stream):"),
    line(None, f"    text = {EMPTY}"),
    line(
        "lc-for",
        f"    {kw('async for')} chunk {kw('in')} stream:  {co('# the four waits of tokens() happen here')}",
    ),
    line("lc-add", "        text += chunk"),
    line(None, f"    {kw('return')} text"),
]
COLLECT = [
    line(None, kw("@do")),
    line(None, f"{kw('def')} {fn('collect')}(stream):"),
    line(None, "    chunks = aiter(stream)"),
    line(None, f"    text = {EMPTY}"),
    line(None, f"    {kw('while')} True:"),
    line("c-await", f"        chunk = {kw('yield')} Await(anext(chunks, None))"),
    line("c-none", f"        {kw('if')} chunk {kw('is')} None:"),
    line("c-return", f"            {kw('return')} text"),
    line("c-add", "        text += chunk"),
]
COLLECT_IN_ONE = [
    line(None, kw("@do")),
    line(None, f"{kw('def')} {fn('collect_in_one')}(stream):"),
    line("ci-await", f"    {kw('return')} ({kw('yield')} Await(collect_async(stream)))"),
]
CORE = (
    '<div class="sf-core" data-anchor="core">'
    '<div class="sf-core-head"><span data-slot="core"></span><span>the program</span></div>'
    '<div class="sf-vars"><span>text = <span data-slot="text"></span></span>'
    '<span>chunk = <span data-slot="chunk"></span></span></div>'
    f'<div class="sf-code" data-anchor="c-code">{"".join(COLLECT)}</div>'
    f'<div class="sf-code sf-alt" data-anchor="ci-code">{"".join(COLLECT_IN_ONE)}</div>'
    "</div>"
)
LOOP_BOX = (
    '<div class="sf-loop" data-anchor="loop">'
    '<div class="sf-loop-head" data-anchor="loop-head">a background asyncio loop, behind await_handler</div>'
    f'<div class="sf-code" data-anchor="l-code">{"".join(LOOP)}</div>'
    f'<div class="sf-code sf-alt" data-anchor="lc-code">{"".join(LOOP_IN_ONE)}</div>'
    "</div>"
)
LISTEN_NAME = 'listen_handler · Awaits it wrote down: <span data-slot="count"></span>'
MORE = ring(
    "more", "whatever else you stack: a log · a clock · a budget · a recording", CORE, "is-faint"
)
PER_STEP_STAGE = f"""<div class="sf-stage">
  <div class="sf-call">
    <code data-slot="call"></code>
    <span class="sf-result is-text" data-anchor="result"><span data-slot="result"></span></span>
  </div>
  <div class="sf-rings">
    {ring("sched", "scheduled", ring("await", "await_handler", LOOP_BOX + ring("listen", LISTEN_NAME, MORE)))}
  </div>
  <div class="sf-token is-effect" data-token="a1">Await</div>
  <div class="sf-token is-effect" data-token="a2">Await</div>
  <div class="sf-token is-effect" data-token="a3">Await</div>
  <div class="sf-token is-effect" data-token="a4">Await</div>
  <div class="sf-token is-effect" data-token="a5">Await</div>
  <div class="sf-token is-value" data-token="hello">"Hello"</div>
  <div class="sf-token is-value" data-token="none">None</div>
  <div class="sf-token is-value" data-token="inside">4 chunks, inside the loop</div>
</div>"""

OUT = [end("n-listen"), end("n-await")]


def await_out(start: str, delay: float = 0.0) -> dict[str, object]:
    """An Await's trip: from the program out past listen_handler, which writes it down, to await_handler and its loop."""
    return {"from": end(start), "via": OUT, "at": "loop-head", "place": "end", "delay": delay}


NOT_MORE = {"r-more": "is-off"}
IN_ONE = {
    **NOT_MORE,
    "c-code": "is-off",
    "ci-code": "is-on",
    "l-code": "is-off",
    "lc-code": "is-on",
}
PER_STEP_STEPS = [
    step(
        "Three handlers around one program. <code>Listen</code> asks to hear every <code>Await</code> that "
        "<code>collect</code> yields.",
        marks=NOT_MORE,
        values={
            "call": "run(bridged(counted(collect(tokens()))))",
            "result": "",
            "core": "collect(tokens())",
            "count": "0",
            "text": '""',
            "chunk": "",
        },
    ),
    step(
        "<code>collect</code> yields <code>Await(anext(chunks, None))</code>. <code>listen_handler</code> writes the "
        "<code>Await</code> down and passes it on.",
        now=["c-await"],
        marks=NOT_MORE,
        values={"count": "1"},
        tokens={"a1": {"from": end("c-await"), "via": [end("n-listen")], **end("n-await")}},
    ),
    step(
        "<code>await_handler</code> runs the coroutine on a background asyncio loop and resumes <code>k</code> with the "
        "chunk.",
        now=["l-sleep", "l-yield", "c-add"],
        marks=NOT_MORE,
        values={"text": '"Hello"', "chunk": '"Hello"'},
        tokens={
            "a1": {"from": end("n-await"), **end("loop-head"), "merge": True},
            "hello": {"from": end("l-yield"), **end("c-await"), "merge": True, "delay": 0.9},
        },
    ),
    step(
        'Each chunk is one <code>Await</code>, so every handler around <code>collect</code> sees it. <code>""</code> is a '
        "chunk too.",
        now=["c-await", "c-add"],
        marks=NOT_MORE,
        values={"count": "4", "text": '"Hello, world"', "chunk": '" world"'},
        tokens={
            "a2": {**await_out("c-await"), "merge": True},
            "a3": {**await_out("c-await", 0.8), "merge": True},
            "a4": {**await_out("c-await", 1.6), "merge": True},
        },
    ),
    step(
        "The fifth <code>Await</code> gives <code>None</code>, the end. <code>Listen</code> counted five: "
        "<code>'Hello, world' 5</code>.",
        now=["c-none", "c-return"],
        marks=NOT_MORE,
        values={"count": "5", "chunk": "None", "result": "'Hello, world' 5"},
        tokens={
            "a5": {**await_out("c-await"), "merge": True},
            "none": {"from": end("loop-head"), **end("c-await"), "merge": True, "delay": 1.9},
        },
    ),
    step(
        "Put the whole loop in one coroutine, and the handlers see one <code>Await</code>: <code>'Hello, world' 1</code>. "
        "The waits inside stay in asyncio's loop.",
        now=["ci-await", "lc-for", "lc-add"],
        marks=IN_ONE,
        values={
            "call": "run(bridged(counted(collect_in_one(tokens()))))",
            "core": "collect_in_one(tokens())",
            "count": "1",
            "text": "",
            "chunk": "",
            "result": "'Hello, world' 1",
        },
        tokens={
            "a1": {**await_out("ci-await"), "merge": True},
            "inside": {"from": end("lc-for"), **end("lc-add"), "delay": 1.9},
        },
    ),
    step(
        "Keep the steps in <code>@do</code> code, and whatever you stack around the program sees each one: a log, a clock, "
        "a budget, or a recording that a test replays.",
        now=["c-await"],
        marks={"r-more": "is-new"},
        values={
            "call": "collect(tokens()), with more handlers around it",
            "core": "collect(tokens())",
            "count": "5",
            "text": '"Hello, world"',
            "chunk": "None",
            "result": "",
        },
        tokens={
            name: {"from": end("c-await"), **end("n-more"), "merge": True, "delay": 0.4 * n}
            for n, name in enumerate(("a1", "a2", "a3", "a4", "a5"))
        },
    ),
]

if __name__ == "__main__":
    write(
        "await-passes-through",
        'The section "Where an await goes" of docs/articles/yield-and-async.md, one step per click: every request that '
        "crosses an await goes through twice_async to the one code that calls send(), drive or asyncio's Task.\n"
        "     Written by tools/docs_site/figures_measure/yield_and_async_figures.py; the order is what yield_and_async.py "
        "prints.",
        AWAIT_STAGE,
        AWAIT_STEPS,
    )
    write(
        "yield-nearest-handler",
        'The section "Where a yield goes" of docs/articles/yield-and-async.md, one step per click: run(ada(titled(twice()))), '
        "where each ReadName goes to the nearest handler around the code that yields it.\n"
        "     Written by tools/docs_site/figures_measure/yield_and_async_figures.py; the order is what yield_and_async.py "
        "prints.",
        NEAREST_STAGE,
        NEAREST_STEPS,
    )
    write(
        "await-per-step",
        'The section "I/O through an async library" of docs/articles/yield-and-async.md, one step per click: with one Await '
        "per chunk, Listen hears five; with the loop in one coroutine, one.\n"
        "     Written by tools/docs_site/figures_measure/yield_and_async_figures.py; the counts are what yield_and_async.py "
        "prints.",
        PER_STEP_STAGE,
        PER_STEP_STEPS,
    )
