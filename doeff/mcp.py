"""MCP tool definition types for doeff.

Provides dataclasses for defining MCP (Model Context Protocol) tools
backed by doeff programs (defk). These are the core data structures
that defmcp-tool Hy macro expands into.

Usage from Hy:

    (defmcp-tool submit-order-tool
      "Submit a trading order"
      [{:name "symbol" :type "string" :description "Stock symbol code"}
       {:name "side" :type "string" :enum ["buy" "sell"] :description "Order side"}
       {:name "qty" :type "integer" :description "Quantity to trade"}]
      (<- result (submit-order symbol side qty))
      result)

The tool's name (submit-order-tool) differs from the defk its body calls (submit-order):
the macro binds the name to the McpToolDef, so a body calling the tool's own name would
call the tool, and the macro refuses that body when it expands.

Usage from Python:

    from doeff.mcp import McpToolDef, McpParamSchema

    tool = McpToolDef(
        name="submit-order-tool",
        description="Submit a trading order",
        params=(
            McpParamSchema(name="symbol", type="string", description="Stock symbol code"),
            McpParamSchema(name="side", type="string", description="Order side", enum=("buy", "sell")),
            McpParamSchema(name="qty", type="integer", description="Quantity to trade"),
        ),
        handler=my_do_function,
    )
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from doeff.program import Expand

# Sentinel for "no default provided"
_MISSING = object()


@dataclass(frozen=True)
class McpJsonType:
    """JSON Schema の型 1 つの、Python の側の意味(表 ``MCP_JSON_TYPES`` の 1 行)。

    handler の引数の型の注記(defmcp-tool の展開が書く文字)と、tool の server が受けた値の検め
    (``McpParamSchema.decode``)の両方がこの行を読む。写しを 2 つ持つと、server の検めと注記が別の判断に
    なった — 例: number を ``float`` の isinstance で検めると正しい JSON の整数 ``2`` を断り、integer を ``int`` の
    isinstance で検めると ``true`` を通す(設計検証 lt-K8KC9HXRJBQ4C47YNMNTVKTT4Y の盲検 B・card ki-973b4e44d7bc)。
    """

    name: str
    #: handler の引数の注記(文字の注記 — 実行の時は評価しない)
    annotation: str
    #: JSON を解いた値のうち受ける Python の型(bool はここに bool を書いた型だけが受ける)
    accepts: tuple[type, ...]
    #: 受けた整数を float にして渡すか(number — 注記の float と、契約の isinstance を実行の時にも真にする)
    as_float: bool = False

    def admits(self, value: object) -> bool:
        """JSON を解いた値 ``value`` がこの型の値か(bool は int の仲間として数えない)。"""
        if isinstance(value, bool) and bool not in self.accepts:
            return False
        return isinstance(value, self.accepts)

    def to_python(self, value: object) -> object:
        """受けた値を handler へ渡す値にする(number の整数だけ float にする)。"""
        if self.as_float and isinstance(value, int):
            return float(value)
        return value


#: JSON Schema の型 → Python の側の意味の表(唯一の定義元)。defmcp-tool の注記・server の検め・
#: ``McpParamSchema`` の型の名の検めがここから読む。
MCP_JSON_TYPES: tuple[McpJsonType, ...] = (
    McpJsonType("string", "str", (str,)),
    McpJsonType("integer", "int", (int,)),
    McpJsonType("number", "float", (int, float), as_float=True),
    McpJsonType("boolean", "bool", (bool,)),
    McpJsonType("array", "list[object]", (list,)),
    McpJsonType("object", "dict[str, object]", (dict,)),
)


def mcp_json_type(name: str) -> McpJsonType | None:
    """表 ``MCP_JSON_TYPES`` の名 ``name`` の行(知らない名は None)。"""
    return next((json_type for json_type in MCP_JSON_TYPES if json_type.name == name), None)


def mcp_json_type_names() -> tuple[str, ...]:
    """表 ``MCP_JSON_TYPES`` の型の名の並び(誤りの文に出す — 呼ぶたびに表を読む)。"""
    return tuple(json_type.name for json_type in MCP_JSON_TYPES)


def mcp_param_annotation(type_name: str, required: bool) -> str | None:
    """handler の引数の型の注記(文字)— 知らない型の名は None。

    必須でない引数は ``| None``: server は、送られず既定値も無い引数に None を渡す(``McpParamSchema.decode``)。
    """
    json_type = mcp_json_type(type_name)
    if json_type is None:
        return None
    return json_type.annotation if required else f"{json_type.annotation} | None"


@dataclass(frozen=True)
class McpDecodedArgument:
    """引数 1 つを検めた結果 — handler へ渡す値か、断る訳(problem が None でなければ断る)。"""

    value: object
    problem: str | None


@dataclass(frozen=True)
class McpArguments:
    """tool の呼び 1 つの引数を検めた結果 — schema の順の値と、断る訳の並び(空なら handler を呼べる)。"""

    values: tuple[object, ...]
    problems: tuple[str, ...]


@dataclass(frozen=True)
class McpParamSchema:
    """JSON Schema for a single MCP tool parameter.

    Maps directly to the ``inputSchema.properties[name]`` object
    in the MCP ``tools/list`` response. ``type`` must name a row of
    ``MCP_JSON_TYPES``.
    """

    name: str
    type: str  # "string" | "integer" | "number" | "boolean" | "array" | "object"
    description: str
    required: bool = True
    enum: tuple[str, ...] | None = None
    default: Any = _MISSING

    def __post_init__(self) -> None:
        if mcp_json_type(self.type) is None:
            names = ", ".join(mcp_json_type_names())
            raise ValueError(
                f"McpParamSchema {self.name}: type {self.type!r} is not one of {names}"
            )

    def decode(self, arguments: Mapping[str, object]) -> McpDecodedArgument:
        """tool の呼びの引数(JSON を解いた object)から、この引数の値を取り出して検める。

        送られない引数: 既定値が在ればそれ、必須なら断る、どちらでもなければ None。送られた値は表の型と enum で検め、
        number の整数は float にする。
        """
        if self.name not in arguments or arguments[self.name] is None:
            if self.default is not _MISSING:
                return McpDecodedArgument(self.default, None)
            if self.required:
                return McpDecodedArgument(None, f"missing required argument: {self.name}")
            return McpDecodedArgument(None, None)
        value = arguments[self.name]
        json_type = mcp_json_type(self.type)
        if json_type is None or not json_type.admits(value):
            return McpDecodedArgument(
                None, f"argument {self.name}: expected {self.type}, got {type(value).__name__}"
            )
        if self.enum is not None and value not in self.enum:
            return McpDecodedArgument(
                None, f"argument {self.name}: expected one of {list(self.enum)}"
            )
        return McpDecodedArgument(json_type.to_python(value), None)

    def to_json_schema(self) -> dict[str, Any]:
        """Produce the JSON Schema property object for this parameter."""
        schema: dict[str, Any] = {
            "type": self.type,
            "description": self.description,
        }
        if self.enum is not None:
            schema["enum"] = list(self.enum)
        if self.default is not _MISSING:
            schema["default"] = self.default
        return schema


@dataclass(frozen=True)
class McpToolDef:
    """Complete MCP tool definition: metadata + doeff handler.

    ``handler`` is a ``@do``-decorated function (like defk). When called
    with the tool's parameters it returns an ``Expand[T]`` program node
    that is executed within the current handler stack.
    """

    name: str
    description: str
    params: tuple[McpParamSchema, ...]
    handler: Callable[..., "Expand"]

    def input_schema(self) -> dict[str, Any]:
        """Generate the ``inputSchema`` object for the MCP ``tools/list`` response."""
        properties: dict[str, Any] = {}
        required: list[str] = []
        for p in self.params:
            properties[p.name] = p.to_json_schema()
            if p.required:
                required.append(p.name)
        schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if required:
            schema["required"] = required
        return schema

    def param_names(self) -> tuple[str, ...]:
        """Return ordered parameter names (matches handler signature)."""
        return tuple(p.name for p in self.params)

    def decode_arguments(self, arguments: Mapping[str, object]) -> McpArguments:
        """tool の呼びの引数を schema の順に検める — 断る訳が 1 つでもあれば handler を呼ばない(server が断る)。

        handler の引数の注記(defmcp-tool の展開)が約束する型を、server の側でも真にする所。検めずに渡していた頃は、
        ``int`` と注記した引数に ``"100"`` が届いて ``(* qty 2)`` が ``'100100'`` を成功として返し、省いた必須の引数に
        None が届いていた(設計検証 lt-K8KC9HXRJBQ4C47YNMNTVKTT4Y の盲検 A)。
        """
        decoded = tuple(p.decode(arguments) for p in self.params)
        return McpArguments(
            values=tuple(d.value for d in decoded),
            problems=tuple(d.problem for d in decoded if d.problem is not None),
        )
