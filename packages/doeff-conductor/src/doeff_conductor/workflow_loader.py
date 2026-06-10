"""Load Hy workflow request artifacts for conductor verbs."""

import ast
import hashlib
import importlib.util
import shutil
import sys
from dataclasses import dataclass
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any, cast

from hy.errors import HyLanguageError

from doeff_conductor.dsl import WorkflowSpec

WORKFLOW_SOURCE_SUFFIX = ".hy"
WORKFLOW_SNAPSHOT_FILENAME = "workflow.hy"
_HY_SURFACE_ERROR = (
    "workflow authoring surface is the .hy Hy macro DSL; "
    "provide a .hy workflow file with a module-level WORKFLOW"
)

_ALLOWLISTED_IMPORT_ROOTS: frozenset[str] = frozenset(
    {
        "__future__",
        "abc",
        "collections",
        "dataclasses",
        "datetime",
        "decimal",
        "doeff",
        "doeff_conductor",
        "doeff_hy",
        "enum",
        "fractions",
        "functools",
        "hy",
        "itertools",
        "json",
        "math",
        "operator",
        "pathlib",
        "pydantic",
        "pydantic_core",
        "re",
        "types",
        "typing",
        "typing_extensions",
        "time",
        "random",
    }
)
_NETWORK_IMPORT_ROOTS: frozenset[str] = frozenset(
    {"requests", "httpx", "socket", "urllib"}
)
_PATHLIB_WRITE_METHODS: frozenset[str] = frozenset(
    {
        "chmod",
        "mkdir",
        "open",
        "rename",
        "replace",
        "rmdir",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
)
_TIME_CALLS: frozenset[str] = frozenset(
    {
        "datetime.datetime.now",
        "datetime.datetime.today",
        "datetime.now",
        "datetime.today",
        "time.time",
        "time.monotonic",
    }
)


@dataclass(frozen=True)
class WorkflowNondeterminismDiagnostic:
    """One loader nondeterminism violation."""

    line: int
    column: int
    construct: str
    replacement: str
    suggestion: str

    def format(self, workflow_path: Path) -> str:
        return (
            f"{workflow_path}:{self.line}:{self.column + 1}: "
            f"workflow glue code must be deterministic, but this module uses "
            f"{self.construct}. Replacement: {self.suggestion} ({self.replacement})."
        )


class WorkflowNondeterminismError(ValueError):
    """Raised when a workflow module uses raw nondeterminism before execution."""

    def __init__(
        self,
        workflow_path: Path,
        diagnostics: tuple[WorkflowNondeterminismDiagnostic, ...],
    ) -> None:
        self.workflow_path = workflow_path
        self.diagnostics = diagnostics
        formatted_diagnostics: str = "\n".join(
            diagnostic.format(workflow_path)
            for diagnostic in diagnostics
        )
        super().__init__(formatted_diagnostics)


def load_workflow_spec(workflow_path_text: str) -> WorkflowSpec:
    """Load a WorkflowSpec from a Hy DSL workflow file."""

    workflow_path: Path = Path(workflow_path_text)
    _ensure_hy_workflow_path(workflow_path)
    _check_workflow_source_determinism(workflow_path)

    spec: ModuleSpec | None = importlib.util.spec_from_file_location(
        _workflow_module_name(workflow_path),
        workflow_path,
    )
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load workflow: {workflow_path_text}")

    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    if "WORKFLOW" not in module.__dict__:
        raise ValueError("workflow .hy file must define module-level WORKFLOW")

    candidate: object = module.__dict__["WORKFLOW"]
    if not isinstance(candidate, WorkflowSpec):
        raise ValueError("WORKFLOW must be doeff_conductor.dsl.WorkflowSpec")
    return candidate


def check_workflow_source_determinism(workflow_path_text: str) -> None:
    """Validate a workflow module's source without executing it."""

    workflow_path: Path = Path(workflow_path_text)
    _ensure_hy_workflow_path(workflow_path)
    _check_workflow_source_determinism(workflow_path)


def snapshot_workflow_source(
    workflow_path_text: str,
    *,
    state_dir: str | Path,
    run_id: str,
) -> Path:
    """Copy a workflow source file into the run state directory."""

    workflow_path: Path = Path(workflow_path_text)
    _ensure_hy_workflow_path(workflow_path)
    _check_workflow_source_determinism(workflow_path)

    snapshot_path: Path = workflow_snapshot_path(state_dir, run_id)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(workflow_path, snapshot_path)
    return snapshot_path


def prepare_workflow_source_for_run(
    workflow_path_text: str,
    *,
    state_dir: str | Path,
    run_id: str,
) -> Path:
    """Return the authoritative source path for a run, creating the snapshot if needed."""

    snapshot_path: Path = workflow_snapshot_path(state_dir, run_id)
    if snapshot_path.exists():
        _check_workflow_source_determinism(snapshot_path)
        return snapshot_path
    return snapshot_workflow_source(
        workflow_path_text,
        state_dir=state_dir,
        run_id=run_id,
    )


def workflow_snapshot_path(state_dir: str | Path, run_id: str) -> Path:
    return Path(state_dir) / "workflows" / run_id / WORKFLOW_SNAPSHOT_FILENAME


def _check_workflow_source_determinism(workflow_path: Path) -> None:
    _ensure_hy_workflow_path(workflow_path)
    module_ast: ast.Module = _compile_hy_source_to_python_ast(workflow_path)

    visitor = _WorkflowNondeterminismVisitor(workflow_path)
    visitor.visit(module_ast)
    if visitor.diagnostics:
        raise WorkflowNondeterminismError(
            workflow_path,
            tuple(visitor.diagnostics),
        )


def _ensure_hy_workflow_path(workflow_path: Path) -> None:
    if not workflow_path.exists():
        raise ValueError(f"workflow file not found: {workflow_path}")
    if workflow_path.suffix != WORKFLOW_SOURCE_SUFFIX:
        raise ValueError(_HY_SURFACE_ERROR)


def _compile_hy_source_to_python_ast(workflow_path: Path) -> ast.Module:
    import hy.compiler
    import hy.reader
    import doeff_hy  # noqa: F401  # activates Hy import hooks for .hy imports

    source: str = workflow_path.read_text(encoding="utf-8")
    try:
        hy_tree = hy.reader.read_many(source, filename=str(workflow_path))
        compiled = hy.compiler.hy_compile(
            hy_tree,
            "__main__",
            filename=str(workflow_path),
            source=source,
        )
        return cast(ast.Module, compiled)
    except (HyLanguageError, SyntaxError) as error:
        raise ValueError(f"cannot parse workflow source: {workflow_path}: {error}") from error


def _workflow_module_name(workflow_path: Path) -> str:
    fingerprint = hashlib.sha256(str(workflow_path.resolve()).encode("utf-8")).hexdigest()[:12]
    safe_stem = "".join(
        character if character.isalnum() else "_"
        for character in workflow_path.stem
    )
    return f"doeff_conductor_workflow_{safe_stem}_{fingerprint}"


class _WorkflowNondeterminismVisitor(ast.NodeVisitor):
    def __init__(self, workflow_path: Path) -> None:
        self.workflow_path = workflow_path
        self.aliases: dict[str, str] = {}
        self.diagnostics: list[WorkflowNondeterminismDiagnostic] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            module_name: str = alias.name
            local_name: str = alias.asname or _root_module(module_name)
            resolved_name: str = module_name if alias.asname else _root_module(module_name)
            self.aliases[local_name] = resolved_name
            replacement: tuple[str, str] | None = _classify_import(module_name)
            if replacement is not None:
                token, suggestion = replacement
                self._add(node, f"import `{module_name}`", token, suggestion)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module_name: str = node.module or "<relative>"
        replacement: tuple[str, str] | None = _classify_import(module_name)
        if replacement is not None:
            token, suggestion = replacement
            self._add(node, f"import from `{module_name}`", token, suggestion)

        for alias in node.names:
            if alias.name == "*":
                continue
            local_name = alias.asname or alias.name
            self.aliases[local_name] = f"{module_name}.{alias.name}"
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        resolved_name: str | None = self._resolve_call_name(node.func)
        if resolved_name == "open":
            self._add(
                node,
                "open(...)",
                "gate!",
                "move filesystem work behind a deterministic `gate!` step",
            )
        elif resolved_name in _TIME_CALLS:
            self._add(
                node,
                f"{resolved_name}(...)",
                "time!",
                "use the explicit `time!` workflow effect",
            )
        elif resolved_name is not None and resolved_name.startswith("random."):
            self._add(
                node,
                f"{resolved_name}(...)",
                "random!",
                "use the explicit `random!` workflow effect",
            )
        elif self._is_pathlib_write(node.func):
            self._add(
                node,
                "pathlib write",
                "gate!",
                "move filesystem work behind a deterministic `gate!` step",
            )
        elif resolved_name is not None and _is_gate_call(resolved_name):
            self._add(
                node,
                f"{resolved_name}(...)",
                "gate!",
                "move subprocess or network work behind a deterministic `gate!` step",
            )
        self.generic_visit(node)

    def _resolve_call_name(self, node: ast.expr) -> str | None:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base_name: str | None = self._resolve_call_name(node.value)
            if base_name is None:
                return node.attr
            return f"{base_name}.{node.attr}"
        if isinstance(node, ast.Call):
            return self._resolve_call_name(node.func)
        return None

    def _is_pathlib_write(self, node: ast.expr) -> bool:
        if not isinstance(node, ast.Attribute):
            return False
        if node.attr not in _PATHLIB_WRITE_METHODS:
            return False
        base_name: str | None = self._resolve_call_name(node.value)
        return base_name is None or base_name.startswith("pathlib.") or base_name == "Path"

    def _add(
        self,
        node: ast.AST,
        construct: str,
        replacement: str,
        suggestion: str,
    ) -> None:
        positioned_node = cast(Any, node)
        line: int = positioned_node.lineno if hasattr(positioned_node, "lineno") else 1
        column: int = positioned_node.col_offset if hasattr(positioned_node, "col_offset") else 0
        self.diagnostics.append(
            WorkflowNondeterminismDiagnostic(
                line=line,
                column=column,
                construct=construct,
                replacement=replacement,
                suggestion=suggestion,
            )
        )


def _classify_import(module_name: str) -> tuple[str, str] | None:
    root_name: str = _root_module(module_name)
    if root_name == "subprocess" or root_name in _NETWORK_IMPORT_ROOTS:
        return (
            "gate!",
            "move subprocess or network work behind a deterministic `gate!` step",
        )
    if root_name in _ALLOWLISTED_IMPORT_ROOTS:
        return None
    return (
        ":params",
        "pass external inputs through a workflow `:params` entry",
    )


def _is_gate_call(resolved_name: str) -> bool:
    root_name: str = _root_module(resolved_name)
    return root_name == "subprocess" or root_name in _NETWORK_IMPORT_ROOTS


def _root_module(module_name: str) -> str:
    return module_name.split(".", 1)[0]
