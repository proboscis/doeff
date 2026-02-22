"""Traceback projection and rendering utilities.

This module serves two roles:
- Imported as ``doeff.traceback``: expose doeff traceback projection/rendering APIs.
- Imported accidentally as top-level ``traceback`` (module shadowing): proxy stdlib traceback.

The module also defines the ``EffectTraceback`` protocol used by different interpreter backends.
"""

from __future__ import annotations

import importlib.util
import linecache
import sysconfig
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    Protocol,
    TypeAlias,
    cast,
    runtime_checkable,
)

if __name__ == "traceback":
    # Some entrypoints can shadow stdlib ``traceback`` with ``doeff/traceback.py``.
    # In that case, load and mirror the real stdlib module so runtime imports stay correct.
    _stdlib_path = Path(sysconfig.get_path("stdlib")) / "traceback.py"
    _spec = importlib.util.spec_from_file_location("_doeff_stdlib_traceback", _stdlib_path)
    if _spec is None or _spec.loader is None:
        raise ImportError("failed to load stdlib traceback module")

    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)

    for _name, _value in vars(_module).items():
        if _name in {
            "__name__",
            "__file__",
            "__package__",
            "__spec__",
            "__loader__",
            "__cached__",
            "__builtins__",
        }:
            continue
        globals()[_name] = _value

    __all__ = list(getattr(_module, "__all__", []))

