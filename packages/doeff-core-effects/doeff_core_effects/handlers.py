"""
Core handlers — reader, state, writer.

Each is a function that takes config and returns a @do handler function.
Use with WithHandler:

    run(WithHandler(reader(env={"key": "value"}),
        WithHandler(state(initial={"count": 0}),
            body())))
"""

from doeff import do
from doeff.program import Resume, Pass

from doeff_core_effects.effects import (
    Ask, Get, Put, Tell, Try, Slog, WriterTellEffect,
    Local, Listen, Await,
)


def reader(env=None):
    """Reader handler: resolves Ask(key) from env dict.

    Args:
        env: dict mapping keys to values. Default empty.
    """
    if env is None:
        env = {}

    @do
    def handler(effect, k):
        if isinstance(effect, Ask):
            if effect.key in env:
                result = yield Resume(k, env[effect.key])
                return result
            from doeff.program import ResumeThrow
            return (yield ResumeThrow(k, KeyError(f"Ask: key not found: {effect.key!r}")))
        yield Pass(effect, k)

    return handler


def state(initial=None):
    """State handler: resolves Get(key) and Put(key, value) from mutable dict.

    Args:
        initial: dict of initial state. Default empty.
    """
    store = dict(initial) if initial else {}

    @do
    def handler(effect, k):
        if isinstance(effect, Get):
            result = yield Resume(k, store.get(effect.key))
            return result
        elif isinstance(effect, Put):
            store[effect.key] = effect.value
            result = yield Resume(k, None)
            return result
        yield Pass(effect, k)

    return handler


def writer():
    """Writer handler: collects Tell(message) into a log list.

    The log is returned as the handler's result when the body completes.
    Access via the handler's return value, or inspect handler_log after run.
    """
    log = []

    @do
    def handler(effect, k):
        if isinstance(effect, Tell):
            log.append(effect.message)
            result = yield Resume(k, None)
            return result
        yield Pass(effect, k)

    handler.log = log  # expose for inspection
    return handler


def try_handler():
    """Try handler: catches errors from Try(program) and returns Ok/Err.

    Usage:
        result = yield Try(some_program)  # Ok(value) or Err(error)
    """
    from doeff_vm import Ok, Err

    @do
    def handler(effect, k):
        if isinstance(effect, Try):
            @do
            def attempt():
                try:
                    value = yield effect.program
                    return Ok(value)
                except Exception as e:
                    return Err(e)
            result = yield Resume(k, (yield attempt()))
            return result
        yield Pass(effect, k)

    return handler


def slog_handler():
    """Structured log handler: collects Slog messages.

    Returns a handler with a .log attribute containing collected entries.
    Each entry is a dict with 'msg' and all kwargs.
    """
    log = []

    @do
    def handler(effect, k):
        if isinstance(effect, Slog):
            entry = {"msg": effect.msg, **effect.kwargs}
            log.append(entry)
            result = yield Resume(k, None)
            return result
        yield Pass(effect, k)

    handler.log = log
    return handler


def local_handler():
    """Local handler: handles Local(env, program) by nesting reader with merged env."""
    from doeff.program import WithHandler

    @do
    def handler(effect, k):
        if isinstance(effect, Local):
            # Get current env from enclosing reader, merge with local overrides
            # Run program under new reader with merged env
            inner_result = yield WithHandler(reader(env=effect.env), effect.program)
            result = yield Resume(k, inner_result)
            return result
        yield Pass(effect, k)

    return handler


def listen_handler():
    """Listen handler: collects effects of specified types during program execution."""

    @do
    def handler(effect, k):
        if isinstance(effect, Listen):
            collected = []
            types_to_collect = effect.types or (WriterTellEffect,)

            @do
            def observer_handler(inner_effect, inner_k):
                if isinstance(inner_effect, tuple(types_to_collect)):
                    collected.append(inner_effect)
                yield Pass(inner_effect, inner_k)

            from doeff.program import WithHandler
            inner_result = yield WithHandler(observer_handler, effect.program)
            result = yield Resume(k, (inner_result, collected))
            return result
        yield Pass(effect, k)

    return handler


def await_handler():
    """Await handler: runs async coroutines via a background thread with asyncio.

    Uses ExternalPromise to bridge async into the scheduler.
    Requires scheduler to be installed.
    """
    from doeff_core_effects.scheduler import CreateExternalPromise, Wait
    import asyncio
    import threading

    # Shared event loop running in a background thread
    _loop = [None]
    _lock = threading.Lock()

    def _get_loop():
        with _lock:
            if _loop[0] is None or _loop[0].is_closed():
                loop = asyncio.new_event_loop()
                t = threading.Thread(target=loop.run_forever, daemon=True)
                t.start()
                _loop[0] = loop
            return _loop[0]

    @do
    def handler(effect, k):
        if isinstance(effect, Await):
            ep = yield CreateExternalPromise()
            loop = _get_loop()

            async def run_coro():
                try:
                    result = await effect.coroutine
                    ep.complete(result)
                except Exception as e:
                    ep.fail(e)

            asyncio.run_coroutine_threadsafe(run_coro(), loop)
            value = yield Wait(ep.future)
            result = yield Resume(k, value)
            return result
        yield Pass(effect, k)

    return handler


