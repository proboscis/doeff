from datetime import datetime

import pytest

from doeff.cesk.types import (
    Environment,
    FutureId,
    IdGenerator,
    SimulatedTime,
    SpawnId,
    Store,
    TaskErr,
    TaskId,
    TaskOk,
)
from doeff._vendor import FrozenDict


class TestTaskId:
    def test_task_id_is_newtype_of_int(self):
        tid = TaskId(42)
        assert tid == 42
        assert isinstance(tid, int)
    
    def test_task_ids_are_comparable(self):
        tid1 = TaskId(1)
        tid2 = TaskId(2)
        assert tid1 < tid2
        assert tid2 > tid1
        assert tid1 != tid2


class TestFutureId:
    def test_future_id_is_newtype_of_int(self):
        fid = FutureId(100)
        assert fid == 100
        assert isinstance(fid, int)


class TestSpawnId:
    def test_spawn_id_is_newtype_of_int(self):
        sid = SpawnId(999)
        assert sid == 999
        assert isinstance(sid, int)


class TestEnvironment:
    def test_environment_is_frozendict(self):
        env: Environment = FrozenDict({"key": "value"})
        assert env["key"] == "value"
    
    def test_environment_is_immutable(self):
        env: Environment = FrozenDict({"a": 1})
        with pytest.raises(TypeError):
            env["b"] = 2  # type: ignore[index]
    
    def test_environment_merge_creates_new(self):
        env1: Environment = FrozenDict({"a": 1})
        env2: Environment = env1 | FrozenDict({"b": 2})
        assert "b" not in env1
        assert env2["a"] == 1
        assert env2["b"] == 2


class TestStore:
    def test_store_is_mutable_dict(self):
        store: Store = {}
        store["__log__"] = []
        store["counter"] = 0
        assert store["counter"] == 0
    
    def test_store_accepts_any_string_key(self):
        store: Store = {"state_key": 42, "__memo__": {}}
        assert store["state_key"] == 42


class TestTaskOk:
    def test_task_ok_holds_value_and_task_id(self):
        tid = TaskId(1)
        result = TaskOk(value="success", task_id=tid)
        assert result.value == "success"
        assert result.task_id == tid
    
    def test_task_ok_is_frozen(self):
        result = TaskOk(value=42, task_id=TaskId(0))
        with pytest.raises(AttributeError):
            result.value = 100  # type: ignore[misc]


class TestTaskErr:
    def test_task_err_holds_error_and_task_id(self):
        tid = TaskId(2)
        error = ValueError("test error")
        result = TaskErr(error=error, task_id=tid)
        assert result.error is error
        assert result.task_id == tid
        assert result.captured_traceback is None
    
    def test_task_err_with_traceback(self):
        tid = TaskId(3)
        error = RuntimeError("failed")
        result = TaskErr(error=error, task_id=tid, captured_traceback=None)
        assert result.error is error


class TestSimulatedTime:
    def test_simulated_time_wraps_datetime(self):
        dt = datetime(2025, 1, 16, 12, 0, 0)
        st = SimulatedTime(dt)
        assert st.value == dt
    
    def test_simulated_time_now(self):
        before = datetime.now()
        st = SimulatedTime.now()
        after = datetime.now()
        assert before <= st.value <= after
    
    def test_simulated_time_comparison(self):
        t1 = SimulatedTime(datetime(2025, 1, 1))
        t2 = SimulatedTime(datetime(2025, 1, 2))
        assert t1 < t2
        assert t1 <= t2
        assert not (t2 < t1)
        assert t1 <= t1


class TestIdGenerator:
    def test_generates_sequential_task_ids(self):
        gen = IdGenerator()
        tid1 = gen.next_task_id()
        tid2 = gen.next_task_id()
        tid3 = gen.next_task_id()
        assert tid1 == TaskId(0)
        assert tid2 == TaskId(1)
        assert tid3 == TaskId(2)
    
    def test_generates_sequential_future_ids(self):
        gen = IdGenerator()
        fid1 = gen.next_future_id()
        fid2 = gen.next_future_id()
        assert fid1 == FutureId(0)
        assert fid2 == FutureId(1)
    
    def test_generates_sequential_spawn_ids(self):
        gen = IdGenerator()
        sid1 = gen.next_spawn_id()
        sid2 = gen.next_spawn_id()
        assert sid1 == SpawnId(0)
        assert sid2 == SpawnId(1)
    
    def test_id_counters_are_independent(self):
        gen = IdGenerator()
        gen.next_task_id()
        gen.next_task_id()
        gen.next_future_id()
        
        assert gen.next_task_id() == TaskId(2)
        assert gen.next_future_id() == FutureId(1)
        assert gen.next_spawn_id() == SpawnId(0)
