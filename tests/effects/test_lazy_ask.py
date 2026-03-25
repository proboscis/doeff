"""Tests for lazy_ask handler — SPEC-EFF-001 lazy program evaluation."""

from doeff import do, run
from doeff.program import WithHandler

from doeff_core_effects.effects import Ask, Local, Try
from doeff_core_effects.handlers import reader, lazy_ask, try_handler
from doeff_core_effects.scheduler import scheduled, Spawn, Gather, Wait


def run_with_lazy(program, env=None):
    """Compose reader + scheduler + lazy_ask + try_handler and run.

    Handler stack (outer to inner):
        reader → scheduler → lazy_ask → try_handler → body
    """
    if env is None:
        env = {}
    body = WithHandler(try_handler(), program)
    body = WithHandler(lazy_ask(), body)
    body = scheduled(body)
    body = WithHandler(reader(env=env), body)
    return run(body)


class TestLazyAskBasic:
    def test_plain_value_passthrough(self):
        @do
        def program():
            return (yield Ask("key"))

        assert run_with_lazy(program(), env={"key": "plain_value"}) == "plain_value"

    def test_lazy_evaluation(self):
        calls = [0]

        @do
        def service():
            calls[0] += 1
            if False:
                yield
            return 42

        @do
        def program():
            return (yield Ask("svc"))

        assert run_with_lazy(program(), env={"svc": service()}) == 42
        assert calls[0] == 1

    def test_lazy_caches(self):
        calls = [0]

        @do
        def service():
            calls[0] += 1
            if False:
                yield
            return 42

        @do
        def program():
            first = yield Ask("svc")
            second = yield Ask("svc")
            return (first, second)

        result = run_with_lazy(program(), env={"svc": service()})
        assert result == (42, 42)
        assert calls[0] == 1

    def test_distinct_keys_independent(self):
        calls = {"a": 0, "b": 0}

        @do
        def svc_a():
            calls["a"] += 1
            if False:
                yield
            return "a"

        @do
        def svc_b():
            calls["b"] += 1
            if False:
                yield
            return "b"

        @do
        def program():
            a = yield Ask("a")
            b = yield Ask("b")
            return (a, b)

        result = run_with_lazy(program(), env={"a": svc_a(), "b": svc_b()})
        assert result == ("a", "b")
        assert calls == {"a": 1, "b": 1}


class TestLazyAskConcurrency:
    def test_concurrent_single_evaluation(self):
        """Two spawned tasks asking same key → evaluated once."""
        calls = [0]

        @do
        def service():
            calls[0] += 1
            if False:
                yield
            return 42

        @do
        def child():
            return (yield Ask("svc"))

        @do
        def program():
            t1 = yield Spawn(child())
            t2 = yield Spawn(child())
            return (yield Gather(t1, t2))

        result = run_with_lazy(program(), env={"svc": service()})
        assert result == [42, 42]
        assert calls[0] == 1

    def test_three_concurrent_not_circular(self):
        """Three concurrent asks must not be treated as circular dependency."""

        @do
        def service():
            if False:
                yield
            return "resolved"

        @do
        def child():
            return (yield Ask("svc"))

        @do
        def program():
            t1 = yield Spawn(child())
            t2 = yield Spawn(child())
            t3 = yield Spawn(child())
            return (yield Gather(t1, t2, t3))

        result = run_with_lazy(program(), env={"svc": service()})
        assert result == ["resolved", "resolved", "resolved"]


