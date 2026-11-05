"""Production handler: maps `Judge` to an HttpRequest and sends it to TypeSafe (or a destination with the same contract).

This module does **not** call HTTP. It only yields an HttpRequest, and the stacked
`http_production_handler` (doeff-core-effects, httpx) does the real I/O. Tests stack a handler that answers HttpRequest
from a script instead.

The answer cache is `jev_memo_handler`: it wraps cacheable Judges in CacheExists / CacheGet / CachePut
(sqlite_cache_handler / in_memory_cache_handler are the backends). The key is determined by the destination, model, state, and questions.
The journal is one slog("jev_judge", ...) line (the journal handler writes it to a file; if not stacked, it passes out to the outer slog sink).
Line fields = host, model, ok, keys (the list of question names), caller (the caller name), questions, tokens, ms, proxy (the relay's self-identification),
cached (a local cache hit); a failure line has error and status.

Once the handler has returned its answer it is done, so it hands off to the continuation with Transfer / TransferThrow (with Resume,
the handler's frame would survive until the end of the continuation; this is a doeff warning).
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from doeff_core_effects import CacheExistsEffect, HttpRequest, cache_get, cache_put, slog
from doeff_system_one import Judge, JudgeError, Verdict

from doeff import Err, Ok, Pass, Transfer, TransferThrow, Try, do
from doeff import handler as _program_handler
from doeff_jev._target import JevTarget, key_required
from doeff_jev._wire import Prepared, cache_key, parse, prepare, proxy_outcome

#: Retries of one HTTP call (5xx / communication failure). A judgment is a short question, so only once.
HTTP_RETRIES = 1


@do
def _post(prepared: Prepared, timeout_seconds: float) -> Any:
    response = yield HttpRequest(method="POST", url=prepared.url, headers=dict(prepared.headers),
                                 body=dict(prepared.body), timeout_seconds=timeout_seconds,
                                 max_retries=HTTP_RETRIES)
    return response


def _journal(target: JevTarget, effect: Judge, caller: str, **fields: Any) -> Any:
    """One slog("jev_judge") line. The destination, the question keys (the list of question names), and the caller name (if non-empty) go on every line."""
    if caller:
        fields["caller"] = caller
    return slog("jev_judge", host=target.host, keys=list(effect.questions), **fields)


def jev_handler(target: JevTarget, *, caller: str = "") -> Callable[[Any], Any]:
    """Handler (Program -> Program) that judges `Judge` against the destination `target`. Other effects are Pass.

    caller = the caller name (e.g. `rulebook-advisor/Stop`). If non-empty, sent to the destination in the x-jev-caller header and put in the jev_judge line.
    The answer's Verdict.proxy is the relay's self-identification (the x-jev-proxy header).
    """

    @do
    def handler(effect: Any, k: Any) -> Any:
        if not isinstance(effect, Judge):
            return (yield Pass(effect, k))
        if key_required(target) and not target.api_key:
            error = JudgeError("no_credentials", "no api key for " + target.host)
            yield _journal(target, effect, caller, model=target.model, ok=False, error=error.detail)
            return (yield TransferThrow(k, error))
        prepared = prepare(target, effect, caller=caller)
        started = time.monotonic()
        outcome = yield Try(_post(prepared, effect.timeout_seconds))
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if isinstance(outcome, Err):
            error = JudgeError("transport", f"{type(outcome.error).__name__}: {str(outcome.error)[:120]}")
            yield _journal(target, effect, caller, model=target.model, ok=False,
                           ms=elapsed_ms, error=error.detail)
            return (yield TransferThrow(k, error))
        if not isinstance(outcome, Ok):
            error = JudgeError("transport", f"unexpected Try outcome {type(outcome).__name__}")
            return (yield TransferThrow(k, error))
        response = outcome.value
        try:
            verdict = parse(target, effect, response.status, response.text, elapsed_ms=elapsed_ms,
                            headers=response.headers)
        except JudgeError as error:
            yield _journal(target, effect, caller, model=target.model, ok=False,
                           status=response.status, ms=elapsed_ms, error=error.detail,
                           proxy=proxy_outcome(response.headers))
            return (yield TransferThrow(k, error))
        yield _journal(target, effect, caller, model=verdict.model, ok=True,
                       questions=len(effect.questions), tokens=verdict.input_tokens, ms=elapsed_ms,
                       proxy=verdict.proxy, cached=False)
        return (yield Transfer(k, verdict))

    return _program_handler(handler)


def jev_memo_handler(target: JevTarget, *, caller: str = "") -> Callable[[Any], Any]:
    """Handler that wraps the answers of cacheable `Judge`s in a cache. On a hit the destination is not called.

    The cache backend (the handler that handles CacheExists / CacheGet / CachePut) is stacked **outside** this handler.
    CacheGet throws KeyError for a missing key, so check with Exists first (same shape as core's memo).
    A hit answer is returned with Verdict.cached set to true (proxy is None: this judgment did not call the relay).
    caller = the caller name to put in the jev_judge line (pass the same value as for jev_handler).
    """

    @do
    def handler(effect: Any, k: Any) -> Any:
        if not isinstance(effect, Judge) or not effect.cacheable:
            return (yield Pass(effect, k))
        key = cache_key(target, effect)
        cached: Any = None
        if (yield CacheExistsEffect(key)):
            try:
                cached = yield cache_get(key)
            except KeyError:
                cached = None  # disappeared between Exists and Get
        if isinstance(cached, Verdict):
            yield _journal(target, effect, caller, model=cached.model, ok=True, cached=True,
                           questions=len(effect.questions), tokens=0, ms=0)
            return (yield Transfer(k, replace(cached, cached=True, proxy=None)))
        verdict = yield effect
        yield cache_put(key, verdict)
        return (yield Transfer(k, verdict))

    return _program_handler(handler)


def verdict_fields(verdict: Verdict) -> Mapping[str, Any]:
    """A plain dict for logging and display."""
    return {name: {"kind": a.kind, "value": a.value, "confidence": a.confidence}
            for name, a in verdict.answers.items()}
