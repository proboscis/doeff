from __future__ import annotations

from datetime import datetime

from doeff.cesk.events import (
    TaskCompleted,
    TaskFailed,
    TaskCancelled,
    FutureResolved,
    FutureRejected,
    TimeAdvanced,
    IOCompleted,
    IOFailed,
)
from doeff.cesk.types import TaskId, FutureId


def test_task_completed_event():
    task_id = TaskId(1)
    result = 42
    event = TaskCompleted(task_id, result)
    
    assert event.task_id == task_id
    assert event.result == result


def test_task_failed_event():
    task_id = TaskId(1)
    error = ValueError("test error")
    event = TaskFailed(task_id, error)
    
    assert event.task_id == task_id
    assert event.error is error


def test_task_cancelled_event():
    task_id = TaskId(1)
    event = TaskCancelled(task_id)
    
    assert event.task_id == task_id


def test_future_resolved_event():
    future_id = FutureId(1)
    value = "result"
    event = FutureResolved(future_id, value)
    
    assert event.future_id == future_id
    assert event.value == value


def test_future_rejected_event():
    future_id = FutureId(1)
    error = RuntimeError("async error")
    event = FutureRejected(future_id, error)
    
    assert event.future_id == future_id
    assert event.error is error


def test_time_advanced_event():
    current_time = datetime(2024, 1, 1, 12, 0, 0)
    event = TimeAdvanced(current_time)
    
    assert event.current_time == current_time


def test_io_completed_event():
    result = {"status": "success"}
    event = IOCompleted(result)
    
    assert event.result == result


def test_io_failed_event():
    error = IOError("IO operation failed")
    event = IOFailed(error)
    
    assert event.error is error


def test_events_are_frozen():
    import pytest
    
    task_id = TaskId(1)
    event = TaskCompleted(task_id, 42)
    
    with pytest.raises((AttributeError, TypeError)):
        event.result = 100  # type: ignore
