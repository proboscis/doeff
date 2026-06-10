"""Agent handler for doeff-conductor."""

import json
import secrets
import shutil
import subprocess
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from doeff_conductor.effects.agent import AgentEffect
    from doeff_conductor.types import Workspace


WorkspaceResolver = Callable[["Workspace"], Path]


class AgentBackendName(str, Enum):
    """Built-in conductor agent backend names."""

    AGENTD = "agentd"
    CODEX_EXEC = "codex-exec"


class AgentBackend(Protocol):
    """Strategy object that executes conductor agent effects."""

    def handle_agent(
        self,
        effect: "AgentEffect",
        workspace_resolver: WorkspaceResolver,
    ) -> object:
        """Handle one agent effect."""


class AgentdAgentBackend:
    """Agent backend backed by doeff-agentd."""

    def handle_agent(
        self,
        effect: "AgentEffect",
        workspace_resolver: WorkspaceResolver,
    ) -> object:
        """Handle schema-validated Agent effect via doeff-agentd."""
        from doeff_agents import (
            AgentEffect as AgentsAgentEffect,
        )
        from doeff_agents import (
            AgentTask as AgentsAgentTask,
        )
        from doeff_agents import (
            AgentType,
            DaemonAgentHandler,
            LazyAgentdClient,
        )

        try:
            agent_type = AgentType(effect.task.agent_type)
        except ValueError as exc:
            raise ValueError(f"unsupported agent_type: {effect.task.agent_type}") from exc

        handler = DaemonAgentHandler(client=LazyAgentdClient())
        return handler.handle_agent(
            AgentsAgentEffect(
                task=AgentsAgentTask(
                    run_id=effect.task.run_id,
                    node_id=effect.task.node_id,
                    attempt=effect.task.attempt,
                    agent_type=agent_type,
                    work_dir=workspace_resolver(effect.task.env),
                    prompt=effect.task.prompt,
                    result_schema=effect.task.result_schema,
                    model=effect.task.model,
                    effort=effect.task.effort,
                    max_retries=effect.task.max_retries,
                    timeout_seconds=effect.task.timeout_seconds,
                )
            )
        )


class CodexExecAgentBackend:
    """Agent backend backed by native structured ``codex exec`` output."""

    def __init__(self, *, codex_home: str | Path | None = None) -> None:
        self.codex_home = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"

    def handle_agent(
        self,
        effect: "AgentEffect",
        workspace_resolver: WorkspaceResolver,
    ) -> object:
        """Handle a Codex task through native structured ``codex exec`` output."""
        from doeff_agents.adapters.codex import trust_workspace_in_codex_home
        from doeff_agents.result_validation import validate_result_payload

        if effect.task.agent_type != "codex":
            raise ValueError("codex-exec backend only supports codex tasks")
        codex_bin: str | None = shutil.which("codex")
        if codex_bin is None:
            raise ValueError("codex CLI is not installed")

        work_dir: Path = workspace_resolver(effect.task.env)
        trust_workspace_in_codex_home(str(self.codex_home), work_dir)

        conductor_dir: Path = work_dir / ".conductor"
        schema_dir: Path = conductor_dir / "schemas"
        result_dir: Path = conductor_dir / "results"
        schema_dir.mkdir(parents=True, exist_ok=True)
        result_dir.mkdir(parents=True, exist_ok=True)
        safe_session_id: str = effect.task.session_id.replace("/", "-")
        schema_path: Path = schema_dir / f"{safe_session_id}.schema.json"
        output_path: Path = result_dir / f"{safe_session_id}.json"
        codex_schema: dict[str, object] = _codex_strict_schema(effect.task.result_schema)
        schema_path.write_text(json.dumps(codex_schema), encoding="utf-8")

        args: list[str] = [
            codex_bin,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(work_dir),
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(output_path),
        ]
        if effect.task.effort is not None:
            args.extend(["-c", f"model_reasoning_effort={json.dumps(effect.task.effort)}"])
        if effect.task.model is not None:
            args.extend(["--model", effect.task.model])
        args.append(effect.task.prompt)

        completed: subprocess.CompletedProcess[str] = subprocess.run(
            args,
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
            timeout=effect.task.timeout_seconds or 600,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                "codex exec failed for "
                f"{effect.task.node_id}: stdout={completed.stdout} stderr={completed.stderr}"
            )
        if not output_path.exists():
            raise RuntimeError(f"codex exec did not write result file: {output_path}")

        payload: object = json.loads(output_path.read_text(encoding="utf-8"))
        validation_error = validate_result_payload(payload, effect.task.result_schema)
        if validation_error is not None:
            raise RuntimeError(f"codex exec returned invalid result: {validation_error}")
        return payload


