# doeff-jev

A handler that judges doeff-system-one's `Judge` effect with **TypeSafe's Jev**. It sits in the same position
(provider side) as doeff-openai does for doeff-llm.

The entry point `doeff_jev` exposes only handlers, handler factories, and the config type (doeff's ADR-DOE-API-001 R1):
`judge_stack`, `jev_handler`, `jev_memo_handler`, `journal_handler`, `durable_cache`, and `JevTarget`. The modules below are internal
(the ones whose names start with `_`) and callers do not read them.

| Part (internal) | Contents |
|---|---|
| `_target.py` | The single place that resolves the destination (URL, model, wire format, API key). The pure `resolve_target(env, read_text)`, and `target_from_process_environment()`, which `judge_stack` calls when no destination is passed |
| `_wire.py` | Mapping Judge to an HTTP request and an HTTP response to a Verdict (pure). Two formats: direct TypeSafe and Vercel AI Gateway |
| `_handlers/production.py` | `jev_handler(target)`: receives Judge and yields an `HttpRequest` (it does not call HTTP itself). `jev_memo_handler(target)`: wraps the answers of cacheable Judges in CacheGet / CachePut |
| `_handlers/journal.py` | The journal sink that writes `slog("jev_judge", ...)` to a file as one JSON line |
| `_wiring.py` | The single place that stacks handlers (`judge_stack`), and the cross-process cache backend (`durable_cache`) |

## Switching the destination

```
JEV_BASE_URL=http://<host>:8646/v1/systemone JEV_MODEL=<model>  # to a self-hosted Jev-compatible judge
JEV_WIRE=gateway                                                  # via Vercel (the shape existing callers use)
```

For destinations other than TypeSafe and Vercel, requests are sent even without a key. The TypeSafe key is read from `TYPESAFE_API_KEY` ->
`~/.config/jev/api_key`, and the Vercel key from `AI_GATEWAY_API_KEY` -> `~/jev_key`.

## Usage (assembly point)

A program only emits the `Judge` effect; the assembly point wraps it with the `judge_stack` stack and runs it with doeff's `run`.

```python
from doeff import do, run, with_handlers
from doeff_core_effects import scheduled
from doeff_system_one import Judge, Question
from doeff_jev import durable_cache, judge_stack

@do
def is_japanese():
    return (yield Judge(state="Is this text Japanese?",
                        questions={"ja": Question(kind="noul", instructions="Is this text written in Japanese?")}))

stack = judge_stack(            # If no destination is passed, it is resolved from this process's environment and home files (see "Switching the destination" above)
    cache=durable_cache(),      # If omitted, the cache lives only within the process
    caller="my-hook",
)
verdict = run(scheduled(with_handlers(stack, is_japanese())))
```

To fix and pass a destination explicitly, use `judge_stack(JevTarget(base_url, model, wire, api_key, source), ...)`.
In tests, pass `http=` a handler that answers HttpRequest from a script (`tests/test_handler.py`).

## Journal

One line per judgment is written to `~/.local/state/doeff-jev/journal.log` (destination, model, caller `caller`, the list of question keys `keys`, number of questions,
tokens, elapsed ms, success or failure, the relay's self-identification `proxy`, and the local cache hit `cached`). Comparisons between Jev and a self-hosted judge are measured with this journal.
At assembly points that do not stack the journal, the same line passes out to the outer slog sink (stderr, etc.).

## Caller name and relay self-identification

`caller` (an argument of `judge_stack` / `jev_handler`) is put in the journal line and also sent to the destination in the request header `x-jev-caller`
(the form is `<component>/<event or rule>`, e.g. `rulebook-advisor/Stop`, `doeff-linter/DOEFF205`, `messaging/classification`).
It is not put in the body (so the answer key of the jev-proxy relay does not change). The origin of an answer that the relay declares in the response header `x-jev-proxy`
(hit / miss / coalesced / refreshed) is recorded in `Verdict.proxy`, and a judgment answered by the local cache is marked `Verdict.cached = True`
(`input_tokens` and `elapsed_ms` keep the values from when it was asked; exclude cached ones when counting cost).
