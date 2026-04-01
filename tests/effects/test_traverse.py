"""Tests for doeff-traverse: Fail, Traverse, Reduce, Zip, Inspect."""

from doeff import do, run
from doeff.program import WithHandler

from doeff_core_effects.handlers import try_handler
from doeff_core_effects.scheduler import scheduled

from doeff_traverse.effects import Fail, Traverse, Reduce, Zip, Inspect
from doeff_traverse.handlers import sequential, normalize_to_none, fail_handler
from doeff_traverse.helpers import try_call
from doeff_traverse.collection import Collection


def run_with(program, handlers=None):
    """Run with scheduler + fail_handler + sequential + given handlers.

    Handler stack (outer to inner):
        scheduler → sequential → fail_handler → [extra handlers] → try_handler → body

    Extra handlers sit between try_handler and fail_handler,
    so they intercept Fail before fail_handler raises it.
    """
    body = WithHandler(try_handler, program)
    if handlers:
        for h in handlers:
            body = WithHandler(h, body)
    body = WithHandler(fail_handler, body)
    body = WithHandler(sequential(), body)
    body = scheduled(body)
    return run(body)


# ---------------------------------------------------------------------------
# Fail effect
# ---------------------------------------------------------------------------

class TestFail:
    def test_fail_unhandled_raises(self):
        """Fail without handler raises the cause as exception."""
        @do
        def program():
            yield Fail(ValueError("boom"))

        import pytest
        with pytest.raises(ValueError, match="boom"):
            run_with(program())

    def test_fail_with_normalize_resumes_none(self):
        """normalize_to_none handler resumes with None at fail site."""
        @do
        def program():
            x = yield Fail(ValueError("boom"))
            return x

        result = run_with(program(), handlers=[normalize_to_none])
        assert result is None

    def test_fail_continues_after_resume(self):
        """After Fail is handled, the rest of the body continues."""
        @do
        def program():
            a = 10
            b = yield Fail(ValueError("oops"))
            return (a, b)

        result = run_with(program(), handlers=[normalize_to_none])
        assert result == (10, None)


# ---------------------------------------------------------------------------
# try_call
# ---------------------------------------------------------------------------

class TestTryCall:
    def test_try_call_success(self):
        """try_call passes through on success."""
        @do
        def program():
            return (yield try_call(int, "42"))

        assert run_with(program()) == 42

    def test_try_call_failure_unhandled(self):
        """try_call failure without handler raises."""
        @do
        def program():
            return (yield try_call(int, "not_a_number"))

        import pytest
        with pytest.raises(ValueError):
            run_with(program())

    def test_try_call_failure_normalized(self):
        """try_call failure with normalize_to_none resumes None."""
        @do
        def program():
            result = yield try_call(int, "not_a_number")
            return result

        result = run_with(program(), handlers=[normalize_to_none])
        assert result is None

    def test_try_call_failure_continues(self):
        """After try_call Fail is handled, rest of body continues."""
        @do
        def program():
            a = yield try_call(int, "42")
            b = yield try_call(int, "bad")
            c = yield try_call(int, "7")
            return (a, b, c)

        result = run_with(program(), handlers=[normalize_to_none])
        assert result == (42, None, 7)


# ---------------------------------------------------------------------------
# Traverse (sequential)
# ---------------------------------------------------------------------------

class TestTraverse:
    def test_traverse_simple(self):
        """Traverse applies f to each item sequentially."""
        @do
        def double(x):
            if False:
                yield
            return x * 2

        @do
        def program():
            result = yield Traverse(double, [1, 2, 3])
            return result

        col = run_with(program())
        assert isinstance(col, Collection)
        assert col.valid_values == [2, 4, 6]

    def test_traverse_with_effect(self):
        """Traverse works with effectful functions."""
        @do
        def process(x):
            return x + 1

        @do
        def program():
            return (yield Traverse(process, [10, 20, 30]))

        col = run_with(program())
        assert col.valid_values == [11, 21, 31]

    def test_traverse_item_failure_isolated(self):
        """One item failing doesn't affect others."""
        @do
        def process(x):
            if x == 2:
                raise ValueError("bad item")
            if False:
                yield
            return x * 10

        @do
        def program():
            return (yield Traverse(process, [1, 2, 3]))

        col = run_with(program())
        assert col.valid_values == [10, 30]
        assert len(col.failed_items) == 1
        assert col.failed_items[0].index == 1

    def test_traverse_chained(self):
        """Two Traverses can be chained."""
        @do
        def step1(x):
            if False:
                yield
            return x + 1

        @do
        def step2(x):
            if False:
                yield
            return x * 10

        @do
        def program():
            a = yield Traverse(step1, [1, 2, 3])
            b = yield Traverse(step2, a)
            return b

        col = run_with(program())
        assert col.valid_values == [20, 30, 40]

    def test_traverse_empty(self):
        """Traverse on empty list returns empty Collection."""
        @do
        def process(x):
            if False:
                yield
            return x

        @do
        def program():
            return (yield Traverse(process, []))

        col = run_with(program())
        assert col.valid_values == []
        assert len(col) == 0


