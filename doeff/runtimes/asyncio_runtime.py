"""AsyncioRuntime - Runtime for real async I/O execution."""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypeVar

from doeff._vendor import Err, Ok
from doeff.cesk.state import CESKState
from doeff.cesk.result import Done, Failed, Suspended
from doeff.runtime import (
    AwaitPayload,
    DelayPayload,
    SchedulePayload,
    SpawnPayload,
    WaitUntilPayload,
)
from doeff.runtimes.base import RuntimeMixin, EffectError, RuntimeResult

if TYPE_CHECKING:
    from doeff.cesk.types import Environment, Store
    from doeff.program import Program

T = TypeVar("T")


class AsyncioRuntime(RuntimeMixin):
    def __init__(self, handlers: dict | None = None):
        self._init_handlers(handlers)
    
    async def _execute_payload(
        self,
        payload: SchedulePayload,
        store: "Store",
    ) -> tuple[Any, "Store"]:
        match payload:
            case AwaitPayload(awaitable=aw):
                result = await aw
                if isinstance(result, tuple) and len(result) == 2:
                    return result
                return (result, store)
            
            case DelayPayload(duration=d):
                await asyncio.sleep(d.total_seconds())
                return (None, store)
            
            case WaitUntilPayload(target=t):
                now = datetime.now(tz=t.tzinfo)
                delay = max(0.0, (t - now).total_seconds())
                await asyncio.sleep(delay)
                return (None, store)
            
            case SpawnPayload(program=prog, env=e, store=s):
                task = asyncio.create_task(self.run(prog, e, s))
                return (task, store)
            
            case _:
                raise TypeError(f"Unknown payload: {type(payload)}")
    
    async def _run_internal(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> tuple[T, "Store", "Environment", Any]:
        """Run program, return (value, final_store, final_env, traceback).
        
        Raises EffectError on failure. On error, includes store state at failure point.
        """
        dispatcher = self._create_dispatcher()
        E, S = self._prepare_env_store(env, store, dispatcher)
        
        state = CESKState.initial(program, E, S)
        final_E = E
        final_S = S
        
        while True:
            result = self._step_until_effect(state, dispatcher)
            
            match result:
                case Done(value=v, store=s):
                    return (v, s, final_E, None)
                
                case Failed(exception=exc, captured_traceback=tb, store=s):
                    err = EffectError(str(exc), exc, tb)
                    err.final_store = s
                    err.final_env = final_E
                    raise err
                
                case (Suspended() as suspended, CESKState() as last_state):
                    payload, new_store = self._get_payload_from_suspended(
                        suspended, last_state, dispatcher
                    )
                    k = self._make_continuation(suspended, last_state, new_store)
                    final_E = last_state.E
                    final_S = new_store
                    
                    try:
                        value, result_store = await self._execute_payload(
                            payload, new_store
                        )
                        state = k.resume(value, result_store)
                        final_S = result_store
                    except Exception as ex:
                        state = k.resume_error(ex, new_store)

    async def run(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> T:
        """Run program with real async I/O. Raises EffectError on failure."""
        value, _, _, _ = await self._run_internal(program, env, store)
        return value
    
    async def run_safe(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> RuntimeResult[T]:
        """Run program, return Result instead of raising."""
        try:
            value, final_store, final_env, tb = await self._run_internal(program, env, store)
            return RuntimeResult(Ok(value), tb, final_store, final_env)
        except EffectError as e:
            cause = e.cause if isinstance(e.cause, Exception) else e
            return RuntimeResult(
                Err(cause), e.effect_traceback, e.final_store, e.final_env
            )
        except Exception as e:
            return RuntimeResult(Err(e))


__all__ = ["AsyncioRuntime"]