class TestLazyAskLocal:
    def test_local_plain_override(self):
        @do
        def program():
            outer = yield Ask("key")
            inner = yield Local({"key": "override"}, Ask("key"))
            after = yield Ask("key")
            return (outer, inner, after)

        result = run_with_lazy(program(), env={"key": "original"})
        assert result == ("original", "override", "original")

    def test_cache_invalidation_on_local_exit(self):
        call_count = [0]

        @do
        def make_service():
            call_count[0] += 1
            db = yield Ask("db_url")
            return f"Service({db})"

        @do
        def program():
            inner = yield Local({"db_url": "test.db"}, Ask("service"))
            outer = yield Ask("service")
            return (inner, outer)

        result = run_with_lazy(
            program(),
            env={"db_url": "prod.db", "service": make_service()},
        )
        assert result == ("Service(test.db)", "Service(prod.db)")
        assert call_count[0] == 2

    def test_non_dependent_cache_survives_local_exit(self):
        service_count = [0]
        logger_count = [0]

        @do
        def make_service():
            service_count[0] += 1
            db = yield Ask("db_url")
            return f"Service({db})"

        @do
        def make_logger():
            logger_count[0] += 1
            if False:
                yield
            return "Logger()"

        @do
        def program():
            _ = yield Ask("logger")
            inner_service = yield Local({"db_url": "test.db"}, Ask("service"))
            outer_service = yield Ask("service")
            outer_logger = yield Ask("logger")
            return (inner_service, outer_service, outer_logger)

        result = run_with_lazy(
            program(),
            env={
                "db_url": "prod.db",
                "service": make_service(),
                "logger": make_logger(),
            },
        )
        assert result == ("Service(test.db)", "Service(prod.db)", "Logger()")
        assert service_count[0] == 2
        assert logger_count[0] == 1

    def test_nested_local_with_lazy_values(self):
        call_count = [0]

        @do
        def make_service():
            call_count[0] += 1
            db = yield Ask("db_url")
            host = yield Ask("host")
            return f"Service({db},{host})"

        @do
        def program():
            inner = yield Local(
                {"host": "inner-host"},
                Local({"db_url": "test.db"}, Ask("service")),
            )
            outer = yield Ask("service")
            return (inner, outer)

        result = run_with_lazy(
            program(),
            env={
                "db_url": "prod.db",
                "host": "prod-host",
                "service": make_service(),
            },
        )
        assert result == ("Service(test.db,inner-host)", "Service(prod.db,prod-host)")
        assert call_count[0] == 2

    def test_local_visible_to_spawned_tasks(self):
        @do
        def worker():
            return (yield Ask("key"))

        @do
        def program():
            task = yield Spawn(worker())
            return (yield Wait(task))

        @do
        def top():
            return (yield Local({"key": "override"}, program()))

        result = run_with_lazy(top(), env={"key": "global"})
        assert result == "override"

    def test_local_lazy_concurrent(self):
        call_count = [0]

        @do
        def expensive():
            call_count[0] += 1
            if False:
                yield
            return 42

        @do
        def worker():
            return (yield Ask("svc"))

        @do
        def program():
            t1 = yield Spawn(worker())
            t2 = yield Spawn(worker())
            r1 = yield Wait(t1)
            r2 = yield Wait(t2)
            return (r1, r2)

        @do
        def top():
            return (yield Local({"svc": expensive()}, program()))

        result = run_with_lazy(top(), env={})
        assert result == (42, 42)
        assert call_count[0] == 1


class TestLazyAskScopeIsolation:
    def test_concurrent_local_isolated_cache(self):
        """Three tasks with different Local overrides: override-dependent entries
        are isolated per scope, non-dependent entries shared (evaluated once)."""
        logger_count = [0]
        service_count = [0]

        @do
        def make_logger():
            logger_count[0] += 1
            if False:
                yield
            return "Logger()"

        @do
        def make_service():
            service_count[0] += 1
            db = yield Ask("db_url")
            logger = yield Ask("logger")
            return f"Svc({db},{logger})"

        @do
        def worker(db):
            return (yield Local({"db_url": db}, Ask("service")))

        @do
        def program():
            t1 = yield Spawn(worker("db1"))
            t2 = yield Spawn(worker("db2"))
            t3 = yield Spawn(worker("db3"))
            return (yield Gather(t1, t2, t3))

        result = run_with_lazy(
            program(),
            env={
                "db_url": "prod",
                "service": make_service(),
                "logger": make_logger(),
            },
        )
        assert result == [
            "Svc(db1,Logger())",
            "Svc(db2,Logger())",
            "Svc(db3,Logger())",
        ]
        assert logger_count[0] == 1, "logger should be evaluated once (shared cache)"
        assert service_count[0] == 3, "service should be evaluated per scope"


class TestLazyAskErrorHandling:
    def test_lazy_failure_propagates(self):
        @do
        def failing_service():
            raise ValueError("boom")

        @do
        def program():
            return (yield Try(Ask("svc")))

        result = run_with_lazy(program(), env={"svc": failing_service()})
        assert hasattr(result, "error") and isinstance(result.error, ValueError)

    def test_missing_key_error(self):
        @do
        def program():
            return (yield Try(Ask("missing")))

        result = run_with_lazy(program(), env={})
        assert hasattr(result, "error") and isinstance(result.error, KeyError)
