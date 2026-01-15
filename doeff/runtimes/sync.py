"""SyncRuntime - Runtime for pure synchronous execution."""

from __future__ import annotations

import time
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


class AsyncEffectInSyncRuntimeError(EffectError):
    pass


class SyncRuntime(RuntimeMixin):
    def __init__(self, handlers: dict | None = None):
        self._init_handlers(handlers)
    
    def _execute_payload(
        self,
        payload: SchedulePayload,
        store: "Store",
    ) -> tuple[Any, "Store"]:
        match payload:
            case AwaitPayload():
                raise AsyncEffectInSyncRuntimeError(
                    "SyncRuntime cannot handle async effects. Use AsyncioRuntime."
                )
            
            case DelayPayload(duration=d):
                time.sleep(d.total_seconds())
                return (None, store)
            
            case WaitUntilPayload():
                raise AsyncEffectInSyncRuntimeError(
                    "SyncRuntime cannot handle WaitUntil. Use AsyncioRuntime."
                )
            
            case SpawnPayload():
                raise AsyncEffectInSyncRuntimeError(
                    "SyncRuntime cannot handle Spawn. Use AsyncioRuntime."
                )
            
            case _:
                raise TypeError(f"Unknown payload: {type(payload)}")
    
    def run(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> T:
        dispatcher = self._create_dispatcher()
        E, S = self._prepare_env_store(env, store, dispatcher)
        
        state = CESKState.initial(program, E, S)
        
        while True:
            result = self._step_until_effect(state, dispatcher)
            
            match result:
                case Done(value=v):
                    return v
                
                case Failed(exception=exc, captured_traceback=tb):
                    raise EffectError(str(exc), exc, tb)
                
                case (Suspended() as suspended, CESKState() as last_state):
                    payload, new_store = self._get_payload_from_suspended(
                        suspended, last_state, dispatcher
                    )
                    k = self._make_continuation(suspended, last_state, new_store)
                    
                    try:
                        value, result_store = self._execute_payload(payload, new_store)
                        state = k.resume(value, result_store)
                    except Exception as ex:
                        state = k.resume_error(ex, new_store)
    
    def run_safe(
        self,
        program: "Program[T]",
        env: "Environment | dict | None" = None,
        store: "Store | None" = None,
    ) -> RuntimeResult[T]:
        try:
            value = self.run(program, env, store)
            return RuntimeResult(Ok(value))
        except EffectError as e:
            cause = e.cause if isinstance(e.cause, Exception) else e
            return RuntimeResult(Err(cause), e.effect_traceback)
        except Exception as e:
            return RuntimeResult(Err(e))


__all__ = ["SyncRuntime", "AsyncEffectInSyncRuntimeError"]
