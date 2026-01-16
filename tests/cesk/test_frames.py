import pytest

from doeff._vendor import Err, FrozenDict, Ok
from doeff.cesk.frames import (
    Frame,
    FrameResult,
    GatherFrame,
    InterceptFrame,
    JoinFrame,
    ListenFrame,
    LocalFrame,
    RaceFrame,
    ReturnFrame,
    SafeFrame,
)
from doeff.cesk.state import EffectControl, Error, ProgramControl, Value
from doeff.cesk.types import Environment, TaskId
from doeff.program import Program


class TestFrameProtocol:
    def test_local_frame_implements_protocol(self):
        frame = LocalFrame(restore_env=FrozenDict())
        assert isinstance(frame, Frame)
    
    def test_safe_frame_implements_protocol(self):
        frame = SafeFrame(saved_env=FrozenDict())
        assert isinstance(frame, Frame)


class TestFrameResult:
    def test_frame_result_holds_control_and_env(self):
        ctrl = Value(42)
        env: Environment = FrozenDict({"key": "value"})
        result = FrameResult(ctrl, env)
        
        assert result.control == ctrl
        assert result.env == env
        assert result.actions == ()
    
    def test_frame_result_with_actions(self):
        ctrl = Value(42)
        env: Environment = FrozenDict()
        actions = (("cancel_tasks", frozenset([TaskId(1)])),)
        result = FrameResult(ctrl, env, actions)
        
        assert result.actions == actions


class TestReturnFrame:
    def test_on_value_sends_to_generator_with_effect(self):
        from doeff.effects import AskEffect
        
        def gen():
            x = yield AskEffect("key1")
            y = yield AskEffect("key2")
            return x + y
        
        g = gen()
        next(g)
        
        frame = ReturnFrame(g, FrozenDict())
        result = frame.on_value(10, FrozenDict())
        
        assert isinstance(result.control, EffectControl)
        assert isinstance(result.control.effect, AskEffect)
    
    def test_on_value_returns_program_control_for_program(self):
        def gen():
            yield Program.pure(42)
            return "done"
        
        g = gen()
        next(g)
        
        frame = ReturnFrame(g, FrozenDict({"saved": True}))
        result = frame.on_value(None, FrozenDict())
        
        assert result.env == FrozenDict({"saved": True})
    
    def test_on_value_stops_iteration(self):
        def gen():
            x = yield
            return x * 2
        
        g = gen()
        next(g)
        
        frame = ReturnFrame(g, FrozenDict())
        result = frame.on_value(21, FrozenDict())
        
        assert isinstance(result.control, Value)
        assert result.control.v == 42
    
    def test_on_error_throws_to_generator(self):
        def gen():
            try:
                yield
            except ValueError as e:
                return f"caught: {e}"
        
        g = gen()
        next(g)
        
        frame = ReturnFrame(g, FrozenDict())
        result = frame.on_error(ValueError("test"), FrozenDict())
        
        assert isinstance(result.control, Value)
        assert result.control.v == "caught: test"
    
    def test_on_error_propagates_if_not_caught(self):
        def gen():
            yield
            return "never"
        
        g = gen()
        next(g)
        
        frame = ReturnFrame(g, FrozenDict())
        result = frame.on_error(ValueError("oops"), FrozenDict())
        
        assert isinstance(result.control, Error)
        assert isinstance(result.control.ex, ValueError)


class TestLocalFrame:
    def test_on_value_restores_env(self):
        original_env: Environment = FrozenDict({"original": True})
        frame = LocalFrame(restore_env=original_env)
        
        current_env: Environment = FrozenDict({"modified": True})
        result = frame.on_value(42, current_env)
        
        assert isinstance(result.control, Value)
        assert result.control.v == 42
        assert result.env == original_env
    
    def test_on_error_restores_env(self):
        original_env: Environment = FrozenDict({"original": True})
        frame = LocalFrame(restore_env=original_env)
        
        error = ValueError("test")
        result = frame.on_error(error, FrozenDict())
        
        assert isinstance(result.control, Error)
        assert result.control.ex is error
        assert result.env == original_env


