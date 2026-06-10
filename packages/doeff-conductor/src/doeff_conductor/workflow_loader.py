"""Load committed Python workflow fixtures for conductor verbs."""

from __future__ import annotations

import importlib.util
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

from doeff_conductor.dsl import WorkflowSpec


def load_workflow_spec(workflow_path_text: str) -> WorkflowSpec:
    """Load a WorkflowSpec from a Python file.

    The module may expose ``workflow``/``WORKFLOW`` directly or a zero-argument
    ``build_workflow`` function.
    """

    workflow_path: Path = Path(workflow_path_text)
    if not workflow_path.exists():
        raise ValueError(f"workflow file not found: {workflow_path_text}")
    if workflow_path.suffix != ".py":
        raise ValueError("C6 workflow verbs currently load Python workflow fixtures")

    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        f"doeff_conductor_workflow_{workflow_path.stem}",
        workflow_path,
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load workflow: {workflow_path_text}")

    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    candidate: Any
    if "workflow" in module.__dict__:
        candidate = module.__dict__["workflow"]
    elif "WORKFLOW" in module.__dict__:
        candidate = module.__dict__["WORKFLOW"]
    elif "build_workflow" in module.__dict__:
        builder: object = module.__dict__["build_workflow"]
        if not callable(builder):
            raise ValueError("build_workflow must be callable")
        candidate = builder()
    else:
        raise ValueError("workflow file must define workflow, WORKFLOW, or build_workflow")

    if not isinstance(candidate, WorkflowSpec):
        raise ValueError("loaded workflow object must be doeff_conductor.dsl.WorkflowSpec")
    return candidate
