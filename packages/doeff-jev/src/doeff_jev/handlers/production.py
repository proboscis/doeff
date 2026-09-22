"""本番の handler — `Judge` を HttpRequest に写して TypeSafe(または同じ契約の宛先)へ出す。

この module は HTTP を**呼ばない**。HttpRequest を yield するだけで、実 I/O は積まれた
`http_production_handler`(doeff-core-effects・httpx)が行う。テストは HttpRequest を台本で
答える handler を代わりに積む。

答えの cache は `jev_memo_handler`: cacheable な Judge を CacheExists / CacheGet / CachePut で包む
(sqlite_cache_handler / in_memory_cache_handler が実体)。鍵は宛先・model・state・問いで決まる。
記録は slog("jev_judge", …) 1 行(journal handler が file へ書く)。

handler は答えを返したら用が済むので Transfer / TransferThrow で続きへ渡す(Resume だと
handler の frame が続きの終わりまで生き残る — doeff の警告)。
"""

import time
from collections.abc import Callable, Mapping
from typing import Any

from doeff_core_effects import HttpRequest, slog
from doeff_core_effects.cache_effects import CacheExistsEffect, cache_get, cache_put
from doeff_system_one import Judge, JudgeError, Verdict
from doeff_vm import Err, Ok

from doeff import Pass, Transfer, TransferThrow, Try, do
from doeff import handler as _program_handler
from doeff_jev.target import JevTarget, key_required
from doeff_jev.wire import Prepared, cache_key, parse, prepare

#: 1 回の HTTP の再試行(5xx / 通信の失敗)。判定は短い問いなので 1 度だけ。
HTTP_RETRIES = 1


@do
def _post(prepared: Prepared, timeout_seconds: float) -> Any:
    response = yield HttpRequest(method="POST", url=prepared.url, headers=dict(prepared.headers),
                                 body=dict(prepared.body), timeout_seconds=timeout_seconds,
                                 max_retries=HTTP_RETRIES)
    return response


def jev_handler(target: JevTarget) -> Callable[[Any], Any]:
    """`Judge` を宛先 `target` で判定する handler(Program -> Program)。他の effect は Pass。"""

    @do
    def handler(effect: Any, k: Any) -> Any:
        if not isinstance(effect, Judge):
            return (yield Pass(effect, k))
        if key_required(target) and not target.api_key:
            error = JudgeError("no_credentials", "no api key for " + target.host)
            yield slog("jev_judge", host=target.host, model=target.model, ok=False, error=error.detail)
            return (yield TransferThrow(k, error))
        prepared = prepare(target, effect)
        started = time.monotonic()
        outcome = yield Try(_post(prepared, effect.timeout_seconds))
        elapsed_ms = int((time.monotonic() - started) * 1000)
        if isinstance(outcome, Err):
            error = JudgeError("transport", f"{type(outcome.error).__name__}: {str(outcome.error)[:120]}")
            yield slog("jev_judge", host=target.host, model=target.model, ok=False,
                       ms=elapsed_ms, error=error.detail)
            return (yield TransferThrow(k, error))
        if not isinstance(outcome, Ok):
            error = JudgeError("transport", f"unexpected Try outcome {type(outcome).__name__}")
            return (yield TransferThrow(k, error))
        response = outcome.value
        try:
            verdict = parse(target, effect, response.status, response.text, elapsed_ms=elapsed_ms)
        except JudgeError as error:
            yield slog("jev_judge", host=target.host, model=target.model, ok=False,
                       status=response.status, ms=elapsed_ms, error=error.detail)
            return (yield TransferThrow(k, error))
        yield slog("jev_judge", host=target.host, model=verdict.model, ok=True,
                   questions=len(effect.questions), tokens=verdict.input_tokens, ms=elapsed_ms)
        return (yield Transfer(k, verdict))

    return _program_handler(handler)


def jev_memo_handler(target: JevTarget) -> Callable[[Any], Any]:
    """cacheable な `Judge` の答えを cache で包む handler。当たれば宛先を呼ばない。

    cache の実体(CacheExists / CacheGet / CachePut を捌く handler)はこの handler の**外**に積む。
    CacheGet は無い鍵で KeyError を投げるので、Exists で先に確かめる(core の memo と同じ形)。
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
                cached = None  # Exists と Get の間に消えた
        if isinstance(cached, Verdict):
            yield slog("jev_judge", host=target.host, model=cached.model, ok=True, cached=True,
                       questions=len(effect.questions), tokens=0, ms=0)
            return (yield Transfer(k, cached))
        verdict = yield effect
        yield cache_put(key, verdict)
        return (yield Transfer(k, verdict))

    return _program_handler(handler)


def verdict_fields(verdict: Verdict) -> Mapping[str, Any]:
    """記録・表示用の素の辞書。"""
    return {name: {"kind": a.kind, "value": a.value, "confidence": a.confidence}
            for name, a in verdict.answers.items()}
