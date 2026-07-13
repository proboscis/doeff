"""
Core handlers — reader, state, writer.

Stateless handlers are pre-installed Program -> Program functions.
Parameterised handlers are factories that return Program -> Program installers.

Compose them by calling each handler with the program to wrap:

    prog = writer(state(initial={"count": 0})(body()))
    prog = reader(env={"key": "value"})(prog)
    run(prog)

writer and slog_handler use lazy state init via Get/Put + Some
(same pattern as Hy defhandler's ``lazy`` clause). They require
the ``state`` handler to be installed as an outer handler.
"""

import threading as _threading

from doeff import do
from doeff.program import Pass, Transfer, TransferThrow
from doeff.program import handler as _program_handler
from doeff_core_effects.effects import (
    Ask,
    Await,
    Get,
    Listen,
    Local,
    Put,
    Slog,
    Try,
    WriterTellEffect,
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
                return (yield Transfer(k, env[effect.key]))
            return (yield TransferThrow(k, KeyError(_missing_key_message(effect.key))))
        yield Pass(effect, k)

    return _program_handler(handler)


def state(initial=None):
    """State handler: resolves Get(key) and Put(key, value) from mutable dict.

    Args:
        initial: dict of initial state. Default empty.
    """
    store = dict(initial) if initial else {}

    @do
    def handler(effect, k):
        if isinstance(effect, Get):
            return (yield Transfer(k, store.get(effect.key)))
        elif isinstance(effect, Put):
            store[effect.key] = effect.value
            return (yield Transfer(k, None))
        yield Pass(effect, k)

    return _program_handler(handler)


_WRITER_LOG_KEY = "__doeff_writer_log__"


@do
def _writer_handler(effect, k):
    """Writer handler: collects Tell(message) into a log list.

    Uses lazy state init via Get/Put + Some (same pattern as Hy
    defhandler's ``lazy`` clause).  Requires the ``state`` handler
    to be installed as an outer handler.

    Retrieve the collected log with ``yield writer_log()``.
    """
    if isinstance(effect, WriterTellEffect):
        from doeff.result import Some

        cached = yield Get(_WRITER_LOG_KEY)
        if isinstance(cached, Some):
            log = cached.value
        else:
            log = []
            yield Put(_WRITER_LOG_KEY, Some(log))
        log.append(effect.msg)
        return (yield Transfer(k, None))
    yield Pass(effect, k)


writer = _program_handler(_writer_handler)
writer.__name__ = "writer"
writer.__qualname__ = "writer"


@do
def writer_log():
    """Return a snapshot of the current writer log from state.

    Requires state handler.  Returns an empty list if no Tell has
    been issued yet.  The returned list is a copy — mutations do not
    affect the handler's internal log.
    """
    from doeff.result import Some

    cached = yield Get(_WRITER_LOG_KEY)
    if isinstance(cached, Some):
        return list(cached.value)
    return []


@do
def _try_handler(effect, k):
    """Try handler: catches errors from Try(program) and returns Ok/Err.

    Captures inner handlers (between body and try_handler) via GetHandlers
    and reinstalls them around the Try program, so effects from the program
    can reach handlers at any position in the chain.

    Usage:
        try_handler(body)
        result = yield Try(some_program)  # Ok(value) or Err(error)
    """
    if isinstance(effect, Try):
        from doeff_vm import Err, Ok

        from doeff.handler_utils import get_inner_handlers

        inner_hs = yield get_inner_handlers(k)

        @do
        def attempt():
            prog = effect.program
            # Reinstall inner handlers + try_handler itself so nested
            # Try effects and inner-handler effects are reachable.
            for h in inner_hs:
                prog = _program_handler(h)(prog)
            prog = try_handler(prog)
            try:
                value = yield prog
                return Ok(value)
            except Exception as e:
                return Err(e)
        return (yield Transfer(k, (yield attempt())))
    yield Pass(effect, k)


try_handler = _program_handler(_try_handler)
try_handler.__name__ = "try_handler"
try_handler.__qualname__ = "try_handler"


def _format_slog_line(effect):
    """One-line plain-text rendering of a SlogEffect: `   LEVEL msg key=value`."""
    kwargs = dict(effect.kwargs)
    level = str(kwargs.pop("level", "info")).upper()
    parts = [f"{level:>8}", str(effect.msg)]
    parts.extend(f"{key}={value}" for key, value in kwargs.items())
    return " ".join(parts)


@do
def _slog_handler(effect, k):
    """Structured log sink: displays each SlogEffect on stderr and consumes it.

    Contract (ADR-DOE-CORE-EFFECTS-001 R2): installing this handler makes
    slog output visible — display is its ONLY job. It is the observability
    IO boundary; it collects nothing (no state, no side-channel).
    Capture flows as values via Listen(prog, types=(SlogEffect,));
    explicit silence is slog_discard_handler.
    (slog_log() is retired: with a display-only sink there is no slog
    state to read. Tell accumulation stays on writer + writer_log().)
    """
    import sys

    if isinstance(effect, Slog):
        print(_format_slog_line(effect), file=sys.stderr)
        return (yield Transfer(k, None))
    yield Pass(effect, k)


slog_handler = _program_handler(_slog_handler)
slog_handler.__name__ = "slog_handler"
slog_handler.__qualname__ = "slog_handler"


@do
def _slog_discard_handler(effect, k):
    """Silent sink for SlogEffect: consumes without display (explicit opt-in,
    ADR-DOE-CORE-EFFECTS-001 R5). For assertions, capture via
    Listen(prog, types=(SlogEffect,)) inside the program instead."""
    if isinstance(effect, Slog):
        return (yield Transfer(k, None))
    yield Pass(effect, k)


slog_discard_handler = _program_handler(_slog_discard_handler)
slog_discard_handler.__name__ = "slog_discard_handler"
slog_discard_handler.__qualname__ = "slog_discard_handler"


@do
def _local_handler(effect, k):
    """Local handler: scoped env override with pass-on-miss semantics.

    Installs a scope reader that handles Ask for overridden keys only,
    passing non-overridden keys through to outer handlers (reader, lazy_ask).

    OCaml 5 semantics: the inner program must run with the same handler
    chain that was in scope when Local was performed. We capture inner
    handlers from the continuation (like the scheduler does for Spawn)
    and reinstall them around the inner program.

    Usage:
        local_handler(body)
    """
    if isinstance(effect, Local):
        from doeff.handler_utils import get_inner_handlers
        overrides = effect.env

        # Capture inner handlers from continuation (between Local site
        # and this handler) so the inner program sees the same chain.
        inner_handlers = yield get_inner_handlers(k)

        @do
        def scope_reader(inner_effect, inner_k):
            if isinstance(inner_effect, Ask) and inner_effect.key in overrides:
                return (yield Transfer(inner_k, overrides[inner_effect.key]))
            yield Pass(inner_effect, inner_k)

        # Reinstall inner handlers + local_handler itself (for nested Locals),
        # then scope_reader innermost
        prog = effect.program
        for h in inner_handlers:
            prog = _program_handler(h)(prog)
        prog = local_handler(prog)
        prog = _program_handler(scope_reader)(prog)

        inner_result = yield prog
        return (yield Transfer(k, inner_result))
    yield Pass(effect, k)


local_handler = _program_handler(_local_handler)
local_handler.__name__ = "local_handler"
local_handler.__qualname__ = "local_handler"


@do
def _listen_handler(effect, k):
    """Listen handler: collects effects of specified types during program execution.

    OCaml 5 semantics: reinstall inner handlers so the inner program
    sees the same handler chain as when Listen was performed.

    Usage:
        listen_handler(body)
    """
    if isinstance(effect, Listen):
        from doeff.handler_utils import get_inner_handlers
        collected = []
        types_to_collect = effect.types or (WriterTellEffect,)

        inner_handlers = yield get_inner_handlers(k)

        @do
        def observer_handler(inner_effect, inner_k):
            if isinstance(inner_effect, tuple(types_to_collect)):
                collected.append(inner_effect)
            yield Pass(inner_effect, inner_k)

        prog = effect.program
        for h in inner_handlers:
            prog = _program_handler(h)(prog)
        prog = _program_handler(observer_handler)(prog)

        inner_result = yield prog
        return (yield Transfer(k, (inner_result, collected)))
    yield Pass(effect, k)


listen_handler = _program_handler(_listen_handler)
listen_handler.__name__ = "listen_handler"
listen_handler.__qualname__ = "listen_handler"


# --- Shared Await bridge loop (process-global singleton, issues #494/#498) ---
#
# One background asyncio loop + daemon thread for ALL await_handler instances,
# instead of one per instance (which leaked a thread + loop + fds per run).

_await_bridge_lock = _threading.Lock()
_await_bridge_state = {
    # (loop, thread) once created; replaced atomically under the lock.
    "bridge": None,
    # BaseException that killed the loop thread's run_forever, if any.
    "thread_error": None,
    "atexit_registered": False,
}


def _await_bridge_thread_main(loop):
    """Run the shared bridge loop, capturing whatever kills run_forever."""
    import asyncio

    asyncio.set_event_loop(loop)
    try:
        loop.run_forever()
    except BaseException as e:
        _await_bridge_state["thread_error"] = e
        raise


def _shutdown_await_bridge():
    """atexit hook: best-effort drain, stop and close the shared bridge loop.

    Cancels in-flight bridge coroutines (their ``ep.fail(CancelledError)``
    lands in an already-dead run queue and is ignored), stops the loop, joins
    the thread briefly, and closes the loop once run_forever has returned.
    """
    import asyncio

    with _await_bridge_lock:
        bridge = _await_bridge_state["bridge"]
        _await_bridge_state["bridge"] = None
    if bridge is None:
        return
    loop, thread = bridge
    if thread.is_alive() and not loop.is_closed():

        def _drain_and_stop():
            for task in asyncio.all_tasks():
                task.cancel()
            # Stop in the NEXT callback batch so the cancellations above get
            # one loop iteration to actually propagate into the coroutines.
            loop.call_soon(loop.stop)

        loop.call_soon_threadsafe(_drain_and_stop)
        thread.join(timeout=1.0)
    if not thread.is_alive() and not loop.is_closed():
        loop.close()


def _get_await_bridge_loop():
    """Return the process-global bridge loop, (re)creating it if needed.

    Double-checked locking. The bridge coroutine never lets an exception
    escape onto the loop, so the loop is expected to outlive the process;
    this replacement path is a backstop for external kills only (e.g. user
    code scheduled its own task on the loop and raised SystemExit, or a
    forked child inherited a bridge whose thread does not exist). It warns
    loudly, naming the killer exception, before starting a fresh loop.
    """
    import asyncio
    import atexit
    import warnings

    bridge = _await_bridge_state["bridge"]
    if bridge is not None:
        loop, thread = bridge
        if not loop.is_closed() and thread.is_alive():
            return loop
    with _await_bridge_lock:
        bridge = _await_bridge_state["bridge"]
        if bridge is not None:
            loop, thread = bridge
            if not loop.is_closed() and thread.is_alive():
                return loop
            # Previous loop is unusable — report loudly and replace it.
            killer = _await_bridge_state["thread_error"]
            warnings.warn(
                "doeff await bridge loop was unusable "
                f"(closed={loop.is_closed()}, thread_alive={thread.is_alive()}, "
                f"killed_by={killer!r}); starting a replacement loop",
                RuntimeWarning,
                stacklevel=2,
            )
            if not thread.is_alive() and not loop.is_closed():
                loop.close()  # reclaim the dead loop's fds
        new_loop = asyncio.new_event_loop()
        t = _threading.Thread(
            target=_await_bridge_thread_main,
            args=(new_loop,),
            name="doeff-await-bridge",
            daemon=True,
        )
        t.start()
        _await_bridge_state["bridge"] = (new_loop, t)
        _await_bridge_state["thread_error"] = None
        if not _await_bridge_state["atexit_registered"]:
            atexit.register(_shutdown_await_bridge)
            _await_bridge_state["atexit_registered"] = True
        return new_loop


def _observe_await_bridge_future(future):
    """Done-callback: observe the bridge future so a BaseException that
    escaped the bridge coroutine is never silently discarded (#494)."""
    import concurrent.futures
    import warnings

    try:
        exc = future.exception()
    except concurrent.futures.CancelledError:
        return
    if exc is not None:
        warnings.warn(
            f"doeff Await bridge coroutine terminated with {exc!r} "
            "(already reported to the waiting doeff task via ep.fail)",
            RuntimeWarning,
            stacklevel=2,
        )


def await_handler():
    """Await handler: runs async coroutines via a background thread with asyncio.

    Uses ExternalPromise to bridge async into the scheduler.
    Requires scheduler to be installed.

    All instances share one process-global event loop + daemon thread
    (#498); the loop is stopped/closed by an atexit hook. The bridge
    coroutine resolves its promise on EVERY exit, including BaseException
    such as asyncio.CancelledError (#494). ``ep.fail`` is the ONLY
    propagation channel: the scheduler's task wrapper catches only
    ``Exception``, so KeyboardInterrupt/SystemExit failed into the promise
    escape the scheduler and reach the ``run()`` caller's thread. They are
    deliberately NOT re-raised on the loop thread — that would kill the
    shared loop (asyncio re-raises them out of ``run_forever``), silently
    orphaning every other in-flight Await in the process, while gaining
    nothing: ``threading`` swallows SystemExit on daemon threads.

    Known limitation (#498): cancelling a doeff task does NOT cancel the
    in-flight bridged coroutine — it keeps running on the shared loop and
    its late completion is ignored. Fixing that requires scheduler-side
    cancel propagation to the run_coroutine_threadsafe future.

    Isolation trade-off of the shared loop: a bridged coroutine that blocks
    the loop (e.g. a synchronous call inside async code) now stalls every
    run's Awaits process-wide, not just its own run's.
    """
    import asyncio

    from doeff_core_effects.scheduler import CreateExternalPromise, Wait

    @do
    def handler(effect, k):
        if isinstance(effect, Await):
            ep = yield CreateExternalPromise()
            loop = _get_await_bridge_loop()

            async def run_coro():
                try:
                    result = await effect.coroutine
                except BaseException as e:
                    # Resolve the promise on EVERY exit — a swallowed
                    # BaseException (e.g. asyncio.CancelledError) would
                    # otherwise park the scheduler forever (#494).
                    # Never re-raise here, not even KeyboardInterrupt or
                    # SystemExit: asyncio would propagate it out of
                    # run_forever and kill the SHARED loop thread, silently
                    # hanging every other in-flight Await in the process.
                    # ep.fail already delivers it to the run() caller.
                    ep.fail(e)
                else:
                    ep.complete(result)

            fut = asyncio.run_coroutine_threadsafe(run_coro(), loop)
            fut.add_done_callback(_observe_await_bridge_future)
            value = yield Wait(ep.future)
            return (yield Transfer(k, value))
        yield Pass(effect, k)

    return _program_handler(handler)


def _missing_key_message(key):
    """Build an actionable error message for a missing Ask key."""
    return (
        f"Ask: key not found: {key!r}\n"
        f"\n"
        f"To provide this key, use one of:\n"
        f"  uv run doeff run --set {key}=VALUE ...          # inline key-value\n"
        f"  uv run doeff run --env myapp.module.env ...     # merge an env dict/Program[dict]\n"
        f"  uv run doeff run --interpreter myapp.interp ... # custom interpreter with env baked in\n"
        f"  uv run doeff run -c '...' --set {key}=VALUE     # with inline code (heredoc supported)\n"
        f"\n"
        f"Use {{import.path}} in --set to import a symbol: --set {key}={{myapp.impl}}\n"
        f"See: uv run doeff run --help"
    )


def lazy_ask(env=None, *, strict=False):  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
    """Lazy Ask handler — replaces reader per SPEC-EFF-001.

    Handles Ask, Local, and lazy program evaluation. Takes env directly —
    no separate reader handler needed.

    Ask resolution order:
    1. Local overrides (from active Local scopes)
    2. Base env (passed to lazy_ask())
    3. Miss behavior:
       - strict=False (default): ``Pass(effect, k)`` — delegate to outer
         handler so composition like ``(lazy-ask (env-var-ask ...))`` works.
       - strict=True: ``TransferThrow(KeyError)`` — legacy behavior when you
         want the handler to be authoritative and loud about misses.

    If the resolved value is a Program (any DoExpr node — Expand, Perform,
    Pure, etc.), it is evaluated lazily with caching. Concurrent asks for
    the same key coordinate via per-key semaphore.

    Local creates a new handler scope with merged env/overrides and an
    isolated scope cache for override-dependent entries.

    Cache isolation:
    - shared_cache: entries whose deps don't intersect any active override keys.
      Shared across all scopes and spawned tasks.
    - scope_cache: per-Local entries whose deps intersect override keys.
      Isolated per Local scope; tasks spawned inside the same scope share it.

    Requires scheduler (for semaphores) to be installed as an outer handler.
    """
    if env is None:
        env = {}

    from doeff import Program
    from doeff.handler_utils import get_inner_handlers
    from doeff_core_effects.scheduler import (
        AcquireSemaphore,
        CreateSemaphore,
        ReleaseSemaphore,
    )

    shared_cache = {}       # key → value (override-independent entries)
    shared_deps = {}        # key → frozenset of dep keys
    eval_stack = []         # stack of dep-tracking sets for nested evals
    sems = {}               # key → Semaphore handle

    def _make_handler(effective_env, override_keys=frozenset()):  # noqa: PLR0915 - baseline cleanup keeps existing control flow unchanged
        """Create a handler with the given effective env (base + overrides)."""
        scope_cache = {}    # key → value (override-dependent, isolated per scope)
        scope_deps = {}     # key → frozenset of dep keys

        def _cache_lookup(key):
            """Check scope cache, then shared cache (validated against overrides)."""
            if key in scope_cache:
                return scope_cache[key], scope_deps.get(key, frozenset())
            if key in shared_cache:
                deps = shared_deps.get(key, frozenset())
                if not (deps & override_keys):
                    return shared_cache[key], deps
            return None, None

        def _cache_store(key, value, deps):
            """Store in scope or shared cache based on override dependency."""
            if deps & override_keys:
                scope_cache[key] = value
                scope_deps[key] = deps
            else:
                shared_cache[key] = value
                shared_deps[key] = deps

        @do
        def handler(effect, k):  # noqa: PLR0911, PLR0912, PLR0915 - baseline cleanup keeps existing control flow unchanged
            if isinstance(effect, Ask):
                # Track as dependency if inside a lazy evaluation
                if eval_stack:
                    eval_stack[-1].add(effect.key)

                # Resolve from effective env (overrides + base)
                if effect.key in effective_env:
                    raw = effective_env[effect.key]
                else:
                    if strict:
                        return (yield TransferThrow(
                            k, KeyError(_missing_key_message(effect.key))
                        ))
                    # Forward to outer handler so env-var-ask (or other
                    # fallback handlers) can resolve the key.
                    yield Pass(effect, k)
                    return None

                # Plain value — resume directly
                if not isinstance(raw, Program):
                    return (yield Transfer(k, raw))

                # Cache lookup (scope then shared)
                cached_val, cached_dep = _cache_lookup(effect.key)
                if cached_val is not None:
                    if eval_stack:
                        eval_stack[-1].update(cached_dep)
                    return (yield Transfer(k, cached_val))

                # Create per-key semaphore on first lazy access
                if effect.key not in sems:
                    sem = yield CreateSemaphore(1)
                    sems[effect.key] = sem

                yield AcquireSemaphore(sems[effect.key])

                # Double-check after acquiring
                cached_val, cached_dep = _cache_lookup(effect.key)
                if cached_val is not None:
                    yield ReleaseSemaphore(sems[effect.key])
                    if eval_stack:
                        eval_stack[-1].update(cached_dep)
                    return (yield Transfer(k, cached_val))

                # Evaluate the program under this handler so effects
                # flow through lazy_ask and see the current env.
                # Reinstall inner handlers (between lazy_ask and the
                # Ask source) so the lazy Program's effects are handled.
                inner_hs = yield get_inner_handlers(k)
                wrapped = raw
                for h in inner_hs:
                    wrapped = _program_handler(h)(wrapped)
                eval_stack.append(set())
                error = None
                value = None
                try:
                    value = yield _program_handler(handler)(wrapped)
                except Exception as e:
                    error = e

                deps = frozenset(eval_stack.pop() if eval_stack else set())
                yield ReleaseSemaphore(sems[effect.key])

                if error is not None:
                    return (yield TransferThrow(k, error))

                _cache_store(effect.key, value, deps)
                if eval_stack:
                    eval_stack[-1].update(deps)
                return (yield Transfer(k, value))

            elif isinstance(effect, Local):
                # Create a new handler with merged env and fresh scope cache
                merged = {**effective_env, **effect.env}
                merged_overrides = override_keys | frozenset(effect.env.keys())
                inner_handler = _make_handler(merged, merged_overrides)

                # Reinstall inner handlers so effects from the Local
                # body flow through handlers between lazy_ask and the
                # Local call site.
                inner_hs = yield get_inner_handlers(k)
                prog = effect.program
                for h in inner_hs:
                    prog = _program_handler(h)(prog)

                error = None
                inner_result = None
                try:
                    inner_result = yield inner_handler(prog)
                except Exception as e:
                    error = e

                if error is not None:
                    return (yield TransferThrow(k, error))
                return (yield Transfer(k, inner_result))

            yield Pass(effect, k)

        return _program_handler(handler)

    return _make_handler(dict(env))


def env_var_ask(*, prefix="DOEFF_"):
    """Ask handler backed by ``os.environ``.

    Contract
    --------
    Each ``Ask(key)`` looks up ``os.environ[prefix + key]`` on every call.

    - Missing → ``Pass(effect, k)`` (forward to outer handler).
    - Plain string → resume directly, no caching.
    - ``"{module.path}"`` → import the symbol on every Ask.
      If it's a Program, evaluate it with the current inner handlers
      reinstalled so recursive Ask resolves naturally, then cache the
      resolved value keyed on ``(ask_key, raw_env_value)``. A change to
      the env var's raw string invalidates the cache.
      Otherwise resume with the imported object verbatim.

    Concurrency
    -----------
    A per-key semaphore ensures that concurrent Asks for the same lazy
    Program evaluate it only once — matching ``lazy_ask``'s semantics.

    The handler never calls ``strict=True``-style throws; unresolved keys
    always flow through to outer handlers (or Unhandled).
    """
    import os

    from doeff import Program
    from doeff.cli.run_services import import_symbol
    from doeff.handler_utils import get_inner_handlers
    from doeff_core_effects.scheduler import (
        AcquireSemaphore,
        CreateSemaphore,
        ReleaseSemaphore,
    )

    # cache[key] = (raw_env_value, resolved_value)
    cache: dict = {}
    sems: dict = {}

    @do
    def handler(effect, k):  # noqa: PLR0911 - baseline cleanup keeps existing control flow unchanged
        if not isinstance(effect, Ask):
            yield Pass(effect, k)
            return None

        env_key = f"{prefix}{effect.key}"
        raw = os.environ.get(env_key)
        if raw is None:
            yield Pass(effect, k)
            return None

        # Plain string — no caching, always fresh.
        if not (raw.startswith("{") and raw.endswith("}")):
            return (yield Transfer(k, raw))

        # {module.path} — cache with raw-value invalidation.
        cached = cache.get(effect.key)
        if cached is not None and cached[0] == raw:
            return (yield Transfer(k, cached[1]))

        # Per-key semaphore serialises concurrent evals.
        if effect.key not in sems:
            sems[effect.key] = yield CreateSemaphore(1)
        yield AcquireSemaphore(sems[effect.key])

        # Double-check after acquiring the semaphore.
        cached = cache.get(effect.key)
        if cached is not None and cached[0] == raw:
            yield ReleaseSemaphore(sems[effect.key])
            return (yield Transfer(k, cached[1]))

        try:
            path = raw[1:-1].strip()
            value = import_symbol(path)
            # If the imported symbol is a zero-arg factory (typical for @do
            # functions), call it to produce the Program. A value that's
            # already a Program is used verbatim.
            if (
                not isinstance(value, Program)
                and callable(value)
                and not isinstance(value, type)
            ):
                try:
                    maybe_program = value()
                except TypeError:
                    maybe_program = value
                value = maybe_program
            if isinstance(value, Program):
                inner_hs = yield get_inner_handlers(k)
                wrapped = value
                for h in inner_hs:
                    wrapped = _program_handler(h)(wrapped)
                resolved = yield wrapped
            else:
                resolved = value
            cache[effect.key] = (raw, resolved)
        except Exception as e:
            yield ReleaseSemaphore(sems[effect.key])
            return (yield TransferThrow(k, e))

        yield ReleaseSemaphore(sems[effect.key])
        return (yield Transfer(k, resolved))

    return _program_handler(handler)
