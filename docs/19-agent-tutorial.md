# Tutorial: Building an Automated Code Review System

> **Needs components outside `pip install doeff`.** This page uses `doeff-agents`. PyPI has only
> an old `doeff-agents` 0.1.1 build, which does not import with doeff 0.5.0, so install it from a
> checkout of this repository together with the repository packages it depends on
> (`doeff-claude-code`, `doeff-codex`). pip takes everything else, `doeff-time` included, from PyPI:

```bash
git clone --depth 1 https://github.com/proboscis/doeff.git
pip install doeff doeff-time ./doeff/packages/doeff-claude-code \
  ./doeff/packages/doeff-codex ./doeff/packages/doeff-agents
```

This tutorial builds an automated code reviewer: a program that asks Claude Code to review a file
and gets the review back as structured data. After reading it you can:

- write the work with an agent as a doeff Program, a `@do` function that yields `doeff-agents`
  effects;
- run that Program under a handler of your own that answers the effects from a script, with no
  agent, no terminal and no session host;
- run the same Program, unchanged, under the production handler that talks to the session host.

[![A narrated video, about three minutes, of this page's steps: review_file as a doeff Program that yields LaunchSession, AwaitResult, FollowUp and StopSession; scripted_agent, a handler that resumes the Program with scripted outcomes and logs each effect; the agent asking, timing out and returning a result off the schema, with the session stopped every time; review_all over three files under scheduled, try_handler and scripted_agent; and the same Programs under scenario_agent_handler and the production daemon_agent_handlers](assets/videos/agent-tutorial.gif)](assets/videos/agent-tutorial.mp4)

The steps up to "Run it against Claude Code" run as they are, with only the install above. That
last step needs a machine with `tmux`, Claude Code and a session host.

## What We'll Build

| Piece | What it is | Where it comes from |
| --- | --- | --- |
| `review_file(path)` | a Program: starts a session for one file, waits for its review, stops the session | you write it (Step 1) |
| `review_all(paths, at_most)` | a Program: reviews several files, at most `at_most` sessions at a time | you write it (Step 4) |
| `scripted_agent(outcomes, log)` | a test handler: answers the session effects from a script and records them | you write it (Step 2) |
| `scenario_agent_handler(...)` | a test handler: the same job, packaged | `doeff_agents` (Step 5) |
| `daemon_agent_handlers()` | the production handlers: send the effects to the session host, which runs Claude Code | `doeff_agents` (Step 6) |

## The Effects a Review Uses

A Program does not start Claude Code itself. It yields an effect, a value that says what it needs,
and the handler installed around the Program decides how that need is met. A review needs four
effects:

| The Program yields | It asks for | The handler resumes it with |
| --- | --- | --- |
| `LaunchSession(spec)` | a session that runs `spec.prompt` in `spec.work_dir` | a `SessionHandle` |
| `AwaitResult(handle, timeout_seconds=...)` | the result of the session, or the reason there is none | an `AwaitOutcome` |
| `FollowUp(handle, message)` | `message` sent into the running session | the same `SessionHandle` |
| `StopSession(handle)` | the session stopped | `None` |

`spec` is an `AgentSpec`. Its `result_schema` is a JSON Schema: the agent returns its result
through doeff-agents, and the handler checks it against the schema before the Program sees it.
The Program never reads the terminal or a file to get the result.

An `AwaitOutcome` has these fields:

| Field | Meaning |
| --- | --- |
| `status` | `AwaitStatus.EXITED` (the agent finished), `AwaitStatus.AWAITING_INPUT` (it stopped to ask something) or `AwaitStatus.TIMED_OUT` (nothing came back within `timeout_seconds`) |
| `result` | the result, valid against `result_schema`; `None` when there is none |
| `validation_error` | why the result the agent gave was not valid; `None` when it was valid or absent |

`AwaitResult` waits in one step. The waiting (watching the session, checking the result) is the
handler's job, so the Program has no polling loop and no `sleep`.

## Step 1: The Review as a Program

`review_file` yields the four effects. `StopSession` is in a `finally` block, so the session is
stopped whether the review succeeds or fails. Save it as `review.py`:

```python file=review.py
from pathlib import Path

from doeff import do
from doeff_agents import (
    AgentSpec,
    AgentType,
    AwaitResult,
    AwaitStatus,
    FollowUp,
    LaunchSession,
    StopSession,
)

REVIEW_SCHEMA = {
    "type": "object",
    "required": ["verdict", "issues"],
    "properties": {
        "verdict": {"enum": ["approve", "request-changes"]},
        "issues": {"type": "array", "items": {"type": "string"}},
    },
}


class ReviewFailed(Exception):
    """The session ended without a valid review."""


def review_spec(path: str) -> AgentSpec:
    return AgentSpec(
        run_id="review",
        node_id=path,
        attempt=1,
        agent_type=AgentType.CLAUDE,
        work_dir=Path.cwd(),
        prompt=f"Review {path}: list potential bugs and readability problems.",
        result_schema=REVIEW_SCHEMA,
    )


@do
def review_file(path: str):
    session = yield LaunchSession(review_spec(path))
    try:
        outcome = yield AwaitResult(session, timeout_seconds=600)
        if outcome.status is AwaitStatus.AWAITING_INPUT:
            yield FollowUp(session, "Decide on your own and finish the review.")
            outcome = yield AwaitResult(session, timeout_seconds=600)
        if outcome.result is None or outcome.validation_error is not None:
            reason = outcome.validation_error or outcome.status.value
            raise ReviewFailed(f"{path}: {reason}")
        return outcome.result
    finally:
        yield StopSession(session)
```

The session id comes from `run_id`, `node_id` and `attempt`, so a handler that is asked to launch
the same spec again can find the session it already started instead of starting a second one:

```python
from review import review_spec

print(review_spec("src/utils.py").session_id)  # review-src-utils.py-1
```

Calling `review_file("src/utils.py")` only builds the Program. Nothing runs until a handler
answers its effects.

## Step 2: A Test Handler

A handler is a function of the effect and the continuation `k` (the rest of the Program), made one
with `@handler`. It answers an effect with `yield Resume(k, value)`: the Program goes on with
`value` as the result of its `yield`.

`scripted_agent` answers the four effects from a script. `outcomes` maps a session id to the
`AwaitOutcome`s its `AwaitResult`s get, in order, and `log` records each effect it answers. Save it
as `scripted.py`:

```python file=scripted.py
from doeff import Resume, handler
from doeff_agents import (
    AwaitResultEffect,
    FollowUpEffect,
    LaunchSessionEffect,
    SessionHandle,
    StopSessionEffect,
)

SessionEffect = LaunchSessionEffect | AwaitResultEffect | FollowUpEffect | StopSessionEffect


def scripted_agent(outcomes, log):
    pending = {session_id: list(steps) for session_id, steps in outcomes.items()}

    @handler
    def answer(effect: SessionEffect, k):
        if isinstance(effect, LaunchSessionEffect):
            log.append(f"launch {effect.spec.session_id}")
            return (yield Resume(k, SessionHandle(effect.spec.session_id)))
        session_id = effect.handle.session_id
        if isinstance(effect, AwaitResultEffect):
            outcome = pending[session_id].pop(0)
            log.append(f"await {session_id}: {outcome.status.value}")
            return (yield Resume(k, outcome))
        if isinstance(effect, FollowUpEffect):
            log.append(f"follow-up {session_id}")
            return (yield Resume(k, effect.handle))
        log.append(f"stop {session_id}")
        return (yield Resume(k, None))

    return answer
```

The annotation `effect: SessionEffect` tells doeff which effects this handler takes. Any other
effect goes past it to the next handler out.

Run a review under it. The agent in the script finishes and returns a valid review:

```python
from doeff import run
from doeff_agents import AwaitOutcome, AwaitStatus

from review import review_file
from scripted import scripted_agent

approve = AwaitOutcome(status=AwaitStatus.EXITED, result={"verdict": "approve", "issues": []})

log = []
agent = scripted_agent({"review-src-utils.py-1": [approve]}, log)
print(run(agent(review_file("src/utils.py"))))  # {'verdict': 'approve', 'issues': []}
print(log)  # ['launch review-src-utils.py-1', 'await review-src-utils.py-1: exited', 'stop review-src-utils.py-1']
```

`review_file` did not change for the test. The test chose the handler.

## Step 3: When the Agent Does Not Finish

The script can play the other outcomes. Add this below the example above.

The agent stops to ask a question, gets a follow-up, and then finishes:

```python continues
asks = AwaitOutcome(status=AwaitStatus.AWAITING_INPUT)
log = []
agent = scripted_agent({"review-src-api.py-1": [asks, approve]}, log)
print(run(agent(review_file("src/api.py"))))  # {'verdict': 'approve', 'issues': []}
print(log[2])  # follow-up review-src-api.py-1
```

Nothing comes back within the timeout, or the agent returns a result that does not match the
schema. `review_file` raises `ReviewFailed`, and the `finally` block still stops the session:

```python continues
from review import ReviewFailed, review_spec

slow = AwaitOutcome(status=AwaitStatus.TIMED_OUT)
wrong = AwaitOutcome(
    status=AwaitStatus.EXITED,
    result={"verdict": "maybe", "issues": []},
    validation_error="'maybe' is not one of ['approve', 'request-changes']",
)


def failure(path, outcome):
    log = []
    agent = scripted_agent({review_spec(path).session_id: [outcome]}, log)
    try:
        run(agent(review_file(path)))
    except ReviewFailed as error:
        return str(error), log[-1]


print(failure("src/slow.py", slow))  # ('src/slow.py: timed-out', 'stop review-src-slow.py-1')
print(failure("src/odd.py", wrong))  # ("src/odd.py: 'maybe' is not one of ['approve', 'request-changes']", 'stop review-src-odd.py-1')
```

## Step 4: Reviewing Several Files at Once

`review_all` starts one task per file with `Spawn` and collects the results with `Gather`. A
semaphore lets at most `at_most` reviews hold a session at a time. `Try` turns a failed review into
an `Err` value, so one failure does not stop the others. Save it as `review_all.py`:

```python file=review_all.py
from doeff import do
from doeff_core_effects import Try
from doeff_core_effects import AcquireSemaphore, CreateSemaphore, Gather, ReleaseSemaphore, Spawn

from review import review_file


@do
def review_all(paths, at_most):
    sessions = yield CreateSemaphore(at_most)

    @do
    def one(path):
        yield AcquireSemaphore(sessions)
        try:
            return (yield review_file(path))
        finally:
            yield ReleaseSemaphore(sessions)

    tasks = []
    for path in paths:
        tasks.append((yield Spawn(Try(one(path)))))
    return (yield Gather(*tasks))
```

`Spawn`, `Gather` and the semaphore are answered by the scheduler, `scheduled`, and `Try` by
`try_handler`. The session effects still go to `scripted_agent`:

```python
from doeff import run
from doeff_agents import AwaitOutcome, AwaitStatus
from doeff_core_effects import try_handler
from doeff_core_effects import scheduled

from review_all import review_all
from scripted import scripted_agent

approve = AwaitOutcome(status=AwaitStatus.EXITED, result={"verdict": "approve", "issues": []})
slow = AwaitOutcome(status=AwaitStatus.TIMED_OUT)
outcomes = {
    "review-a.py-1": [approve],
    "review-b.py-1": [slow],
    "review-c.py-1": [approve],
}

log = []
program = try_handler(scripted_agent(outcomes, log)(review_all(["a.py", "b.py", "c.py"], 2)))
a, b, c = run(scheduled(program))
print(a)  # Ok({'verdict': 'approve', 'issues': []})
print(b)  # Err(ReviewFailed('b.py: timed-out'))
print(c)  # Ok({'verdict': 'approve', 'issues': []})
print([line for line in log if line.startswith("stop")])  # ['stop review-a.py-1', 'stop review-b.py-1', 'stop review-c.py-1']
```

The review of `b.py` failed, and its session was stopped all the same.

## Step 5: The Packaged Test Handler

`doeff_agents` ships a test handler of the same kind, made by `scenario_agent_handler`. Its script
maps a session id to `ScenarioStep`s, one per `AwaitResult`. Install it around a Program like any
handler:

```python
from doeff import run
from doeff_agents import ScenarioStep, scenario_agent_handler

from review import review_file

scenario = scenario_agent_handler(
    scripts={
        "review-src-utils.py-1": [
            ScenarioStep.success({"verdict": "request-changes", "issues": ["unused import"]}),
        ],
    },
)
print(run(scenario(review_file("src/utils.py"))))  # {'verdict': 'request-changes', 'issues': ['unused import']}
```

Use your own handler when the test needs to see each effect, as `scripted_agent` does with `log`.
Use `scenario_agent_handler` when the script of results is all the test needs.

## Step 6: Run It Against Claude Code

In production the effects go to the session host, the module `doeff_agents.sessionhost.host`. The
host owns the sessions: it starts Claude Code in a terminal, watches it, checks its result against
the schema and stops it, and a session lives on even when the program that launched it exits. This
step needs:

- `tmux` and the Claude Code CLI (`claude`), signed in;
- a session host running on the machine.

Start the host with the default paths, which the production handler also uses:

```bash
AGENTD_DB="${XDG_STATE_HOME:-$HOME/.local/state}/doeff/agentd.sqlite"
if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
  AGENTD_SOCKET="$XDG_RUNTIME_DIR/doeff/agentd.sock"
else
  AGENTD_SOCKET="/tmp/doeff-agentd-${USER:-unknown}.sock"
fi
python -m doeff_agents.sessionhost.host --db "$AGENTD_DB" --socket "$AGENTD_SOCKET" --max-running 10
```

Then install `daemon_agent_handlers()` around the same `review_all`, in place of `scripted_agent`.
It returns two handlers, a time handler and the agent handler; the time handler goes outside. Save
this as `code_reviewer.py` next to `review.py` and `review_all.py`:

```python excerpt
import sys

from doeff import run
from doeff_agents import daemon_agent_handlers
from doeff_core_effects import try_handler
from doeff_core_effects import scheduled

from review_all import review_all

time_handler, agent_handler = daemon_agent_handlers()
program = time_handler(agent_handler(try_handler(review_all(sys.argv[1:], 2))))
for result in run(scheduled(program)):
    print(result)
```

```bash
python code_reviewer.py src/main.py src/utils.py
```

Without a session host, `agent_effectful_handlers()` gives a tuple of the same shape that runs the
sessions in `tmux` from the calling process. The sessions then end when that process ends.

The Programs are the same in every step. Only the handlers changed:

| Run | Handlers around the Program |
| --- | --- |
| a test that sees every effect | `scripted_agent(outcomes, log)` |
| a test with a packaged script | `scenario_agent_handler(scripts=...)` |
| production, through the session host | `daemon_agent_handlers()` |
| production, from the calling process | `agent_effectful_handlers()` |

## Key Takeaways

1. **The Program says what it needs; the handler decides how.** `review_file` yields
   `LaunchSession`, `AwaitResult`, `FollowUp` and `StopSession` and does not know whether a script,
   `tmux` or the session host answers them.
2. **Waiting is the handler's job.** `AwaitResult` returns when the agent has finished, stopped to
   ask, or run out of time, so the Program has no polling loop and no `sleep`.
3. **The result is data checked against a schema.** `AgentSpec.result_schema` says what a review
   is; `AwaitOutcome.result` is valid against it or `None`, with the reason in `validation_error`.
4. **Clean-up is a `finally` block.** `yield StopSession(session)` in `finally` stops the session
   on success, on failure and on timeout.
5. **The handler gives the effects their meaning; the Program stays the same.** Under a handler of
   your own, or `scenario_agent_handler`, the same effects are answered without an agent, and
   `review_file` runs unchanged.

## Next Steps

- [What doeff is](positioning/what-doeff-is.md) explains effects, handlers and `Resume` in general.
- [Semaphore Effects](21-semaphore-effects.md) covers the semaphore used in Step 4.
- The [doeff-agents README](../packages/doeff-agents/README.md) covers the session host, its
  command line and the other handlers.
