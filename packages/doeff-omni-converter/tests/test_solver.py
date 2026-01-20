"""Tests for A* solver."""

import pytest
from doeff_omni_converter import (
    F,
    KleisliEdge,
    KleisliRuleBook,
    can_convert,
    estimate_cost,
    solve,
    solve_lazy,
)

from doeff.program import Program


def identity_converter(x):
    """Simple identity converter returning pure Program."""
    return Program.pure(x)


class TestSolver:
    """Tests for A* solver."""

    def test_direct_conversion(self):
        """Test finding a direct conversion path."""

        def rules(fmt):
            if fmt == F.path:
                return [KleisliEdge(identity_converter, F.numpy(), 1, "load")]
            return []

        rulebook = KleisliRuleBook([rules])
        path = solve(rulebook, F.path, F.numpy())

        assert len(path) == 1
        assert path[0].name == "load"
        assert path[0].dst_format == F.numpy()

    def test_multi_step_conversion(self):
        """Test finding a multi-step conversion path."""

        def rules(fmt):
            if fmt == F.path:
                return [KleisliEdge(identity_converter, F.numpy(), 1, "load")]
            if fmt == F.numpy():
                return [KleisliEdge(identity_converter, F.torch(), 2, "to_torch")]
            return []

        rulebook = KleisliRuleBook([rules])
        path = solve(rulebook, F.path, F.torch())

        assert len(path) == 2
        assert path[0].name == "load"
        assert path[1].name == "to_torch"

    def test_same_format_empty_path(self):
        """Test that converting to same format returns empty path."""
        rulebook = KleisliRuleBook([])
        path = solve(rulebook, F.path, F.path)

        assert path == []

    def test_optimal_path_selection(self):
        """Test that solver finds optimal (lowest cost) path."""

        def rules(fmt):
            if fmt == F.path:
                return [
                    KleisliEdge(identity_converter, F.numpy(), 10, "expensive"),
                    KleisliEdge(identity_converter, F.pil(), 1, "cheap_pil"),
                ]
            if fmt == F.pil():
                return [
                    KleisliEdge(identity_converter, F.numpy(), 1, "pil_to_numpy"),
                ]
            return []

        rulebook = KleisliRuleBook([rules])
        path = solve(rulebook, F.path, F.numpy())

        # Should prefer path -> pil (1) -> numpy (1) = 2
        # Over path -> numpy (10) = 10
        assert len(path) == 2
        assert path[0].name == "cheap_pil"
        assert path[1].name == "pil_to_numpy"

    def test_no_path_raises(self):
        """Test that missing path raises ValueError."""
        rulebook = KleisliRuleBook([])

        with pytest.raises(ValueError, match="No conversion path"):
            solve(rulebook, F.path, F.numpy())

    def test_solve_lazy_returns_none(self):
        """Test that solve_lazy returns None on failure."""
        rulebook = KleisliRuleBook([])

        result = solve_lazy(rulebook, F.path, F.numpy())
        assert result is None

    def test_can_convert(self):
        """Test can_convert helper."""

        def rules(fmt):
            if fmt == F.path:
                return [KleisliEdge(identity_converter, F.numpy(), 1, "load")]
            return []

        rulebook = KleisliRuleBook([rules])

        assert can_convert(rulebook, F.path, F.numpy()) is True
        assert can_convert(rulebook, F.numpy(), F.path) is False

    def test_estimate_cost(self):
        """Test estimate_cost helper."""

        def rules(fmt):
            if fmt == F.path:
                return [KleisliEdge(identity_converter, F.numpy(), 5, "load")]
            if fmt == F.numpy():
                return [KleisliEdge(identity_converter, F.torch(), 3, "to_torch")]
            return []

        rulebook = KleisliRuleBook([rules])

        assert estimate_cost(rulebook, F.path, F.torch()) == 8
        assert estimate_cost(rulebook, F.torch(), F.path) is None


class TestRuleBook:
    """Tests for KleisliRuleBook."""

    def test_multiple_rules(self):
        """Test rulebook with multiple rule functions."""

        def rules1(fmt):
            if fmt == F.path:
                return [KleisliEdge(identity_converter, F.numpy(), 1, "load")]
            return []

        def rules2(fmt):
            if fmt == F.numpy():
                return [KleisliEdge(identity_converter, F.torch(), 1, "to_torch")]
            return []

        rulebook = KleisliRuleBook([rules1, rules2])

        assert len(rulebook.get_edges(F.path)) == 1
        assert len(rulebook.get_edges(F.numpy())) == 1

    def test_add_rule(self):
        """Test adding rules to rulebook."""

        def rules1(fmt):
            if fmt == F.path:
                return [KleisliEdge(identity_converter, F.numpy(), 1, "load")]
            return []

        def rules2(fmt):
            if fmt == F.path:
                return [KleisliEdge(identity_converter, F.pil(), 1, "load_pil")]
            return []

        rulebook1 = KleisliRuleBook([rules1])
        rulebook2 = rulebook1.add_rule(rules2)

        # Original unchanged
        assert len(rulebook1.get_edges(F.path)) == 1
        # New has both
        assert len(rulebook2.get_edges(F.path)) == 2

    def test_merge_rulebooks(self):
        """Test merging two rulebooks."""

        def rules1(fmt):
            if fmt == F.path:
                return [KleisliEdge(identity_converter, F.numpy(), 1, "load")]
            return []

        def rules2(fmt):
            if fmt == F.numpy():
                return [KleisliEdge(identity_converter, F.torch(), 1, "to_torch")]
            return []

        rulebook1 = KleisliRuleBook([rules1])
        rulebook2 = KleisliRuleBook([rules2])
        merged = rulebook1.merge(rulebook2)

        path = solve(merged, F.path, F.torch())
        assert len(path) == 2
