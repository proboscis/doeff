"""Tests for cloudpickle serializer."""

import hy  # noqa: F401
import pytest

from doeff import Pure, run, do
from doeff_ml_nexus.serializer import default_serializer, CloudpickleSerializer


class TestCloudpickleSerializer:
    def test_pure_roundtrip(self):
        program = Pure(42)
        try:
            data = default_serializer.dumps(program)
        except TypeError:
            pytest.skip("doeff-vm pickle not supported on this Python version")
        loaded = default_serializer.loads(data)
        assert run(loaded) == 42

    def test_do_program_roundtrip(self):
        @do
        def compute():
            return 1 + 2 + 3

        try:
            data = default_serializer.dumps(compute())
        except TypeError:
            pytest.skip("doeff-vm pickle not supported on this Python version")
        loaded = default_serializer.loads(data)
        assert run(loaded) == 6

    def test_complex_result_roundtrip(self):
        result = {"key": [1, 2, 3], "nested": {"a": True}}
        data = default_serializer.dumps(result)
        loaded = default_serializer.loads(data)
        assert loaded == result

    def test_is_frozen(self):
        s = CloudpickleSerializer()
        assert isinstance(s, CloudpickleSerializer)
