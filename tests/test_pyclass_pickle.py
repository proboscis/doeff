"""TDD: All doeff_vm pyclasses that users encounter must be picklable.

Regression test for: "cannot pickle 'builtins.Ok' object"
Root cause: pyclass definitions missing `module = "doeff_vm.doeff_vm"` and `__reduce__`.
"""

import pickle

import cloudpickle
import pytest

import pytest

from doeff_vm.doeff_vm import Err, Ok
# REMOVED: from doeff_vm.doeff_vm import TraceFrame, TraceHop, Var


# ---------------------------------------------------------------------------
# Ok / Err  (core Result types — used by cache)
# ---------------------------------------------------------------------------

class TestOkPickle:
    def test_pickle_roundtrip(self):
        ok = Ok(42)
        restored = pickle.loads(pickle.dumps(ok))
        assert restored.is_ok()
        assert restored.value == 42

    def test_cloudpickle_roundtrip(self):
        ok = Ok({"key": [1, 2, 3]})
        restored = cloudpickle.loads(cloudpickle.dumps(ok))
        assert restored.is_ok()
        assert restored.value == {"key": [1, 2, 3]}

    def test_nested_ok(self):
        nested = Ok(Ok(99))
        restored = pickle.loads(pickle.dumps(nested))
        assert restored.value.value == 99

    def test_ok_none(self):
        ok = Ok(None)
        restored = pickle.loads(pickle.dumps(ok))
        assert restored.value is None

    def test_module_path(self):
        """Ok.__module__ must be 'doeff_vm.doeff_vm', not 'builtins'."""
        assert Ok.__module__ == "doeff_vm.doeff_vm"


class TestErrPickle:
    def test_pickle_roundtrip(self):
        err = Err(ValueError("boom"))
        restored = pickle.loads(pickle.dumps(err))
        assert restored.is_err()
        assert isinstance(restored.error, ValueError)
        assert str(restored.error) == "boom"

    def test_cloudpickle_roundtrip(self):
        err = Err(RuntimeError("fail"))
        restored = cloudpickle.loads(cloudpickle.dumps(err))
        assert restored.is_err()
        assert isinstance(restored.error, RuntimeError)

    def test_err_with_captured_traceback(self):
        err = Err(ValueError("x"), "traceback text")
        restored = pickle.loads(pickle.dumps(err))
        assert restored.captured_traceback == "traceback text"

    def test_module_path(self):
        assert Err.__module__ == "doeff_vm.doeff_vm"


# ---------------------------------------------------------------------------
# TraceFrame / TraceHop  (appear inside Err tracebacks)
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="uses removed API: TraceFrame")
class TestTraceFramePickle:
    def test_pickle_roundtrip(self):
        frame = TraceFrame(func_name="foo", source_file="bar.py", source_line=10)
        restored = pickle.loads(pickle.dumps(frame))
        assert restored.func_name == "foo"
        assert restored.source_file == "bar.py"
        assert restored.source_line == 10

    def test_cloudpickle_roundtrip(self):
        frame = TraceFrame(func_name="f", source_file="x.py", source_line=1)
        restored = cloudpickle.loads(cloudpickle.dumps(frame))
        assert restored.func_name == "f"

    def test_module_path(self):
        assert TraceFrame.__module__ == "doeff_vm.doeff_vm"


@pytest.mark.skip(reason="uses removed API: TraceHop")
class TestTraceHopPickle:
    def test_pickle_roundtrip(self):
        frame = TraceFrame(func_name="f", source_file="a.py", source_line=5)
        hop = TraceHop(frames=[frame])
        restored = pickle.loads(pickle.dumps(hop))
        assert len(restored.frames) == 1
        assert restored.frames[0].func_name == "f"

    def test_empty_frames(self):
        hop = TraceHop(frames=[])
        restored = pickle.loads(pickle.dumps(hop))
        assert restored.frames == []

    def test_module_path(self):
        assert TraceHop.__module__ == "doeff_vm.doeff_vm"


# ---------------------------------------------------------------------------
# Var  (variable references in pipeline results)
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="uses removed API: Var")
class TestVarPickle:
    def test_pickle_roundtrip(self):
        var = Var(raw=42, owner_segment=7)
        restored = pickle.loads(pickle.dumps(var))
        assert restored.raw == 42
        assert restored.owner_segment == 7

    def test_cloudpickle_roundtrip(self):
        var = Var(raw=1, owner_segment=0)
        restored = cloudpickle.loads(cloudpickle.dumps(var))
        assert restored.raw == 1

    def test_module_path(self):
        assert Var.__module__ == "doeff_vm.doeff_vm"
