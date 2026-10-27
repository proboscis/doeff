"""本番の handler — `Judge` を HttpRequest に写して TypeSafe(または同じ契約の宛先)へ出す。

この module は HTTP を**呼ばない**。HttpRequest を yield するだけで、実 I/O は積まれた
`http_production_handler`(doeff-core-effects・httpx)が行う。テストは HttpRequest を台本で
答える handler を代わりに積む。

答えの cache は `jev_memo_handler`: cacheable な Judge を CacheExists / CacheGet / CachePut で包む
(sqlite_cache_handler / in_memory_cache_handler が実体)。鍵は宛先・model・state・問いで決まる。
記録は slog("jev_judge", …) 1 行(journal handler が file へ書く。積まなければ外の slog の出口へ抜ける)。
行の欄 = host・model・ok・keys(問いの名の列)・caller(呼び手の名)・questions・tokens・ms・proxy(中継の名乗り)・
cached(手元の cache の当たり)、失敗の行は error・status。

handler は答えを返したら用が済むので Transfer / TransferThrow で続きへ渡す(Resume だと
handler の frame が続きの終わりまで生き残る — doeff の警告)。
"""

import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from doeff_core_effects import HttpRequest, slog
from doeff_core_effects.cache_effects import CacheExistsEffect, cache_get, cache_put
from doeff_system_one import Judge, JudgeError, Verdict

from doeff import Err, Ok, Pass, Transfer, TransferThrow, Try, do
from doeff import handler as _program_handler
from doeff_jev.target import JevTarget, key_required
from doeff_jev.wire import Prepared, cache_key, parse, prepare, proxy_outcome

#: 1 回の HTTP の再試行(5xx / 通信の失敗)。判定は短い問いなので 1 度だけ。
HTTP_RETRIES = 1


@do
def _post(prepared: Prepared, timeout_seconds: float) -> Any:
    response = yield HttpRequest(method="POST", url=prepared.url, headers=dict(prepared.headers),
                                 body=dict(prepared.body), timeout_seconds=timeout_seconds,
                                 max_retries=HTTP_RETRIES)
    return response


def _journal(target: JevTarget, effect: Judge, caller: str, **fields: Any) -> Any:
    """slog("jev_judge") の 1 行。宛先・問いの key(問いの名の列)・呼び手の名(空でなければ)は全部の行に載せる。"""
    if caller:
        fields["caller"] = caller
    return slog("jev_judge", host=target.host, keys=list(effect.questions), **fields)


def jev_handler(target: JevTarget, *, caller: str = "") -> Callable[[Any], Any]:
    """`Judge` を宛先 `target` で判定する handler(Program -> Program)。他の effect は Pass。

    caller = 呼び手の名(例 `rulebook-advisor/Stop`)。空でなければ見出し x-jev-caller で宛先へ送り、jev_judge の行に載せる。
    答えの Verdict.proxy は中継の名乗り(見出し x-jev-proxy)。
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
    """cacheable な `Judge` の答えを cache で包む handler。当たれば宛先を呼ばない。

    cache の実体(CacheExists / CacheGet / CachePut を捌く handler)はこの handler の**外**に積む。
    CacheGet は無い鍵で KeyError を投げるので、Exists で先に確かめる(core の memo と同じ形)。
    当たった答えは Verdict.cached を真にして返す(proxy は None — この判定は中継を呼んでいない)。
    caller = jev_judge の行に載せる呼び手の名(jev_handler と同じ値を渡す)。
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
            yield _journal(target, effect, caller, model=cached.model, ok=True, cached=True,
                           questions=len(effect.questions), tokens=0, ms=0)
            return (yield Transfer(k, replace(cached, cached=True, proxy=None)))
        verdict = yield effect
        yield cache_put(key, verdict)
        return (yield Transfer(k, verdict))

    return _program_handler(handler)


def verdict_fields(verdict: Verdict) -> Mapping[str, Any]:
    """記録・表示用の素の辞書。"""
    return {name: {"kind": a.kind, "value": a.value, "confidence": a.confidence}
            for name, a in verdict.answers.items()}
