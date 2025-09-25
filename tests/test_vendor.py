"""Tests for doeff._vendor module, specifically WStep unique ID generation."""


import pytest

from doeff._vendor import WNode, WStep


class UnhashableType:
    """A class that is not hashable."""
    def __init__(self, value):
        self.value = value


def test_wstep_unique_id_generation():
    """Test that each WStep instance gets a unique ID."""
    node1 = WNode(1)
    node2 = WNode(2)
    output_node = WNode(3)

    # Create multiple WStep instances with same inputs
    step1 = WStep(inputs=(node1, node2), output=output_node)
    step2 = WStep(inputs=(node1, node2), output=output_node)
    step3 = WStep(inputs=(node1, node2), output=output_node)

    # Each should have a different unique ID
    assert step1._unique_id != step2._unique_id
    assert step2._unique_id != step3._unique_id
    assert step1._unique_id != step3._unique_id

    # And different hashes
    assert hash(step1) != hash(step2)
    assert hash(step2) != hash(step3)
    assert hash(step1) != hash(step3)


def test_wstep_hashable_with_unhashable_components():
    """Test that WStep is hashable even with unhashable inputs/output."""
    # Since WStep now uses unique ID for hashing, it should work
    # even with unhashable components
    unhashable_item = UnhashableType("test")
    unhashable_output = UnhashableType("output")

    # Should not raise any errors - WStep uses unique ID for hash
    step = WStep(inputs=(unhashable_item,), output=unhashable_output)

    # Verify we can hash the step
    assert hash(step) is not None
    assert step._unique_id is not None


def test_wstep_empty_inputs():
    """Test that WStep works with empty inputs tuple."""
    output_node = WNode(1)

    step = WStep(inputs=(), output=output_node)

    # Verify we can hash the step and it has a unique ID
    assert hash(step) is not None
    assert step._unique_id is not None


def test_wstep_with_meta():
    """Test that WStep works with metadata."""
    node1 = WNode(1)
    output_node = WNode(2)

    # Meta can contain any values since it's not used in __hash__
    meta = {"key": "value", "list": [1, 2, 3]}

    step = WStep(inputs=(node1,), output=output_node, meta=meta)

    # Verify we can hash the step and it has metadata
    assert hash(step) is not None
    assert step.meta == meta
    assert step._unique_id is not None


def test_wstep_hash_consistency():
    """Test that WStep hash is consistent for the same instance."""
    node1 = WNode(1)
    node2 = WNode(2)
    output_node = WNode(3)

    step = WStep(inputs=(node1, node2), output=output_node)

    # Same instance should always produce same hash
    hash1 = hash(step)
    hash2 = hash(step)
    assert hash1 == hash2

    # Hash should be based on unique ID
    assert hash(step) == hash(step._unique_id)


def test_wstep_unique_id_format():
    """Test that unique ID has expected format (UUID)."""
    import uuid

    node1 = WNode(1)
    output_node = WNode(2)

    step = WStep(inputs=(node1,), output=output_node)

    # Should be a valid UUID string
    try:
        uuid_obj = uuid.UUID(step._unique_id)
        assert str(uuid_obj) == step._unique_id
    except ValueError:
        pytest.fail(f"Unique ID '{step._unique_id}' is not a valid UUID")