def make_agent_backend(
    agent_backend: AgentBackendName | str | AgentBackend | None = None,
    *,
    codex_home: str | Path | None = None,
) -> AgentBackend:
    """Build the requested agent backend strategy."""
    if agent_backend is None:
        return AgentdAgentBackend()
    if isinstance(agent_backend, AgentBackendName):
        backend_name = agent_backend
    elif isinstance(agent_backend, str):
        try:
            backend_name = AgentBackendName(agent_backend)
        except ValueError as exc:
            valid_names = ", ".join(item.value for item in AgentBackendName)
            raise ValueError(f"unsupported agent backend: {agent_backend}; expected {valid_names}") from exc
    else:
        return agent_backend

    factories: dict[AgentBackendName, Callable[[], AgentBackend]] = {
        AgentBackendName.AGENTD: AgentdAgentBackend,
        AgentBackendName.CODEX_EXEC: lambda: CodexExecAgentBackend(codex_home=codex_home),
    }
    return factories[backend_name]()


class AgentHandler:
    """Handler for schema-validated conductor agent effects."""

    def __init__(
        self,
        workflow_id: str | None = None,
        *,
        workspace_resolver: WorkspaceResolver | None = None,
        backend: AgentBackend | None = None,
    ) -> None:
        self.workflow_id = workflow_id or secrets.token_hex(4)
        self._workspace_resolver = workspace_resolver
        self._backend = backend or AgentdAgentBackend()

    def _resolve_workspace_path(self, workspace: "Workspace") -> Path:
        if self._workspace_resolver is None:
            raise ValueError("Agent workspace requires a workspace resolver")
        return self._workspace_resolver(workspace)

    def handle_agent(self, effect: "AgentEffect") -> object:
        """Handle schema-validated Agent effect via the injected backend."""
        return self._backend.handle_agent(effect, self._resolve_workspace_path)


def _codex_strict_schema(schema: dict[str, object]) -> dict[str, object]:
    """Return a Codex/OpenAI structured-output-compatible schema copy."""
    strict_schema = _codex_strict_schema_value(schema)
    if not isinstance(strict_schema, dict):
        raise TypeError("Codex schema root must be an object")
    return strict_schema


def _codex_strict_schema_value(value: object) -> object:
    if isinstance(value, dict):
        strict_value: dict[str, object] = {
            str(key): _codex_strict_schema_value(item)
            for key, item in value.items()
        }
        properties = strict_value.get("properties")
        if isinstance(properties, dict) and strict_value.get("type") == "object":
            strict_value["required"] = list(properties)
            strict_value.setdefault("additionalProperties", False)
        if "items" in strict_value:
            strict_value["items"] = _codex_strict_schema_value(strict_value["items"])
        return strict_value
    if isinstance(value, list):
        return [_codex_strict_schema_value(item) for item in value]
    return value


__all__ = [
    "AgentBackend",
    "AgentBackendName",
    "AgentHandler",
    "AgentdAgentBackend",
    "CodexExecAgentBackend",
    "make_agent_backend",
]