# ---------------------------------------------------------------------------
# Reduce
# ---------------------------------------------------------------------------

class TestReduce:
    def test_reduce_sum(self):
        """Reduce folds valid values."""
        @do
        def add(acc, x):
            if False:
                yield
            return acc + x

        @do
        def program():
            col = yield Traverse(lambda x: _pure(x * 2), [1, 2, 3])
            return (yield Reduce(add, 0, col))

        result = run_with(program())
        assert result == 12

    def test_reduce_skips_failed(self):
        """Reduce only folds valid items."""
        @do
        def process(x):
            if x == 2:
                raise ValueError("bad")
            if False:
                yield
            return x

        @do
        def add(acc, x):
            if False:
                yield
            return acc + x

        @do
        def program():
            col = yield Traverse(process, [1, 2, 3])
            return (yield Reduce(add, 0, col))

        result = run_with(program())
        assert result == 4  # 1 + 3, skipping failed item 2


# ---------------------------------------------------------------------------
# Zip
# ---------------------------------------------------------------------------

class TestZip:
    def test_zip_simple(self):
        """Zip combines two collections by index."""
        @do
        def program():
            a = yield Traverse(lambda x: _pure(x), [1, 2, 3])
            b = yield Traverse(lambda x: _pure(x * 10), [1, 2, 3])
            return (yield Zip(a, b))

        col = run_with(program())
        assert col.valid_values == [(1, 10), (2, 20), (3, 30)]

    def test_zip_failure_union(self):
        """Zip marks items as failed if either side failed."""
        @do
        def fail_on_2(x):
            if x == 2:
                raise ValueError("bad")
            if False:
                yield
            return x

        @do
        def program():
            a = yield Traverse(fail_on_2, [1, 2, 3])
            b = yield Traverse(lambda x: _pure(x * 10), [1, 2, 3])
            zipped = yield Zip(a, b)
            return zipped

        col = run_with(program())
        assert len(col.valid_items) == 2
        assert col.valid_values == [(1, 10), (3, 30)]
        assert len(col.failed_items) == 1


# ---------------------------------------------------------------------------
# Inspect
# ---------------------------------------------------------------------------

class TestInspect:
    def test_inspect_shows_history(self):
        """Inspect returns per-item history."""
        @do
        def process(x):
            if x == 2:
                raise ValueError("bad")
            if False:
                yield
            return x * 10

        @do
        def program():
            col = yield Traverse(process, [1, 2, 3])
            return (yield Inspect(col))

        items = run_with(program())
        assert len(items) == 3
        assert items[0].value == 10
        assert items[0].failed is False
        assert items[1].failed is True
        assert items[2].value == 30


# ---------------------------------------------------------------------------
# Handler composition
# ---------------------------------------------------------------------------

class TestComposition:
    def test_fail_inside_traverse_with_normalize(self):
        """Fail + normalize inside Traverse: failed items get None, others continue."""
        @do
        def process(x):
            if x == 2:
                result = yield Fail(ValueError("bad"))
                return result  # None from normalize
            if False:
                yield
            return x * 10

        @do
        def program():
            return (yield Traverse(process, [1, 2, 3]))

        col = run_with(program(), handlers=[normalize_to_none])
        # With normalize, Fail resumes with None — item doesn't "fail"
        # The function returns None as its value
        values = col.valid_values
        assert 10 in values
        assert None in values
        assert 30 in values

    def test_full_pipeline(self):
        """Full pipeline: Traverse → Reduce → Traverse with stats."""
        @do
        def compute(x):
            if False:
                yield
            return x ** 2

        @do
        def with_stats(pair):
            x, mean = pair
            if False:
                yield
            return x - mean

        @do
        def add(acc, x):
            if False:
                yield
            return acc + x

        @do
        def program():
            squares = yield Traverse(compute, [1, 2, 3, 4])
            total = yield Reduce(add, 0, squares)
            mean = total / 4  # we know all 4 succeed
            # Use mean in next stage — monadic dependency
            centered = yield Traverse(
                lambda x: with_stats((x, mean)),
                squares,
            )
            return centered

        col = run_with(program())
        # squares: [1, 4, 9, 16], mean: 7.5
        # centered: [1-7.5, 4-7.5, 9-7.5, 16-7.5] = [-6.5, -3.5, 1.5, 8.5]
        assert col.valid_values == [-6.5, -3.5, 1.5, 8.5]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@do
def _pure(x):
    """Lift a plain value into a @do function."""
    if False:
        yield
    return x
