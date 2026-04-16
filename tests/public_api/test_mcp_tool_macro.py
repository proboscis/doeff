"""Tests for defmcp-tool Hy macro expansion and runtime behavior."""

import sys
import types

import hy
import hy.macros
import pytest

import doeff_hy  # noqa — registers extensions
from doeff import do, run
from doeff.mcp import McpToolDef, McpParamSchema
from doeff_vm import Expand


def _eval_mcp(code: str, **extra_globals):
    """Evaluate Hy code with defmcp-tool macro available."""
    module_name = "test_mcp_tool"
    mod = types.ModuleType(module_name)
    sys.modules[module_name] = mod

    mod.__dict__.update({
        "run": run,
        "do": do,
        **extra_globals,
    })

    hy.macros.require("doeff_hy.macros", mod, assignments=[
        ["defmcp-tool", "defmcp-tool"],
        ["<-", "<-"],
    ])

    tree = hy.read_many(code)
    result = None
    for form in tree:
        result = hy.eval(form, mod.__dict__, module=mod)
    return mod, result


class TestDefmcpToolMacro:
    def test_basic_expansion(self):
        """defmcp-tool produces an McpToolDef instance."""
        mod, _ = _eval_mcp("""
(defmcp-tool my-tool
  "A test tool"
  [{:name "x" :type "string" :description "input value"}]
  x)
""")
        tool = mod.__dict__["my_tool"]
        assert isinstance(tool, McpToolDef)
        assert tool.name == "my-tool"
        assert tool.description == "A test tool"

    def test_params_schema(self):
        """Params are correctly converted to McpParamSchema."""
        mod, _ = _eval_mcp("""
(defmcp-tool order-tool
  "Order tool"
  [{:name "symbol" :type "string" :description "Stock symbol"}
   {:name "side" :type "string" :enum ["buy" "sell"] :description "Side"}
   {:name "qty" :type "integer" :description "Quantity"}]
  (+ symbol side (str qty)))
""")
        tool = mod.__dict__["order_tool"]
        assert len(tool.params) == 3
        assert tool.params[0].name == "symbol"
        assert tool.params[1].enum == ("buy", "sell")
        assert tool.params[2].type == "integer"

    def test_handler_returns_expand(self):
        """Handler callable returns an Expand (DoExpr program node)."""
        mod, _ = _eval_mcp("""
(defmcp-tool echo-tool
  "Echo back the input"
  [{:name "msg" :type "string" :description "Message to echo"}]
  msg)
""")
        tool = mod.__dict__["echo_tool"]
        prog = tool.handler("hello")
        assert isinstance(prog, Expand)

    def test_handler_executes_via_run(self):
        """Handler's program can be executed via doeff run()."""
        mod, _ = _eval_mcp("""
(defmcp-tool concat-tool
  "Concatenate inputs"
  [{:name "a" :type "string" :description "first"}
   {:name "b" :type "string" :description "second"}]
  (+ a b))
""")
        tool = mod.__dict__["concat_tool"]
        prog = tool.handler("hello", "world")
        result = run(prog)
        assert result == "helloworld"

    def test_handler_with_bind(self):
        """Handler body supports <- binding to call inner defk."""
        @do
        def inner_fn(x):
            return x + "-processed"

        mod, _ = _eval_mcp("""
(defmcp-tool process-tool
  "Process input"
  [{:name "data" :type "string" :description "Input data"}]
  (<- result (inner-fn data))
  result)
""", inner_fn=inner_fn)
        tool = mod.__dict__["process_tool"]
        prog = tool.handler("test")
        result = run(prog)
        assert result == "test-processed"

    def test_input_schema_from_macro(self):
        """input_schema() produces valid JSON Schema."""
        mod, _ = _eval_mcp("""
(defmcp-tool schema-tool
  "Schema test"
  [{:name "a" :type "string" :description "param a"}
   {:name "b" :type "integer" :description "param b"}]
  (+ a (str b)))
""")
        tool = mod.__dict__["schema_tool"]
        schema = tool.input_schema()
        assert schema["type"] == "object"
        assert "a" in schema["properties"]
        assert "b" in schema["properties"]
        assert schema["required"] == ["a", "b"]

    def test_param_names_match_handler_signature(self):
        """param_names() matches the handler function's parameter names."""
        import inspect

        mod, _ = _eval_mcp("""
(defmcp-tool sig-tool
  "Signature test"
  [{:name "alpha" :type "string" :description "first"}
   {:name "beta" :type "integer" :description "second"}]
  (+ alpha (str beta)))
""")
        tool = mod.__dict__["sig_tool"]
        assert tool.param_names() == ("alpha", "beta")
        sig = inspect.signature(tool.handler)
        assert list(sig.parameters.keys()) == ["alpha", "beta"]

    def test_missing_description_raises(self):
        """defmcp-tool without description string raises error."""
        with pytest.raises(Exception, match="description string"):
            _eval_mcp("""
(defmcp-tool bad-tool
  42
  [{:name "x" :type "string" :description "x"}]
  x)
""")
