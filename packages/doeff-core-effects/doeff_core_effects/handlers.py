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

from doeff_core_effects.effects import Ask, Get, Put, Tell, Try, Slog


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