class TestInterceptFrame:
    def test_on_value_passes_through(self):
        frame = InterceptFrame(transforms=())
        env: Environment = FrozenDict({"key": "value"})
        
        result = frame.on_value("result", env)
        
        assert isinstance(result.control, Value)
        assert result.control.v == "result"
        assert result.env == env
    
    def test_on_error_passes_through(self):
        frame = InterceptFrame(transforms=())
        env: Environment = FrozenDict()
        
        error = RuntimeError("test")
        result = frame.on_error(error, env)
        
        assert isinstance(result.control, Error)
        assert result.control.ex is error


class TestListenFrame:
    def test_on_value_returns_listen_result_structure(self):
        frame = ListenFrame(log_start_index=5)
        
        result = frame.on_value("my_value", FrozenDict())
        
        assert isinstance(result.control, Value)
        assert ("capture_log", 5) in result.actions
    
    def test_on_error_passes_through(self):
        frame = ListenFrame(log_start_index=0)
        
        error = ValueError("test")
        result = frame.on_error(error, FrozenDict())
        
        assert isinstance(result.control, Error)
        assert result.control.ex is error


class TestGatherFrame:
    def test_on_value_collects_and_continues(self):
        prog2 = Program.pure(200)
        frame = GatherFrame(
            remaining_programs=[prog2],
            collected_results=[100],
            saved_env=FrozenDict({"saved": True}),
        )
        
        result = frame.on_value(150, FrozenDict())
        
        assert isinstance(result.control, ProgramControl)
        assert result.env == FrozenDict({"saved": True})
        assert len(result.actions) == 1
        assert result.actions[0][0] == "push_gather_frame"
    
    def test_on_value_completes_when_no_remaining(self):
        frame = GatherFrame(
            remaining_programs=[],
            collected_results=[100, 200],
            saved_env=FrozenDict(),
        )
        
        result = frame.on_value(300, FrozenDict())
        
        assert isinstance(result.control, Value)
        assert result.control.v == [100, 200, 300]
    
    def test_on_error_returns_error(self):
        frame = GatherFrame(
            remaining_programs=[Program.pure(1)],
            collected_results=[],
            saved_env=FrozenDict({"saved": True}),
        )
        
        error = ValueError("failed")
        result = frame.on_error(error, FrozenDict())
        
        assert isinstance(result.control, Error)
        assert result.control.ex is error
        assert result.env == FrozenDict({"saved": True})


class TestSafeFrame:
    def test_on_value_wraps_in_ok(self):
        frame = SafeFrame(saved_env=FrozenDict({"saved": True}))
        
        result = frame.on_value(42, FrozenDict())
        
        assert isinstance(result.control, Value)
        assert isinstance(result.control.v, Ok)
        assert result.control.v.value == 42
        assert result.env == FrozenDict({"saved": True})
    
    def test_on_error_wraps_in_err(self):
        frame = SafeFrame(saved_env=FrozenDict())
        
        error = ValueError("test")
        result = frame.on_error(error, FrozenDict())
        
        assert isinstance(result.control, Value)
        assert isinstance(result.control.v, Err)
        assert result.control.v.error is error


class TestRaceFrame:
    def test_on_value_cancels_other_tasks(self):
        other_ids = frozenset([TaskId(1), TaskId(2)])
        frame = RaceFrame(other_task_ids=other_ids)
        
        result = frame.on_value("winner", FrozenDict())
        
        assert isinstance(result.control, Value)
        assert result.control.v == "winner"
        assert ("cancel_tasks", other_ids) in result.actions
    
    def test_on_error_cancels_other_tasks(self):
        other_ids = frozenset([TaskId(1)])
        frame = RaceFrame(other_task_ids=other_ids)
        
        error = ValueError("failed")
        result = frame.on_error(error, FrozenDict())
        
        assert isinstance(result.control, Error)
        assert ("cancel_tasks", other_ids) in result.actions


class TestJoinFrame:
    def test_on_value_passes_through(self):
        frame = JoinFrame(target_task_id=TaskId(5))
        
        result = frame.on_value("joined_value", FrozenDict())
        
        assert isinstance(result.control, Value)
        assert result.control.v == "joined_value"
    
    def test_on_error_passes_through(self):
        frame = JoinFrame(target_task_id=TaskId(5))
        
        error = RuntimeError("child failed")
        result = frame.on_error(error, FrozenDict())
        
        assert isinstance(result.control, Error)
        assert result.control.ex is error
