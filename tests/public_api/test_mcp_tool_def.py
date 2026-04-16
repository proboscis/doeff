"""Tests for McpToolDef and McpParamSchema dataclasses."""

from doeff.mcp import McpParamSchema, McpToolDef, _MISSING


def _dummy_handler(x, y):
    """Placeholder handler for testing."""
    return None


class TestMcpParamSchema:
    def test_basic_param(self):
        p = McpParamSchema(name="symbol", type="string", description="Stock symbol")
        assert p.name == "symbol"
        assert p.type == "string"
        assert p.description == "Stock symbol"
        assert p.required is True
        assert p.enum is None
        assert p.default is _MISSING

    def test_param_with_enum(self):
        p = McpParamSchema(
            name="side", type="string", description="Order side",
            enum=("buy", "sell"),
        )
        assert p.enum == ("buy", "sell")

    def test_param_optional_with_default(self):
        p = McpParamSchema(
            name="limit", type="integer", description="Max results",
            required=False, default=10,
        )
        assert p.required is False
        assert p.default == 10

    def test_to_json_schema_basic(self):
        p = McpParamSchema(name="x", type="string", description="test")
        schema = p.to_json_schema()
        assert schema == {"type": "string", "description": "test"}

    def test_to_json_schema_with_enum(self):
        p = McpParamSchema(
            name="side", type="string", description="Side",
            enum=("buy", "sell"),
        )
        schema = p.to_json_schema()
        assert schema["enum"] == ["buy", "sell"]

    def test_to_json_schema_with_default(self):
        p = McpParamSchema(
            name="limit", type="integer", description="Max",
            default=10,
        )
        schema = p.to_json_schema()
        assert schema["default"] == 10

    def test_frozen(self):
        p = McpParamSchema(name="x", type="string", description="test")
        try:
            p.name = "y"
            assert False, "should be frozen"
        except AttributeError:
            pass


class TestMcpToolDef:
    def _make_tool(self):
        return McpToolDef(
            name="submit-order",
            description="Submit a trading order",
            params=(
                McpParamSchema(name="symbol", type="string", description="Stock symbol code"),
                McpParamSchema(name="side", type="string", description="Order side", enum=("buy", "sell")),
                McpParamSchema(name="qty", type="integer", description="Quantity to trade"),
            ),
            handler=_dummy_handler,
        )

    def test_basic_fields(self):
        tool = self._make_tool()
        assert tool.name == "submit-order"
        assert tool.description == "Submit a trading order"
        assert len(tool.params) == 3

    def test_input_schema(self):
        tool = self._make_tool()
        schema = tool.input_schema()
        assert schema["type"] == "object"
        assert set(schema["properties"].keys()) == {"symbol", "side", "qty"}
        assert schema["required"] == ["symbol", "side", "qty"]
        assert schema["properties"]["side"]["enum"] == ["buy", "sell"]

    def test_input_schema_optional_params(self):
        tool = McpToolDef(
            name="test",
            description="test",
            params=(
                McpParamSchema(name="a", type="string", description="required"),
                McpParamSchema(name="b", type="string", description="optional", required=False),
            ),
            handler=_dummy_handler,
        )
        schema = tool.input_schema()
        assert schema["required"] == ["a"]

    def test_input_schema_no_required(self):
        tool = McpToolDef(
            name="test",
            description="test",
            params=(
                McpParamSchema(name="a", type="string", description="opt", required=False),
            ),
            handler=_dummy_handler,
        )
        schema = tool.input_schema()
        assert "required" not in schema

    def test_param_names(self):
        tool = self._make_tool()
        assert tool.param_names() == ("symbol", "side", "qty")

    def test_frozen(self):
        tool = self._make_tool()
        try:
            tool.name = "x"
            assert False, "should be frozen"
        except AttributeError:
            pass

    def test_handler_is_callable(self):
        tool = self._make_tool()
        assert callable(tool.handler)
