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


def test_wstep_eq_only_compares_unique_id():
    """Test that WStep equality only compares _unique_id, not other fields."""
    node1 = WNode(1)
    node2 = WNode(2)
    output_node = WNode(3)

    # Create two steps with same unique_id but different meta
    step1 = WStep(inputs=(node1,), output=output_node, meta={"key": "value1"})

    # Manually create step2 with same _unique_id but different meta
    step2 = WStep(
        inputs=(node2,),  # different inputs
        output=WNode(999),  # different output
        meta={"key": "value2"},  # different meta
        _unique_id=step1._unique_id,  # same unique_id
    )

    # Should be equal because _unique_id is the same
    assert step1 == step2
    assert hash(step1) == hash(step2)

    # Different unique_id means not equal
    step3 = WStep(inputs=(node1,), output=output_node, meta={"key": "value1"})
    assert step1 != step3  # different _unique_id even with same other fields


def test_wstep_eq_with_non_bool_comparison_in_meta():
    """Test that WStep equality works when meta contains objects with non-bool __eq__.

    This is the core fix for ISSUE-CORE-412: when meta contains numpy arrays,
    pandas DataFrames, or similar objects, their __eq__ returns non-bool values.
    The old dataclass-generated __eq__ would fail with:
    'ValueError: The truth value of a DataFrame is ambiguous'
    """
    import numpy as np

    node1 = WNode(1)
    output_node = WNode(2)

    # Create steps with numpy arrays in meta (numpy __eq__ returns array, not bool)
    array1 = np.array([1, 2, 3])
    array2 = np.array([4, 5, 6])

    step1 = WStep(inputs=(node1,), output=output_node, meta={"data": array1})
    step2 = WStep(inputs=(node1,), output=output_node, meta={"data": array2})

    # These should NOT raise ValueError - equality is based on _unique_id only
    assert step1 != step2  # different _unique_id
    assert step1 == step1  # same instance

    # Create step3 with same _unique_id as step1 but different array
    step3 = WStep(
        inputs=(node1,),
        output=output_node,
        meta={"data": array2},  # different array
        _unique_id=step1._unique_id,  # same unique_id
    )

    # Should be equal (same _unique_id) without raising ValueError
    assert step1 == step3


def test_wstep_set_operations_with_non_bool_meta():
    """Test that WStep works correctly in set operations with non-bool __eq__ meta.

    This tests the actual use case from ISSUE-CORE-412: merging graph.steps
    after Ray serialization/deserialization.
    """
    import numpy as np

    node1 = WNode(1)
    output_node = WNode(2)

    # Simulate the scenario: same WStep with numpy array in meta
    array = np.array([[1, 2], [3, 4]])
    original_step = WStep(inputs=(node1,), output=output_node, meta={"df": array})

    # Simulate Ray serialize/deserialize by creating new instance with same _unique_id
    deserialized_step = WStep(
        inputs=(node1,),
        output=output_node,
        meta={"df": array.copy()},  # copy of array (different object)
        _unique_id=original_step._unique_id,
    )

    # Set operations should work without ValueError
    parent_steps = {original_step}
    child_steps = {deserialized_step}

    # This is what _merge_context does - should not raise
    combined = set(parent_steps)
    combined.update(child_steps)

    # Should have only one step (same _unique_id)
    assert len(combined) == 1
