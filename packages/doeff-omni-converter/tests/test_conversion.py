"""Tests for effect-based conversion."""


from doeff_omni_converter import (
    RULEBOOK_KEY,
    AutoData,
    ConvertEffect,
    F,
    KleisliEdge,
    KleisliRuleBook,
    convert,
    convert_handler_interceptor,
)

from doeff import do
from doeff.cesk.runtime import SyncRuntime


class TestAutoData:
    """Tests for AutoData."""

    def test_creation(self):
        """Test creating AutoData."""
        data = AutoData("test_value", F.path)
        assert data.value == "test_value"
        assert data.format == F.path

    def test_cast(self):
        """Test cast (reinterpret format without conversion)."""
        data = AutoData("test_value", F.path)
        casted = data.cast(F.bytes_)

        assert casted.value == "test_value"  # Same value
        assert casted.format == F.bytes_  # Different format

    def test_map_value(self):
        """Test map_value transformation."""
        data = AutoData(5, F.path)
        mapped = data.map_value(lambda x: x * 2)

        assert mapped.value == 10
        assert mapped.format == F.path

    def test_to_returns_convert_effect(self):
        """Test that .to() returns a ConvertEffect."""
        data = AutoData("test", F.path)
        effect = data.to(F.numpy())

        assert isinstance(effect, ConvertEffect)
        assert effect.data == data
        assert effect.target_format == F.numpy()


class TestConvertEffect:
    """Tests for ConvertEffect."""

    def test_effect_creation(self):
        """Test creating ConvertEffect."""
        data = AutoData("test", F.path)
        effect = convert(data, F.numpy())

        assert isinstance(effect, ConvertEffect)
        assert effect.data == data
        assert effect.target_format == F.numpy()

    def test_effect_intercept(self):
        """Test that intercept returns self (no nested programs)."""
        data = AutoData("test", F.path)
        effect = ConvertEffect(data=data, target_format=F.numpy())

        intercepted = effect.intercept(lambda e: e)
        assert intercepted is effect


