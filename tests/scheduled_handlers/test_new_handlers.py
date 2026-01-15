"""Tests for new scheduled handlers: callstack, graph, atomic."""

import pytest
from doeff._vendor import FrozenDict
from doeff.runtime import Resume
from doeff.scheduled_handlers.callstack import (
    handle_program_call_frame,
    handle_program_call_stack,
)
from doeff.scheduled_handlers.graph import (
    handle_graph_step,
    handle_graph_annotate,
    handle_graph_snapshot,
)
from doeff.scheduled_handlers.atomic import (
    handle_atomic_get,
    handle_atomic_update,
)


class MockEffect:
    pass


class TestCallstackHandlers:
    def test_program_call_stack_empty(self):
        effect = MockEffect()
        env = FrozenDict({})
        store = {}
        
        result = handle_program_call_stack(effect, env, store)
        
        assert isinstance(result, Resume)
        assert result.value == ()
        assert result.store == store

    def test_program_call_stack_with_frames(self):
        effect = MockEffect()
        env = FrozenDict({})
        frames = ("frame1", "frame2", "frame3")
        store = {"__call_stack__": frames}
        
        result = handle_program_call_stack(effect, env, store)
        
        assert result.value == frames

    def test_program_call_frame_depth_0(self):
        effect = MockEffect()
        effect.depth = 0
        env = FrozenDict({})
        frames = ("outer", "middle", "inner")
        store = {"__call_stack__": frames}
        
        result = handle_program_call_frame(effect, env, store)
        
        assert result.value == "inner"

    def test_program_call_frame_depth_1(self):
        effect = MockEffect()
        effect.depth = 1
        env = FrozenDict({})
        frames = ("outer", "middle", "inner")
        store = {"__call_stack__": frames}
        
        result = handle_program_call_frame(effect, env, store)
        
        assert result.value == "middle"

    def test_program_call_frame_empty_stack_raises(self):
        effect = MockEffect()
        effect.depth = 0
        env = FrozenDict({})
        store = {}
        
        with pytest.raises(IndexError, match="Call stack is empty"):
            handle_program_call_frame(effect, env, store)

    def test_program_call_frame_depth_exceeds_stack_raises(self):
        effect = MockEffect()
        effect.depth = 5
        env = FrozenDict({})
        frames = ("frame1", "frame2")
        store = {"__call_stack__": frames}
        
        with pytest.raises(IndexError, match="exceeds available stack size"):
            handle_program_call_frame(effect, env, store)


class TestGraphHandlers:
    def test_graph_step_creates_new_node(self):
        effect = MockEffect()
        effect.value = "test_value"
        effect.meta = {"op": "test"}
        env = FrozenDict({})
        store = {}
        
        result = handle_graph_step(effect, env, store)
        
        assert isinstance(result, Resume)
        assert result.value == "test_value"
        assert "__graph__" in result.store
        graph = result.store["__graph__"]
        assert len(graph.steps) == 2
        assert graph.last.output.value == "test_value"

    def test_graph_annotate_updates_metadata(self):
        from doeff._vendor import WGraph
        
        effect = MockEffect()
        effect.meta = {"note": "annotated"}
        env = FrozenDict({})
        initial_graph = WGraph.single("initial")
        store = {"__graph__": initial_graph}
        
        result = handle_graph_annotate(effect, env, store)
        
        assert isinstance(result, Resume)
        assert result.value is None
        new_graph = result.store["__graph__"]
        assert new_graph.last.meta.get("note") == "annotated"

    def test_graph_snapshot_returns_current_graph(self):
        from doeff._vendor import WGraph
        
        effect = MockEffect()
        env = FrozenDict({})
        initial_graph = WGraph.single("snapshot_test")
        store = {"__graph__": initial_graph}
        
        result = handle_graph_snapshot(effect, env, store)
        
        assert isinstance(result, Resume)
        assert result.value == initial_graph
        assert result.store == store


class TestAtomicHandlers:
    def test_atomic_get_missing_key_returns_none(self):
        effect = MockEffect()
        effect.key = "missing"
        effect.default_factory = None
        env = FrozenDict({})
        store = {}
        
        result = handle_atomic_get(effect, env, store)
        
        assert result.value is None

    def test_atomic_get_missing_key_with_default_factory(self):
        effect = MockEffect()
        effect.key = "counter"
        effect.default_factory = lambda: 0
        env = FrozenDict({})
        store = {}
        
        result = handle_atomic_get(effect, env, store)
        
        assert result.value == 0
        assert result.store["__atomic_state__"]["counter"] == 0

    def test_atomic_get_existing_key(self):
        effect = MockEffect()
        effect.key = "existing"
        effect.default_factory = None
        env = FrozenDict({})
        store = {"__atomic_state__": {"existing": 42}}
        
        result = handle_atomic_get(effect, env, store)
        
        assert result.value == 42

    def test_atomic_update_creates_new_key(self):
        effect = MockEffect()
        effect.key = "counter"
        effect.updater = lambda x: (x or 0) + 1
        effect.default_factory = None
        env = FrozenDict({})
        store = {}
        
        result = handle_atomic_update(effect, env, store)
        
        assert result.value == 1
        assert result.store["__atomic_state__"]["counter"] == 1

    def test_atomic_update_modifies_existing_key(self):
        effect = MockEffect()
        effect.key = "counter"
        effect.updater = lambda x: x + 10
        effect.default_factory = None
        env = FrozenDict({})
        store = {"__atomic_state__": {"counter": 5}}
        
        result = handle_atomic_update(effect, env, store)
        
        assert result.value == 15
        assert result.store["__atomic_state__"]["counter"] == 15

    def test_atomic_update_with_default_factory(self):
        effect = MockEffect()
        effect.key = "list"
        effect.updater = lambda x: x + ["item"]
        effect.default_factory = list
        env = FrozenDict({})
        store = {}
        
        result = handle_atomic_update(effect, env, store)
        
        assert result.value == ["item"]
        assert result.store["__atomic_state__"]["list"] == ["item"]
