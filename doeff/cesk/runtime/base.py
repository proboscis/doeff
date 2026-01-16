from __future__ import annotations

from typing import TYPE_CHECKING, Any

from doeff.cesk.runtime import Runtime
from doeff.cesk.state_new import CESKState, TaskStatus, Value, Error, TaskState
from doeff.cesk.step_new import step, Done, Failed, Suspended
from doeff.cesk.types import TaskId, TaskIdGenerator
from doeff.cesk.handlers import (
    HandlerContext,
    ResumeWith,
    ResumeWithError,
    PerformAction,
    get_default_registry,
)
from doeff._vendor import FrozenDict

if TYPE_CHECKING:
    from doeff.program import Program
    from doeff.cesk.types import Store, Environment


class BaseRuntime(Runtime):
    def __init__(self):
        from doeff.cesk.handlers import core, control, time, task, io
        self.registry = get_default_registry()
        self.task_id_gen = TaskIdGenerator()
    
    def run(
        self,
        program: Program,
        env: Environment | dict | None = None,
        store: Store | None = None,
    ) -> Any:
        task_id, self.task_id_gen = self.task_id_gen.next()
        
        if env is None:
            env_frozen = FrozenDict()
        elif isinstance(env, FrozenDict):
            env_frozen = env
        else:
            env_frozen = FrozenDict(env)
        
        if store is None:
            store = {}
        
        state = CESKState.initial(program, task_id, env_frozen, store)
        
        max_iterations = 10000
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            result = step(state)
            
            if isinstance(result, Done):
                return result.value
            
            elif isinstance(result, Failed):
                raise result.error
            
            elif isinstance(result, Suspended):
                effect = result.effect
                new_state = result.state
                
                active_task = new_state.get_active_task()
                if not active_task:
                    raise RuntimeError("Suspended but no active task")
                
                handler = self.registry.get_handler(type(effect))
                if not handler:
                    raise RuntimeError(f"No handler for effect type {type(effect).__name__}")
                
                ctx = HandlerContext(
                    store=new_state.store,
                    environment=active_task.environment,
                    kontinuation=active_task.kontinuation,
                )
                
                try:
                    handler_result = handler(effect, ctx)
                    
                    if isinstance(handler_result, ResumeWith):
                        task_with_value = active_task.__class__(
                            task_id=active_task.task_id,
                            control=Value(handler_result.value),
                            environment=active_task.environment,
                            kontinuation=active_task.kontinuation,
                            status=TaskStatus.RUNNING,
                        )
                        state = new_state.with_task(active_task.task_id, task_with_value)
                    
                    elif isinstance(handler_result, ResumeWithError):
                        from doeff.cesk.state_new import Error
                        task_with_error = active_task.__class__(
                            task_id=active_task.task_id,
                            control=Error(handler_result.error),
                            environment=active_task.environment,
                            kontinuation=active_task.kontinuation,
                            status=TaskStatus.RUNNING,
                        )
                        state = new_state.with_task(active_task.task_id, task_with_error)
                    
                    elif isinstance(handler_result, PerformAction):
                        raise NotImplementedError("Action execution not yet implemented in base runtime")
                    
                    else:
                        raise RuntimeError(f"Unknown handler result type: {type(handler_result)}")
                
                except Exception as e:
                    from doeff.cesk.state_new import Error
                    task_with_error = active_task.__class__(
                        task_id=active_task.task_id,
                        control=Error(e),
                        environment=active_task.environment,
                        kontinuation=active_task.kontinuation,
                        status=TaskStatus.RUNNING,
                    )
                    state = new_state.with_task(active_task.task_id, task_with_error)
            
            else:
                raise RuntimeError(f"Unknown step result type: {type(result)}")
        
        raise RuntimeError(f"Runtime exceeded maximum iterations ({max_iterations})")


__all__ = ["BaseRuntime"]
