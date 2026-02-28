"""Python-facing helpers for the Static Effect Dependency Analyzer (SEDA).

This module provides lightweight dataclasses that mirror the structures returned by the
`doeff-effect-analyzer` PyO3 extension. It keeps all heavyweight parsing and symbol resolution in
Rust; the Python side is intentionally thin and only normalises analyzer output into Python-native
objects.

The current implementation relies on a placeholder Rust analyzer that returns empty summaries; the
shapes are stable so downstream tooling can begin integration work.
"""


from collections.abc import Iterable
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any

try:  # pragma: no cover - optional dependency during bootstrap
    _rust_analyze = import_module("doeff_effect_analyzer").analyze
except ImportError:  # pragma: no cover
    try:
        _rust_analyze = import_module("doeff_effect_anlyzer").analyze
    except ImportError:
        _rust_analyze = None


@dataclass(slots=True)
class SourceSpan:
    """Location of an effect in the source tree."""

    file: str
    line: int
    column: int


@dataclass(slots=True)
class EffectUsage:
    """Single effect usage with provenance."""

    key: str
    span: SourceSpan | None = None
    via: str | None = None


@dataclass(slots=True)
class EffectSummary:
    """Flattened list of effect dependencies for a given symbol."""

    qualified_name: str
    module: str
    target_kind: str = "unknown"
    defined_at: SourceSpan | None = None
    effects: list[EffectUsage] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class EffectTreeNode:
    """Node within the hierarchical call tree."""

    kind: str
    label: str
    effects: list[str]
    span: SourceSpan | None
    children: list["EffectTreeNode"] = field(default_factory=list)

    def walk(self) -> Iterable["EffectTreeNode"]:
        """Depth-first traversal yielding this node and descendants."""

        yield self
        for child in self.children:
            yield from child.walk()


@dataclass(slots=True)
class EffectReport:
    """Full analyzer output (summary + tree)."""

    summary: EffectSummary
    tree: EffectTreeNode

    def to_dict(self) -> dict[str, Any]:
        """Convert the report into a plain Python dictionary."""

        return {
            "summary": {
                "qualified_name": self.summary.qualified_name,
                "module": self.summary.module,
                "target_kind": self.summary.target_kind,
                "defined_at": (
                    {
                        "file": self.summary.defined_at.file,
                        "line": self.summary.defined_at.line,
                        "column": self.summary.defined_at.column,
                    }
                    if self.summary.defined_at
                    else None
                ),
                "effects": [
                    {
                        "key": usage.key,
                        "span": (
                            {
                                "file": usage.span.file,
                                "line": usage.span.line,
                                "column": usage.span.column,
                            }
                            if usage.span
                            else None
                        ),
                        "via": usage.via,
                    }
                    for usage in self.summary.effects
                ],
                "warnings": list(self.summary.warnings),
            },
            "tree": _tree_to_dict(self.tree),
        }


def analyze(dotted_path: str) -> EffectReport:
    """Invoke the Rust analyzer for a given dotted path (``module.symbol``)."""

    if _rust_analyze is None:  # pragma: no cover - defensive
        raise RuntimeError(
            "doeff_effect_analyzer extension module is not installed. "
            "Run `uv run maturin develop --manifest-path packages/doeff-effect-analyzer/Cargo.toml`."
        )

    py_report = _rust_analyze(dotted_path)
    summary_dict = py_report.summary().to_dict()
    tree_dict = py_report.tree().to_dict()
    return _convert_report(summary_dict, tree_dict)


def _convert_report(summary: dict[str, Any], tree: dict[str, Any]) -> EffectReport:
    return EffectReport(
        summary=_convert_summary(summary),
        tree=_convert_tree(tree),
    )


def _convert_summary(summary: dict[str, Any]) -> EffectSummary:
    effects = [
        EffectUsage(
            key=item["key"],
            span=(
                SourceSpan(
                    file=item["span"]["file"],
                    line=int(item["span"]["line"]),
                    column=int(item["span"]["column"]),
                )
                if item.get("span")
                else None
            ),
            via=item.get("via"),
        )
        for item in summary.get("effects", [])
    ]
    defined_payload = summary.get("defined_at")
    defined_at = (
        SourceSpan(
            file=defined_payload["file"],
            line=int(defined_payload["line"]),
            column=int(defined_payload["column"]),
        )
        if isinstance(defined_payload, dict)
        else None
    )
    return EffectSummary(
        qualified_name=summary.get("qualified_name", ""),
        module=summary.get("module", ""),
        target_kind=summary.get("target_kind", "unknown"),
        defined_at=defined_at,
        effects=effects,
        warnings=list(summary.get("warnings", [])),
    )


def _convert_tree(payload: dict[str, Any]) -> EffectTreeNode:
    span_payload = payload.get("span")
    span = (
        SourceSpan(
            file=span_payload["file"],
            line=int(span_payload["line"]),
            column=int(span_payload["column"]),
        )
        if isinstance(span_payload, dict)
        else None
    )

    children = [_convert_tree(child) for child in payload.get("children", [])]
    return EffectTreeNode(
        kind=payload.get("kind", "unknown"),
        label=payload.get("label", ""),
        effects=list(payload.get("effects", [])),
        span=span,
        children=children,
    )


def _tree_to_dict(node: EffectTreeNode) -> dict[str, Any]:
    return {
        "kind": node.kind,
        "label": node.label,
        "effects": list(node.effects),
        "span": (
            {
                "file": node.span.file,
                "line": node.span.line,
                "column": node.span.column,
            }
            if node.span
            else None
        ),
        "children": [_tree_to_dict(child) for child in node.children],
    }


__all__ = [
    "EffectReport",
    "EffectSummary",
    "EffectTreeNode",
    "EffectUsage",
    "SourceSpan",
    "analyze",
]
