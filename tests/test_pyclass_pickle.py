"""TDD: All doeff_vm pyclasses that users encounter must be picklable.

Regression test for: "cannot pickle 'builtins.Ok' object"
Root cause: pyclass definitions missing `module = "doeff_vm.doeff_vm"` and `__reduce__`.
"""

import pickle

import cloudpickle
from doeff_vm.doeff_vm import Err, Ok


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