class TestConversionHandler:
    """Tests for conversion handler integration."""

    def test_simple_conversion(self):
        """Test a simple conversion through the handler."""
        runtime = SyncRuntime()

        # Define a simple converter
        @do
        def load_image(path):
            return {"loaded": path}

        def rules(fmt):
            if fmt == F.path:
                return [KleisliEdge(load_image, F.numpy(), 1, "load")]
            return []

        rulebook = KleisliRuleBook([rules])

        @do
        def pipeline():
            img = AutoData("/path/to/img.jpg", F.path)
            result = yield img.to(F.numpy())
            return result

        result = runtime.run(
            pipeline().intercept(convert_handler_interceptor), env={RULEBOOK_KEY: rulebook}
        )

        assert isinstance(result.value, AutoData)
        assert result.value.format == F.numpy()
        assert result.value.value == {"loaded": "/path/to/img.jpg"}

    def test_multi_step_conversion(self):
        """Test conversion with multiple steps."""
        runtime = SyncRuntime()

        # Define converters
        @do
        def path_to_pil(path):
            return {"pil": path}

        @do
        def pil_to_numpy(pil_data):
            return {"numpy": pil_data}

        def rules(fmt):
            if fmt == F.path:
                return [KleisliEdge(path_to_pil, F.pil(), 1, "to_pil")]
            if fmt == F.pil():
                return [KleisliEdge(pil_to_numpy, F.numpy(), 1, "to_numpy")]
            return []

        rulebook = KleisliRuleBook([rules])

        @do
        def pipeline():
            img = AutoData("image.jpg", F.path)
            result = yield img.to(F.numpy())
            return result

        result = runtime.run(
            pipeline().intercept(convert_handler_interceptor), env={RULEBOOK_KEY: rulebook}
        )

        assert result.value.format == F.numpy()
        # Check the nested conversion happened
        assert result.value.value == {"numpy": {"pil": "image.jpg"}}

    def test_no_conversion_needed(self):
        """Test conversion when source equals target."""
        runtime = SyncRuntime()
        rulebook = KleisliRuleBook([])

        @do
        def pipeline():
            img = AutoData("data", F.numpy())
            result = yield img.to(F.numpy())
            return result

        result = runtime.run(
            pipeline().intercept(convert_handler_interceptor), env={RULEBOOK_KEY: rulebook}
        )

        assert result.value.value == "data"
        assert result.value.format == F.numpy()

    def test_conversion_logs_events(self):
        """Test that conversion produces log events."""
        runtime = SyncRuntime()

        @do
        def load_image(path):
            return {"loaded": path}

        def rules(fmt):
            if fmt == F.path:
                return [KleisliEdge(load_image, F.numpy(), 1, "load")]
            return []

        rulebook = KleisliRuleBook([rules])

        @do
        def pipeline():
            img = AutoData("/img.jpg", F.path)
            result = yield img.to(F.numpy())
            return result

        result = runtime.run(
            pipeline().intercept(convert_handler_interceptor), env={RULEBOOK_KEY: rulebook}
        )

        # Check that logs were produced
        log_events = [msg.get("event") for msg in result.log if isinstance(msg, dict)]
        assert "convert_start" in log_events
        assert "convert_path" in log_events
        assert "convert_step" in log_events
        assert "convert_complete" in log_events

    def test_missing_rulebook_raises(self):
        """Test that missing rulebook raises appropriate error."""
        runtime = SyncRuntime()

        @do
        def pipeline():
            img = AutoData("/img.jpg", F.path)
            result = yield img.to(F.numpy())
            return result

        # Run without rulebook in environment
        result = runtime.run(pipeline().intercept(convert_handler_interceptor), env={})

        # Should have an error
        assert result.error is not None

    def test_no_path_raises(self):
        """Test that missing conversion path raises error."""
        runtime = SyncRuntime()
        rulebook = KleisliRuleBook([])

        @do
        def pipeline():
            img = AutoData("/img.jpg", F.path)
            result = yield img.to(F.numpy())  # No rules to convert!
            return result

        result = runtime.run(
            pipeline().intercept(convert_handler_interceptor), env={RULEBOOK_KEY: rulebook}
        )

        assert result.error is not None


class TestEffectfulConverters:
    """Tests for converters using various effects."""

    def test_converter_with_logging(self):
        """Test converter that uses tell effect for logging."""
        from doeff.effects import tell

        runtime = SyncRuntime()

        @do
        def logging_converter(data):
            yield tell({"custom": "log", "data": data})
            return {"converted": data}

        def rules(fmt):
            if fmt == F.path:
                return [KleisliEdge(logging_converter, F.numpy(), 1, "log_load")]
            return []

        rulebook = KleisliRuleBook([rules])

        @do
        def pipeline():
            img = AutoData("test", F.path)
            result = yield img.to(F.numpy())
            return result

        result = runtime.run(
            pipeline().intercept(convert_handler_interceptor), env={RULEBOOK_KEY: rulebook}
        )

        # Check custom log was captured
        custom_logs = [
            msg for msg in result.log if isinstance(msg, dict) and msg.get("custom") == "log"
        ]
        assert len(custom_logs) == 1
        assert custom_logs[0]["data"] == "test"

    def test_converter_with_ask(self):
        """Test converter that uses ask effect for config."""
        from doeff.effects import ask

        runtime = SyncRuntime()

        @do
        def config_converter(data):
            config = yield ask("conversion_config")
            return {"data": data, "config": config}

        def rules(fmt):
            if fmt == F.path:
                return [KleisliEdge(config_converter, F.numpy(), 1, "config_load")]
            return []

        rulebook = KleisliRuleBook([rules])

        @do
        def pipeline():
            img = AutoData("test", F.path)
            result = yield img.to(F.numpy())
            return result

        result = runtime.run(
            pipeline().intercept(convert_handler_interceptor),
            env={RULEBOOK_KEY: rulebook, "conversion_config": {"setting": "value"}},
        )

        assert result.value.value["config"] == {"setting": "value"}
