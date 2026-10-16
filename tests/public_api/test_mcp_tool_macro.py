"""Tests for defmcp-tool Hy macro expansion and runtime behavior."""

import sys
import types

import doeff_hy  # noqa — registers extensions
import hy
import hy.macros
import pytest
from doeff_vm import Expand

from doeff import McpToolDef, do, run


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

    def test_handler_params_are_annotated_with_the_schema_types(self):
        """handler の引数は schema の :type を写した型の注記(文字)を持ち、必須でない引数は `| None`
        (card ki-973b4e44d7bc)。"""
        import inspect

        mod, _ = _eval_mcp("""
(defmcp-tool typed-tool
  "Typed"
  [{:name "s" :type "string" :description "s"}
   {:name "i" :type "integer" :description "i" :required True}
   {:name "n" :type "number" :description "n"}
   {:name "b" :type "boolean" :description "b"}
   {:name "xs" :type "array" :description "xs"}
   {:name "o" :type "object" :description "o"}
   {:name "maybe-s" :type "string" :description "maybe" :required False}]
  s)
""")
        sig = inspect.signature(mod.__dict__["typed_tool"].handler)
        assert {name: p.annotation for name, p in sig.parameters.items()} == {
            "s": "str",
            "i": "int",
            "n": "float",
            "b": "bool",
            "xs": "list[object]",
            "o": "dict[str, object]",
            "maybe_s": "str | None",
        }

    def test_unknown_param_type_raises(self):
        """schema の :type が JSON Schema の型でなければ、展開の時に誤りにする(handler の引数の型に写せない)。"""
        with pytest.raises(Exception, match="知らない型"):
            _eval_mcp("""
(defmcp-tool bad-type-tool
  "Bad type"
  [{:name "x" :type "str" :description "x"}]
  x)
""")

    def test_default_with_required_or_enum_builds_the_schema(self):
        """:default を :required・:enum と一緒に書いても定義でき、schema に全部の欄が載る。既定値が None の引数は必須でない
        注記(`| None`)になる(設計検証 lt-K8KC9HXRJBQ4C47YNMNTVKTT4Y の盲検 B)。"""
        import inspect

        mod, _ = _eval_mcp("""
(defmcp-tool default-tool
  "Defaults"
  [{:name "limit" :type "integer" :description "l" :required False :default 10}
   {:name "side" :type "string" :description "s" :enum ["buy" "sell"] :default "buy"}
   {:name "note" :type "string" :description "n" :default None}]
  limit)
""")
        tool = mod.__dict__["default_tool"]
        limit, side, note = tool.params
        assert (limit.required, limit.default) == (False, 10)
        assert (side.enum, side.default) == (("buy", "sell"), "buy")
        assert note.default is None
        annotations = {
            name: p.annotation for name, p in inspect.signature(tool.handler).parameters.items()
        }
        assert annotations == {"limit": "int | None", "side": "str", "note": "str | None"}

    @pytest.mark.parametrize(
        "body",
        [
            "(<- result (submit-order symbol))\n  result",
            "(when symbol (! (submit_order symbol)))",
            "(+ \"order: \" (str (! (submit-order symbol))))",
        ],
    )
    def test_body_calling_the_tools_own_name_raises(self, body):
        """本体が tool 自身の名を呼ぶ展開は断る(綴りが - でも _ でも、式の奥でも)。展開はその名を McpToolDef に束ねるので、
        同じ名の defk が先に在っても本体の呼びは tool を指し、run で TypeError: 'McpToolDef' object is not callable に
        なっていた(docs 12 章 MCP の道具の例・card ki-896fc4bca8cb)。"""

        @do
        def submit_order(symbol):
            return {"order": symbol}

        with pytest.raises(SyntaxError, match="tool 自身の名 submit-order を呼んでいます"):
            _eval_mcp(f"""
(defmcp-tool submit-order
  "Submit a trading order"
  [{{:name "symbol" :type "string" :description "Stock symbol"}}]
  {body})
""", submit_order=submit_order)

    def test_tool_named_apart_from_its_defk_runs_the_defk(self):
        """tool の名を defk と分けると、本体は defk を呼び、handler の run は defk の結果を返す(docs 12 章の例の形)。
        tool の値を読むだけの `(. submit-order-tool name)` や引用の中の名は呼びでないので断らない。"""

        @do
        def submit_order(symbol, qty):
            return {"order": [symbol, qty], "status": "accepted"}

        mod, _ = _eval_mcp("""
(defmcp-tool submit-order-tool
  "Submit a trading order"
  [{:name "symbol" :type "string" :description "Stock symbol"}
   {:name "qty" :type "integer" :description "Quantity"}]
  (setv written '(submit-order-tool symbol))
  (<- result (submit-order symbol qty))
  (setv (get result "tool") (. submit-order-tool name))
  result)
""", submit_order=submit_order, hy=hy)
        tool = mod.__dict__["submit_order_tool"]
        assert tool.name == "submit-order-tool"
        assert run(tool.handler("7203", 100)) == {
            "order": ["7203", 100],
            "status": "accepted",
            "tool": "submit-order-tool",
        }

    def test_missing_description_raises(self):
        """defmcp-tool without description string raises error."""
        with pytest.raises(Exception, match="description string"):
            _eval_mcp("""
(defmcp-tool bad-tool
  42
  [{:name "x" :type "string" :description "x"}]
  x)
""")
