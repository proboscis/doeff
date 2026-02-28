"""Effect call tree utilities."""


from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing aid only
    from doeff.types import CallFrame, EffectObservation
else:  # Fallback types that behave like ``Any``
    CallFrame = Any
    EffectObservation = Any


@dataclass
class EffectCallTreeNode:
    """Node within an effect call tree."""

    name: str
    is_effect: bool
    children: OrderedDict[str, "EffectCallTreeNode"] = field(default_factory=OrderedDict)
    observations: list["EffectObservation"] = field(default_factory=list)

    def add_child(self, name: str, *, is_effect: bool) -> "EffectCallTreeNode":
        node = self.children.get(name)
        if node is None:
            node = EffectCallTreeNode(name=name, is_effect=is_effect)
            self.children[name] = node
        return node

    @property
    def count(self) -> int:
        return len(self.observations)


class EffectCallTree:
    """Hierarchical view of effects grouped by program call stack."""

    def __init__(self, root: EffectCallTreeNode) -> None:
        self._root = root

    @classmethod
    def from_observations(
        cls, observations: Iterable["EffectObservation"]
    ) -> "EffectCallTree":
        root = EffectCallTreeNode(name="<root>", is_effect=False)

        for observation in observations:
            current = root

            for frame in observation.call_stack_snapshot:
                label = _format_frame(frame)
                current = current.add_child(label, is_effect=False)

            effect_label = _format_effect(observation)
            effect_node = current.add_child(effect_label, is_effect=True)
            effect_node.observations.append(observation)

        return cls(root)

    def visualize_ascii(self) -> str:
        """Render the call tree using ASCII connectors."""

        if not self._root.children:
            return "(no effects)"

        lines: list[str] = []
        children = list(self._root.children.values())
        for index, child in enumerate(children):
            _render_node(child, prefix="", lines=lines, is_last=index == len(children) - 1)
        return "\n".join(lines)


def _render_node(
    node: EffectCallTreeNode,
    *,
    prefix: str,
    lines: list[str],
    is_last: bool,
) -> None:
    connector = "└─ " if is_last else "├─ "
    lines.append(prefix + connector + _display_name(node))

    next_prefix = prefix + ("   " if is_last else "│  ")
    children = list(node.children.values())
    for index, child in enumerate(children):
        _render_node(child, prefix=next_prefix, lines=lines, is_last=index == len(children) - 1)


def _display_name(node: EffectCallTreeNode) -> str:
    if not node.is_effect:
        return node.name

    count = node.count
    if count <= 1:
        return node.name
    return f"{node.name} x{count}"


def _format_frame(frame: "CallFrame") -> str:
    function_name = getattr(frame, "function_name", "<unknown>")
    args = getattr(frame, "args", ())
    kwargs = getattr(frame, "kwargs", {})

    parts: list[str] = []
    for value in args:
        parts.append(_short_repr(value))
    for key, value in kwargs.items():
        parts.append(f"{key}={_short_repr(value)}")

    arg_str = ", ".join(parts)
    return f"{function_name}({arg_str})"


def _format_effect(observation: "EffectObservation") -> str:
    effect_type = getattr(observation, "effect_type", "Effect")
    key = getattr(observation, "key", None)

    if key is None:
        return effect_type
    return f"{effect_type}({_short_repr(key)})"


def _short_repr(value: Any, *, limit: int = 40) -> str:
    text = repr(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


__all__ = ["EffectCallTree", "EffectCallTreeNode"]
