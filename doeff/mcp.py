"""MCP tool definition types for doeff.

Provides dataclasses for defining MCP (Model Context Protocol) tools
backed by doeff programs (defk). These are the core data structures
that defmcp-tool Hy macro expands into.

Usage from Hy:

    (defmcp-tool submit-order
      "Submit a trading order"
      [{:name "symbol" :type "string" :description "Stock symbol code"}
       {:name "side" :type "string" :enum ["buy" "sell"] :description "Order side"}
       {:name "qty" :type "integer" :description "Quantity to trade"}]
      (<- result (submit-order symbol side qty))
      result)

Usage from Python:

    from doeff.mcp import McpToolDef, McpParamSchema

    tool = McpToolDef(
        name="submit-order",
        description="Submit a trading order",
        params=(
            McpParamSchema(name="symbol", type="string", description="Stock symbol code"),
            McpParamSchema(name="side", type="string", description="Order side", enum=("buy", "sell")),
            McpParamSchema(name="qty", type="integer", description="Quantity to trade"),
        ),
        handler=my_do_function,
    )
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from doeff.program import Expand

# Sentinel for "no default provided"
_MISSING = object()


@dataclass(frozen=True)
class McpParamSchema:
    """JSON Schema for a single MCP tool parameter.

    Maps directly to the ``inputSchema.properties[name]`` object
    in the MCP ``tools/list`` response.
    """

    name: str
    type: str  # "string" | "integer" | "number" | "boolean" | "array" | "object"
    description: str
    required: bool = True
    enum: tuple[str, ...] | None = None
    default: Any = _MISSING

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
