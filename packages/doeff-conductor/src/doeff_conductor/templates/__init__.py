"""
Workflow templates for doeff-conductor.

Pre-built workflow templates:
- basic_pr: issue -> agent -> PR
- enforced_pr: issue -> agent -> test -> fix loop -> PR
- reviewed_pr: issue -> agent -> review -> PR
- multi_agent: issue -> parallel agents -> merge -> PR
"""

from __future__ import annotations

import inspect
from typing import Any, Callable

from .basic_pr import basic_pr
from .enforced_pr import enforced_pr
from .reviewed_pr import reviewed_pr
from .multi_agent import multi_agent

# Template registry
TEMPLATES: dict[str, tuple[Callable[..., Any], str]] = {
    "basic_pr": (
        basic_pr,
        "Basic PR workflow: issue -> agent -> PR",
    ),
    "enforced_pr": (
        enforced_pr,
        "Enforced PR workflow: issue -> agent -> test -> fix loop -> PR",
    ),
    "reviewed_pr": (
        reviewed_pr,
        "Reviewed PR workflow: issue -> agent -> review -> PR",
    ),
    "multi_agent": (
        multi_agent,
        "Multi-agent PR workflow: issue -> parallel agents -> merge -> PR",
    ),
}


def is_template(name: str) -> bool:
    """Check if name is a registered template."""
    return name in TEMPLATES


def get_template(name: str) -> Callable[..., Any]:
    """Get a template function by name."""
    if name not in TEMPLATES:
        raise KeyError(f"Unknown template: {name}")
    return TEMPLATES[name][0]


def get_available_templates() -> dict[str, str]:
    """Get dictionary of available templates with descriptions."""
    return {name: desc for name, (_, desc) in TEMPLATES.items()}


def get_template_source(name: str) -> str:
    """Get source code for a template."""
    if name not in TEMPLATES:
        raise KeyError(f"Unknown template: {name}")
    func = TEMPLATES[name][0]
    return inspect.getsource(func)


__all__ = [
    "basic_pr",
    "enforced_pr",
    "reviewed_pr",
    "multi_agent",
    "is_template",
    "get_template",
    "get_available_templates",
    "get_template_source",
    "TEMPLATES",
]
