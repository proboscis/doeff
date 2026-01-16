from datetime import datetime, timedelta

import pytest

from doeff.cesk.actions import (
    Action,
    AppendLog,
    AwaitExternal,
    BlockForFuture,
    BlockForTasks,
    CancelTasks,
    CreateTask,
    CreateTasks,
    Delay,
    ModifyStore,
    PerformIO,
    Resume,
    ResumeError,
    RunProgram,
    WaitUntil,
)
from doeff.cesk.types import FutureId, SpawnId, TaskId
from doeff._vendor import FrozenDict
from doeff.program import Program


class TestResume:
    def test_resume_holds_value(self):
        action = Resume(value=42)
        assert action.value == 42
        assert action.store is None
    
    def test_resume_with_store(self):
        store = {"key": "value"}
        action = Resume(value="result", store=store)
        assert action.value == "result"
        assert action.store == store


class TestResumeError:
    def test_resume_error_holds_exception(self):
        error = ValueError("test")
        action = ResumeError(error=error)
        assert action.error is error
        assert action.store is None


class TestCreateTask:
    def test_create_task_holds_program(self):
        prog = Program.pure(42)
        action = CreateTask(program=prog)
        assert action.program is prog
        assert action.env is None
        assert action.spawn_id is None
        assert action.parent_id is None
    
    def test_create_task_with_env_and_ids(self):
        prog = Program.pure(42)
        env = FrozenDict({"key": "value"})
        action = CreateTask(
            program=prog,
            env=env,
            spawn_id=SpawnId(1),
            parent_id=TaskId(0),
        )
        assert action.env == env
        assert action.spawn_id == SpawnId(1)
        assert action.parent_id == TaskId(0)


class TestCreateTasks:
    def test_create_tasks_holds_multiple_programs(self):
        progs = (Program.pure(1), Program.pure(2), Program.pure(3))
        action = CreateTasks(programs=progs)
        assert action.programs == progs
        assert action.envs is None


class TestPerformIO:
    def test_perform_io_holds_callable(self):
        def my_io():
            return "result"
        
        action = PerformIO(io_callable=my_io, io_id=42)
        assert action.io_callable is my_io
        assert action.io_id == 42


class TestAwaitExternal:
    def test_await_external_holds_awaitable_and_future_id(self):
        async def coro():
            return "result"
        
        aw = coro()
        fid = FutureId(1)
        action = AwaitExternal(awaitable=aw, future_id=fid)
        assert action.awaitable is aw
        assert action.future_id == fid
        aw.close()


class TestDelay:
    def test_delay_holds_duration(self):
        duration = timedelta(seconds=5)
        action = Delay(duration=duration)
        assert action.duration == duration


class TestWaitUntil:
    def test_wait_until_holds_target(self):
        target = datetime(2025, 1, 16, 12, 0, 0)
        action = WaitUntil(target=target)
        assert action.target == target


class TestCancelTasks:
    def test_cancel_tasks_holds_task_ids(self):
        ids = frozenset([TaskId(1), TaskId(2)])
        action = CancelTasks(task_ids=ids)
        assert action.task_ids == ids


class TestRunProgram:
    def test_run_program_holds_program(self):
        prog = Program.pure(42)
        action = RunProgram(program=prog)
        assert action.program is prog
        assert action.env is None
    
    def test_run_program_with_env(self):
        prog = Program.pure(42)
        env = FrozenDict({"key": "value"})
        action = RunProgram(program=prog, env=env)
        assert action.env == env


class TestBlockForFuture:
    def test_block_for_future_holds_future_id(self):
        fid = FutureId(5)
        action = BlockForFuture(future_id=fid)
        assert action.future_id == fid


class TestBlockForTasks:
    def test_block_for_tasks_holds_task_ids(self):
        ids = frozenset([TaskId(1), TaskId(2)])
        action = BlockForTasks(task_ids=ids, wait_all=True)
        assert action.task_ids == ids
        assert action.wait_all is True
    
    def test_block_for_any(self):
        ids = frozenset([TaskId(1), TaskId(2)])
        action = BlockForTasks(task_ids=ids, wait_all=False)
        assert action.wait_all is False


class TestModifyStore:
    def test_modify_store_holds_key_value(self):
        action = ModifyStore(key="counter", value=42)
        assert action.key == "counter"
        assert action.value == 42


class TestAppendLog:
    def test_append_log_holds_message(self):
        action = AppendLog(message="test log")
        assert action.message == "test log"