else:
    import traceback as _py_traceback

    from doeff.trace import (
        ActiveChainEntry,
        ContextEntry,
        EffectResultActive,
        EffectResultResumed,
        EffectResultThrew,
        EffectResultTransferred,
        EffectYield,
        ExceptionSite,
        HandlerStackEntry,
        ProgramYield,
        SpawnBoundary,
        TraceDispatch,
        TraceFrame,
        TraceResumePoint,
        coerce_active_chain_entries,
        coerce_trace_entries,
    )

    if TYPE_CHECKING:
        from types import TracebackType

    @runtime_checkable
    class EffectTraceback(Protocol):
        def format(self) -> str: ...

        def format_default(self) -> str: ...

        def format_short(self) -> str: ...

        def to_dict(self) -> dict[str, Any]: ...

    @dataclass(frozen=True)
    class PythonTraceback:
        exception: BaseException
        traceback_obj: TracebackType | None = None
        capture_timestamp: float | None = field(default=None)

        def __post_init__(self) -> None:
            if self.traceback_obj is None and self.exception.__traceback__ is not None:
                object.__setattr__(self, "traceback_obj", self.exception.__traceback__)
            if self.capture_timestamp is None:
                object.__setattr__(self, "capture_timestamp", time.time())

        def format(self) -> str:
            lines: list[str] = ["Python Traceback (most recent call last):"]
            if self.traceback_obj is not None:
                lines.extend(
                    chunk.rstrip("\n")
                    for chunk in _py_traceback.format_tb(self.traceback_obj)
                    if chunk.strip()
                )
            else:
                lines.append("  (no traceback available)")
            lines.append("")
            lines.append(f"{type(self.exception).__name__}: {self.exception}")
            return "\n".join(lines)

        def format_short(self) -> str:
            location = "<unknown>"
            if self.traceback_obj is not None:
                tb = self.traceback_obj
                while tb.tb_next is not None:
                    tb = tb.tb_next
                location = tb.tb_frame.f_code.co_name
            return f"{location}: {type(self.exception).__name__}: {self.exception}"

        def to_dict(self) -> dict[str, Any]:
            frames: list[dict[str, Any]] = []
            tb = self.traceback_obj
            while tb is not None:
                frames.append(
                    {
                        "filename": tb.tb_frame.f_code.co_filename,
                        "lineno": tb.tb_lineno,
                        "function": tb.tb_frame.f_code.co_name,
                        "code": None,
                    }
                )
                tb = tb.tb_next
            return {
                "version": "1.0",
                "type": "python",
                "frames": frames,
                "exception": {
                    "type": type(self.exception).__name__,
                    "qualified_type": f"{type(self.exception).__module__}.{type(self.exception).__name__}",
                    "message": str(self.exception),
                    "args": list(self.exception.args) if self.exception.args else [],
                },
                "metadata": {"capture_timestamp": self.capture_timestamp},
            }

    def capture_python_traceback(ex: BaseException) -> PythonTraceback:
        return PythonTraceback(
            exception=ex,
            traceback_obj=ex.__traceback__,
            capture_timestamp=time.time(),
        )

    @dataclass(frozen=True)
    class ProgramFrame:
        function_name: str
        source_file: str
        source_line: int
        code: str | None
        args_repr: str | None

    @dataclass(frozen=True)
    class HandlerFrame:
        handler_name: str
        handler_kind: Literal["python", "rust_builtin"]
        source_file: str | None
        source_line: int | None
        effect_repr: str
        action: Literal["resumed", "returned", "transferred", "delegated", "threw"] | str
        action_detail: str | None

    @dataclass(frozen=True)
    class ResumeMarker:
        function_name: str
        source_file: str
        source_line: int
        code: str | None

    DoeffTraceEntry: TypeAlias = ProgramFrame | HandlerFrame | ResumeMarker

    def _resolve_code(source_file: str, source_line: int) -> str | None:
        if source_line <= 0:
            return None
        code = linecache.getline(source_file, source_line).strip()
        return code if code else None

    def _action_detail_for_dispatch(dispatch: TraceDispatch) -> str | None:
        detail: str | None = None
        if dispatch.action == "resumed":
            value = dispatch.value_repr if dispatch.value_repr is not None else "None"
            detail = f"Resume(k, value={value})"
        elif dispatch.action == "returned":
            value = dispatch.value_repr if dispatch.value_repr is not None else "None"
            detail = f"-> returned {value}"
        elif dispatch.action == "transferred":
            value = dispatch.value_repr if dispatch.value_repr is not None else "None"
            detail = f"Transfer(other_k, value={value})"
        elif dispatch.action == "threw":
            detail = f"raise {dispatch.exception_repr}" if dispatch.exception_repr else "raise <exception>"
        elif dispatch.action == "active":
            detail = "in progress"
        return detail

    def project_trace(
        trace_entries: list[Any] | tuple[Any, ...],
        *,
        allow_active: bool = False,
    ) -> list[DoeffTraceEntry]:
        projected: list[DoeffTraceEntry] = []
        seen_frame_ids: set[int] = set()

        for entry in coerce_trace_entries(list(trace_entries)):
            if isinstance(entry, TraceFrame):
                code = _resolve_code(entry.source_file, entry.source_line)
                if entry.frame_id not in seen_frame_ids:
                    projected.append(
                        ProgramFrame(
                            function_name=entry.function_name,
                            source_file=entry.source_file,
                            source_line=entry.source_line,
                            code=code,
                            args_repr=entry.args_repr,
                        )
                    )
                    seen_frame_ids.add(entry.frame_id)
                else:
                    projected.append(
                        ResumeMarker(
                            function_name=entry.function_name,
                            source_file=entry.source_file,
                            source_line=entry.source_line,
                            code=code,
                        )
                    )
                continue

            if isinstance(entry, TraceResumePoint):
                projected.append(
                    ResumeMarker(
                        function_name=entry.resumed_function_name,
                        source_file=entry.source_file,
                        source_line=entry.source_line,
                        code=_resolve_code(entry.source_file, entry.source_line),
                    )
                )
                continue

            if not isinstance(entry, TraceDispatch):
                continue

            if entry.action == "active" and not allow_active:
                raise ValueError("project_trace() encountered active dispatch in exception context")

            chain = list(entry.delegation_chain)
            if not chain:
                from doeff.trace import TraceDelegationEntry

                chain = [
                    TraceDelegationEntry(
                        handler_name=entry.handler_name,
                        handler_kind=entry.handler_kind,
                        source_file=entry.handler_source_file,
                        source_line=entry.handler_source_line,
                    )
                ]

            delegated_chain = chain[:-1]
            for delegated in delegated_chain:
                projected.append(
                    HandlerFrame(
                        handler_name=delegated.handler_name,
                        handler_kind=delegated.handler_kind,
                        source_file=delegated.source_file,
                        source_line=delegated.source_line,
                        effect_repr=entry.effect_repr,
                        action="delegated",
                        action_detail="yield Delegate()",
                    )
                )

            final_action = "delegated" if entry.action == "active" else entry.action

            projected.append(
                HandlerFrame(
                    handler_name=entry.handler_name,
                    handler_kind=entry.handler_kind,
                    source_file=entry.handler_source_file,
                    source_line=entry.handler_source_line,
                    effect_repr=entry.effect_repr,
                    action=final_action,
                    action_detail=_action_detail_for_dispatch(entry),
                )
            )

        return projected

    @dataclass(frozen=True)
    class DoeffTraceback:
        chain: tuple[DoeffTraceEntry, ...]
        active_chain: tuple[ActiveChainEntry, ...]
        python_traceback: Any | None
        exception: BaseException

        def format(self) -> str:
            return self.format_default()

        def _format_program_entry(self, entry: ProgramFrame | ResumeMarker) -> tuple[str, str | None]:
            location = f"{entry.source_file}:{entry.source_line}"
            head = f"  [program]  {entry.function_name}()  {location}"
            if isinstance(entry, ResumeMarker):
                head += "  -> resumed"
            return head, entry.code

        def _format_handler_entry(self, entry: HandlerFrame) -> tuple[str, str | None]:
            if entry.source_file is None or entry.source_line is None:
                location = "(built-in)"
            else:
                location = f"{entry.source_file}:{entry.source_line}"
            head = (
                f"  [handler]  {entry.handler_name}  {location}"
                f"  -> handling {entry.effect_repr}"
            )
            return head, entry.action_detail

        @staticmethod
        def _truncate_result_repr(value: str, *, limit: int = 80) -> str:
            if len(value) <= limit:
                return value
            return value[:limit] + "..."

        def _render_handler_stack(
            self,
            stack: tuple[HandlerStackEntry, ...],
        ) -> str:
            marker = {
                "active": "⚡",
                "pending": "·",
                "passed": "↗",
                "delegated": "⇆",
                "resumed": "✓",
                "transferred": "⇢",
                "returned": "✓",
                "threw": "✗",
            }
            parts = [f"{entry.handler_name}{marker.get(entry.status, '?')}" for entry in stack]
            return f"[{' > '.join(parts)}]" if parts else "[]"

        def _render_effect_result(
            self,
            result: EffectResultActive
            | EffectResultResumed
            | EffectResultThrew
            | EffectResultTransferred,
        ) -> str:
            if isinstance(result, EffectResultResumed):
                return f"→ resumed with {self._truncate_result_repr(result.value_repr)}"
            if isinstance(result, EffectResultThrew):
                return f"✗ {result.handler_name} raised {result.exception_repr}"
            if isinstance(result, EffectResultTransferred):
                return f"⇢ {result.handler_name} transferred to {result.target_repr}"
            return "… active"

        @staticmethod
        def _format_spawn_boundary(boundary: SpawnBoundary) -> str:
            if boundary.spawn_site is not None:
                site = (
                    f"{boundary.spawn_site.function_name}() "
                    f"{boundary.spawn_site.source_file}:{boundary.spawn_site.source_line}"
                )
            else:
                site = "<unknown>"
            return f"── in task {boundary.task_id} (spawned at {site}) ──"

        def format_default(self) -> str:
            lines: list[str] = ["doeff Traceback (most recent call last):", ""]
            previous_handler_stack: tuple[HandlerStackEntry, ...] | None = None
            for entry in self.active_chain:
                if isinstance(entry, ProgramYield):
                    previous_handler_stack = None
                    lines.append(
                        f"  {entry.function_name}()  {entry.source_file}:{entry.source_line}"
                    )
                    lines.append(f"    yield {entry.sub_program_repr}")
                    lines.append("")
                    continue

                if isinstance(entry, EffectYield):
                    if (
                        previous_handler_stack is not None
                        and entry.handler_stack == previous_handler_stack
                    ):
                        stack_line = "[same]"
                    else:
                        stack_line = self._render_handler_stack(entry.handler_stack)
                    previous_handler_stack = entry.handler_stack
                    lines.append(
                        f"  {entry.function_name}()  {entry.source_file}:{entry.source_line}"
                    )
                    lines.append(f"    yield {entry.effect_repr}")
                    lines.append(f"    {stack_line}")
                    lines.append(f"    {self._render_effect_result(entry.result)}")
                    lines.append("")
                    continue

                if isinstance(entry, SpawnBoundary):
                    previous_handler_stack = None
                    lines.append(self._format_spawn_boundary(entry))
                    lines.append("")
                    continue

                if isinstance(entry, ContextEntry):
                    previous_handler_stack = None
                    continue

                if isinstance(entry, ExceptionSite):
                    previous_handler_stack = None
                    lines.append(
                        f"  {entry.function_name}()  {entry.source_file}:{entry.source_line}"
                    )
                    lines.append(f"    raise {entry.exception_type}({entry.message!r})")
                    lines.append("")

            lines.append(f"{type(self.exception).__name__}: {self.exception}")
            return "\n".join(lines)

        def format_chained(self) -> str:
            lines: list[str] = ["doeff Traceback (most recent call last):", ""]
            for entry in self.chain:
                if isinstance(entry, (ProgramFrame, ResumeMarker)):
                    head, detail = self._format_program_entry(entry)
                else:
                    head, detail = self._format_handler_entry(entry)
                lines.append(head)
                if detail:
                    lines.append(f"               {detail}")

            if self.python_traceback is not None:
                lines.append("")
                lines.append("  Python Traceback:")
                try:
                    tb_lines = self.python_traceback.lines(condensed=False)
                except Exception:
                    tb_lines = str(self.python_traceback).splitlines()
                lines.extend(f"    {line}" for line in tb_lines)

            lines.append("")
            lines.append(f"{type(self.exception).__name__}: {self.exception}")
            return "\n".join(lines)

        def format_sectioned(self) -> str:
            program_entries = [e for e in self.chain if isinstance(e, (ProgramFrame, ResumeMarker))]
            handler_entries = [e for e in self.chain if isinstance(e, HandlerFrame)]
            lines = ["Program Stack:"]
            if program_entries:
                for entry in program_entries:
                    lines.append(f"  {entry.function_name}()  {entry.source_file}:{entry.source_line}")
            else:
                lines.append("  (empty)")

            lines.append("")
            lines.append("Handler Stack:")
            if handler_entries:
                for entry in handler_entries:
                    location = (
                        "(built-in)"
                        if entry.source_file is None or entry.source_line is None
                        else f"{entry.source_file}:{entry.source_line}"
                    )
                    lines.append(
                        f"  {entry.handler_name}  {location}  ({entry.handler_kind})"
                        f"  -> {entry.action}"
                    )
            else:
                lines.append("  (empty)")

            lines.append("")
            lines.append("Root Cause:")
            lines.append(f"  {type(self.exception).__name__}: {self.exception}")
            return "\n".join(lines)

        def format_short(self) -> str:
            parts: list[str] = []
            for entry in self.chain:
                if isinstance(entry, (ProgramFrame, ResumeMarker)):
                    parts.append(f"{entry.function_name}()")
                elif isinstance(entry, HandlerFrame):
                    parts.append(f"[{entry.handler_name}] {entry.effect_repr}")
            if not parts:
                return f"{type(self.exception).__name__}: {self.exception}"
            return " -> ".join(parts) + f": {type(self.exception).__name__}: {self.exception}"

    def build_doeff_traceback(
        exception: BaseException,
        trace_entries: list[Any] | tuple[Any, ...],
        active_chain_entries: list[Any] | tuple[Any, ...] = (),
        *,
        allow_active: bool = False,
    ) -> DoeffTraceback:
        from doeff.types import capture_traceback, get_captured_traceback

        projected = project_trace(trace_entries, allow_active=allow_active)
        active_chain = coerce_active_chain_entries(list(active_chain_entries))
        captured = get_captured_traceback(exception)
        if captured is None:
            captured = capture_traceback(exception)
        return DoeffTraceback(
            chain=tuple(projected),
            active_chain=tuple(active_chain),
            python_traceback=captured,
            exception=exception,
        )

    def attach_doeff_traceback(
        exception: BaseException,
        *,
        traceback_data: Any | None = None,
        allow_active: bool = False,
    ) -> DoeffTraceback | None:
        if traceback_data is None:
            return None

        trace_entries: list[Any] | tuple[Any, ...]
        active_chain_entries: list[Any] | tuple[Any, ...]
        raw_trace = traceback_data
        if hasattr(raw_trace, "entries"):
            trace_entries = cast(Any, raw_trace).entries
            active_chain_entries = getattr(raw_trace, "active_chain", ())
            if not isinstance(trace_entries, (list, tuple)):
                trace_entries = ()
            if not isinstance(active_chain_entries, (list, tuple)):
                active_chain_entries = ()
        elif isinstance(raw_trace, dict):
            trace_entries = raw_trace.get("trace", ())
            active_chain_entries = raw_trace.get("active_chain", ())
            if not isinstance(trace_entries, (list, tuple)):
                trace_entries = ()
            if not isinstance(active_chain_entries, (list, tuple)):
                active_chain_entries = ()
        elif isinstance(raw_trace, (list, tuple)):
            trace_entries = raw_trace
            active_chain_entries = ()
        else:
            return None

        tb = build_doeff_traceback(
            exception,
            trace_entries,
            active_chain_entries,
            allow_active=allow_active,
        )
        return tb

    __all__ = [
        "DoeffTraceEntry",
        "DoeffTraceback",
        "EffectTraceback",
        "HandlerFrame",
        "ProgramFrame",
        "PythonTraceback",
        "ResumeMarker",
        "attach_doeff_traceback",
        "build_doeff_traceback",
        "capture_python_traceback",
        "project_trace",
    ]
