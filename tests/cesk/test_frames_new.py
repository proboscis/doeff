from __future__ import annotations

from doeff.cesk.frames_new import (
    Frame,
    Continue,
    PopFrame,
    ReturnFrame,
    LocalFrame,
    SafeFrame,
    ListenFrame,
    InterceptFrame,
    GatherFrame,
    RaceFrame,
)
from doeff.cesk.types import TaskId, Environment
from doeff._vendor import FrozenDict, Ok, Err
from doeff.program import Program


def test_return_frame_on_value():
    gen = iter([])
    env = FrozenDict()
    frame = ReturnFrame(gen, env)
    
    value, result = frame.on_value(42)
    
    assert value == 42
    assert isinstance(result, Continue)


def test_return_frame_on_error():
    gen = iter([])
    env = FrozenDict()
    frame = ReturnFrame(gen, env)
    
    error = ValueError("test")
    returned_error, result = frame.on_error(error)
    
    assert returned_error is error
    assert isinstance(result, Continue)


def test_local_frame_on_value():
    restore_env = FrozenDict({"key": "value"})
    frame = LocalFrame(restore_env)
    
    value, result = frame.on_value(42)
    
    assert value == 42
    assert isinstance(result, PopFrame)


def test_local_frame_on_error():
    restore_env = FrozenDict({"key": "value"})
    frame = LocalFrame(restore_env)
    
    error = ValueError("test")
    returned_error, result = frame.on_error(error)
    
    assert returned_error is error
    assert isinstance(result, PopFrame)


def test_safe_frame_on_value():
    saved_env = FrozenDict()
    frame = SafeFrame(saved_env)
    
    value, result = frame.on_value(42)
    
    assert isinstance(value, Ok)
    assert value.value == 42
    assert isinstance(result, PopFrame)


def test_safe_frame_on_error():
    saved_env = FrozenDict()
    frame = SafeFrame(saved_env)
    
    error = ValueError("test")
    err_result, result = frame.on_error(error)
    
    assert isinstance(err_result, Err)
    assert err_result.error is error
    assert isinstance(result, PopFrame)


def test_listen_frame_on_value():
    frame = ListenFrame(log_start_index=0)
    
    value, result = frame.on_value(42)
    
    assert value == 42
    assert isinstance(result, Continue)


def test_listen_frame_on_error():
    frame = ListenFrame(log_start_index=0)
    
    error = ValueError("test")
    returned_error, result = frame.on_error(error)
    
    assert returned_error is error
    assert isinstance(result, PopFrame)


def test_intercept_frame_on_value():
    transforms = (lambda e: e,)
    frame = InterceptFrame(transforms)
    
    value, result = frame.on_value(42)
    
    assert value == 42
    assert isinstance(result, PopFrame)


def test_intercept_frame_on_error():
    transforms = (lambda e: e,)
    frame = InterceptFrame(transforms)
    
    error = ValueError("test")
    returned_error, result = frame.on_error(error)
    
    assert returned_error is error
    assert isinstance(result, PopFrame)


def test_gather_frame_on_value_with_remaining():
    remaining = [Program.pure(2), Program.pure(3)]
    collected = [1]
    saved_env = FrozenDict()
    child_ids = [TaskId(1), TaskId(2), TaskId(3)]
    
    frame = GatherFrame(remaining, collected, saved_env, child_ids)
    
    value, result = frame.on_value(2)
    
    assert value == 2
    assert isinstance(result, Continue)


def test_gather_frame_on_value_last_item():
    remaining = []
    collected = [1, 2]
    saved_env = FrozenDict()
    child_ids = [TaskId(1), TaskId(2), TaskId(3)]
    
    frame = GatherFrame(remaining, collected, saved_env, child_ids)
    
    value, result = frame.on_value(3)
    
    assert value == [1, 2, 3]
    assert isinstance(result, PopFrame)


def test_gather_frame_on_error():
    remaining = [Program.pure(2)]
    collected = [1]
    saved_env = FrozenDict()
    child_ids = [TaskId(1), TaskId(2)]
    
    frame = GatherFrame(remaining, collected, saved_env, child_ids)
    
    error = ValueError("test")
    returned_error, result = frame.on_error(error)
    
    assert returned_error is error
    assert isinstance(result, PopFrame)


def test_gather_frame_on_child_done():
    remaining = [Program.pure(3)]
    collected = [1]
    saved_env = FrozenDict()
    child_ids = [TaskId(1), TaskId(2), TaskId(3)]
    
    frame = GatherFrame(remaining, collected, saved_env, child_ids)
    
    result_tuple = frame.on_child_done(TaskId(2), 42)
    
    assert result_tuple is not None
    value, result = result_tuple
    assert value == 42
    assert isinstance(result, Continue)


def test_gather_frame_on_child_done_complete():
    remaining = []
    collected = [1]
    saved_env = FrozenDict()
    child_ids = [TaskId(1), TaskId(2)]
    
    frame = GatherFrame(remaining, collected, saved_env, child_ids)
    
    result_tuple = frame.on_child_done(TaskId(2), 42)
    
    assert result_tuple is not None
    value, result = result_tuple
    assert value == [1, 42]
    assert isinstance(result, PopFrame)


def test_gather_frame_on_child_done_wrong_task():
    remaining = [Program.pure(2)]
    collected = [1]
    saved_env = FrozenDict()
    child_ids = [TaskId(1), TaskId(2)]
    
    frame = GatherFrame(remaining, collected, saved_env, child_ids)
    
    result_tuple = frame.on_child_done(TaskId(999), 42)
    
    assert result_tuple is None


def test_race_frame_on_value():
    child_ids = [TaskId(1), TaskId(2)]
    frame = RaceFrame(child_ids)
    
    value, result = frame.on_value(42)
    
    assert value == 42
    assert isinstance(result, PopFrame)


def test_race_frame_on_error():
    child_ids = [TaskId(1), TaskId(2)]
    frame = RaceFrame(child_ids)
    
    error = ValueError("test")
    returned_error, result = frame.on_error(error)
    
    assert returned_error is error
    assert isinstance(result, PopFrame)


def test_race_frame_on_child_done():
    child_ids = [TaskId(1), TaskId(2)]
    frame = RaceFrame(child_ids)
    
    result_tuple = frame.on_child_done(TaskId(1), 42)
    
    assert result_tuple is not None
    value, result = result_tuple
    assert value == 42
    assert isinstance(result, PopFrame)


def test_race_frame_on_child_done_wrong_task():
    child_ids = [TaskId(1), TaskId(2)]
    frame = RaceFrame(child_ids)
    
    result_tuple = frame.on_child_done(TaskId(999), 42)
    
    assert result_tuple is None


def test_frame_on_child_done_default():
    restore_env = FrozenDict()
    frame = LocalFrame(restore_env)
    
    result = frame.on_child_done(TaskId(1), 42)
    
    assert result is None


def test_frames_are_frozen():
    import pytest
    
    gen = iter([])
    env = FrozenDict()
    frame = ReturnFrame(gen, env)
    
    with pytest.raises((AttributeError, TypeError)):
        frame.generator = iter([1, 2, 3])  # type: ignore
