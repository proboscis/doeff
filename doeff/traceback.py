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
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypeAlias, runtime_checkable

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
        HandlerStackEntry,
        TraceDispatch,
        TraceFrame,
        TraceResumePoint,
        coerce_trace_entries,
    )

    if TYPE_CHECKING:
        from types import TracebackType

    @runtime_checkable
    class EffectTraceback(Protocol):
        def format(self) -> str: ...

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

    _STATUS_MARKERS: dict[str, str] = {
        "resumed": "✓",
        "delegated": "↗",
        "threw": "✗",
        "transferred": "⇢",
        "active": "⚡",
        "pending": "·",
    }
    _INTERNAL_DELEGATE_ONLY_HANDLERS = {"sync_await_handler", "async_await_handler"}

    @dataclass(frozen=True)
    class SpawnSite:
        function_name: str
        file: str
        line: int

    @dataclass(frozen=True)
    class DoeffTraceback:
        chain: tuple[DoeffTraceEntry, ...]
        raw_trace: tuple[TraceFrame | TraceDispatch | TraceResumePoint, ...]
        python_traceback: Any | None
        exception: BaseException
        spawned_from: DoeffTraceback | None = None
        task_id: int | None = None
        parent_task: int | None = None
        spawn_site: SpawnSite | None = None

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

        def format(self) -> str:
            return self.format_default()

        def _python_frames(self) -> list[tuple[str, str, int]]:
            tb_obj = self.exception.__traceback__
            if tb_obj is None and self.python_traceback is not None:
                tb_obj = getattr(self.python_traceback, "traceback_obj", None)
            if tb_obj is None:
                return []

            frames = _py_traceback.extract_tb(tb_obj)
            filtered: list[tuple[str, str, int]] = []
            for frame in frames:
                normalized = frame.filename.replace("\\", "/")
                if frame.name in {"generator_wrapper", "_wrap"}:
                    continue
                if normalized.endswith("/doeff/do.py"):
                    continue
                filtered.append((frame.name, frame.filename, frame.lineno))
            return filtered

        def _default_dispatch_rows(self) -> list[tuple[TraceDispatch, TraceResumePoint | None]]:
            dispatches = [entry for entry in self.raw_trace if isinstance(entry, TraceDispatch)]
            if not dispatches:
                return []

            resume_points = [entry for entry in self.raw_trace if isinstance(entry, TraceResumePoint)]
            resume_by_dispatch = {resume.dispatch_id: resume for resume in resume_points}
            selected_ids: set[int] = set()

            for function_name, source_file, source_line in self._python_frames():
                exact = next(
                    (
                        rp
                        for rp in reversed(resume_points)
                        if rp.resumed_function_name == function_name
                        and rp.source_file == source_file
                        and rp.source_line == source_line
                    ),
                    None,
                )
                if exact is not None:
                    selected_ids.add(exact.dispatch_id)
                    continue
                by_function = next(
                    (
                        rp
                        for rp in reversed(resume_points)
                        if rp.resumed_function_name == function_name and rp.source_file == source_file
                    ),
                    None,
                )
                if by_function is not None:
                    selected_ids.add(by_function.dispatch_id)

            thrown_dispatch = next(
                (dispatch for dispatch in reversed(dispatches) if dispatch.action == "threw"),
                None,
            )
            if thrown_dispatch is not None:
                selected_ids.add(thrown_dispatch.dispatch_id)

            if not selected_ids:
                selected_ids.add(dispatches[-1].dispatch_id)

            rows: list[tuple[TraceDispatch, TraceResumePoint | None]] = []
            for dispatch in dispatches:
                if dispatch.dispatch_id in selected_ids:
                    rows.append((dispatch, resume_by_dispatch.get(dispatch.dispatch_id)))
            return rows

        @staticmethod
        def _truncate(text: str, limit: int) -> str:
            return text if len(text) <= limit else f"{text[:limit]}..."

        @staticmethod
        def _normalized_status(action: str) -> str:
            if action == "returned":
                return "resumed"
            return action

        def _render_stack(
            self,
            dispatch: TraceDispatch,
            previous: str | None,
        ) -> tuple[str, str]:
            stack = dispatch.handler_stack
            if not stack:
                fallback_status = self._normalized_status(dispatch.action)
                stack = (HandlerStackEntry(dispatch.handler_name, fallback_status),)

            parts: list[str] = []
            for entry in stack:
                if (
                    entry.name in _INTERNAL_DELEGATE_ONLY_HANDLERS
                    and entry.status == "delegated"
                ):
                    continue
                marker = _STATUS_MARKERS.get(entry.status, "?")
                parts.append(f"{entry.name}{marker}")

            if not parts:
                marker = _STATUS_MARKERS.get(self._normalized_status(dispatch.action), "?")
                parts.append(f"{dispatch.handler_name}{marker}")

            rendered = f"[{' > '.join(parts)}]"
            if previous == rendered:
                return "[same]", rendered
            return rendered, rendered

        def _result_line(
            self,
            dispatch: TraceDispatch,
            resume_point: TraceResumePoint | None,
        ) -> str | None:
            action = dispatch.action
            if action in {"resumed", "returned"}:
                value = dispatch.value_repr if dispatch.value_repr is not None else "None"
                return f"→ resumed with {self._truncate(value, 80)}"
            if action == "threw":
                detail = dispatch.exception_repr
                if detail is None:
                    detail = f"{type(self.exception).__name__}({str(self.exception)!r})"
                return f"✗ {dispatch.handler_name} raised {detail}"
            if action == "transferred":
                target = "<target>"
                if resume_point is not None:
                    target = (
                        f"{resume_point.resumed_function_name}() "
                        f"{resume_point.source_file}:{resume_point.source_line}"
                    )
                return f"⇢ {dispatch.handler_name} transferred to {target}"
            if action == "active":
                return f"⚡ {dispatch.handler_name} is active"
            return None

        def _default_segment_lines(self) -> list[str]:
            lines: list[str] = []
            previous_stack: str | None = None
            fallback_frames = self._python_frames()

            for dispatch, resume_point in self._default_dispatch_rows():
                if resume_point is not None:
                    function_name = resume_point.resumed_function_name
                    source_file = resume_point.source_file
                    source_line = resume_point.source_line
                elif fallback_frames:
                    function_name, source_file, source_line = fallback_frames[-1]
                else:
                    function_name, source_file, source_line = ("<unknown>", "<unknown>", 0)

                lines.append(f"  {function_name}()  {source_file}:{source_line}")
                lines.append(f"    yield {dispatch.effect_repr}")
                stack_line, previous_stack = self._render_stack(dispatch, previous_stack)
                lines.append(f"    {stack_line}")
                result_line = self._result_line(dispatch, resume_point)
                if result_line:
                    lines.append(f"    {result_line}")

            if fallback_frames:
                function_name, source_file, source_line = fallback_frames[-1]
            else:
                function_name, source_file, source_line = ("<unknown>", "<unknown>", 0)
            lines.append(f"  {function_name}()  {source_file}:{source_line}")
            message = str(self.exception)
            if message:
                lines.append(f"    raise {type(self.exception).__name__}({message!r})")
            else:
                lines.append(f"    raise {type(self.exception).__name__}()")
            return lines

        def _task_separator(self) -> str:
            if self.task_id is None:
                return "── in spawned task ──"
            if self.spawn_site is None:
                return f"── in task {self.task_id} ──"
            return (
                f"── in task {self.task_id} "
                f"(spawned at {self.spawn_site.function_name} "
                f"{self.spawn_site.file}:{self.spawn_site.line}) ──"
            )

        def format_default(self) -> str:
            lines: list[str] = ["doeff Traceback (most recent call last):", ""]

            segments: list[DoeffTraceback] = []
            current: DoeffTraceback | None = self
            while current is not None:
                segments.append(current)
                current = current.spawned_from

            for idx, segment in enumerate(segments):
                if idx > 0:
                    lines.append(segment._task_separator())
                lines.extend(segment._default_segment_lines())
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

    def _coerce_spawn_site(raw: Any) -> SpawnSite | None:
        if not isinstance(raw, dict):
            return None
        function_name = raw.get("function_name")
        source_file = raw.get("file")
        line = raw.get("line")
        if not isinstance(function_name, str) or not isinstance(source_file, str):
            return None
        if not isinstance(line, int):
            return None
        return SpawnSite(function_name=function_name, file=source_file, line=line)

    def _parse_trace_payload(
        trace_payload: Any,
    ) -> tuple[list[Any], dict[str, Any], Any | None]:
        if isinstance(trace_payload, (list, tuple)):
            return list(trace_payload), {}, None
        if isinstance(trace_payload, dict):
            trace_entries = trace_payload.get("trace", [])
            if not isinstance(trace_entries, (list, tuple)):
                trace_entries = []
            metadata = {
                "task_id": trace_payload.get("task_id"),
                "parent_task": trace_payload.get("parent_task"),
                "spawn_site": _coerce_spawn_site(trace_payload.get("spawn_site")),
            }
            return list(trace_entries), metadata, trace_payload.get("spawned_from")
        return [], {}, None

    def build_doeff_traceback(
        exception: BaseException,
        trace_entries: Any,
        *,
        allow_active: bool = False,
        _captured_traceback: Any | None = None,
        _seen_payload_ids: set[int] | None = None,
    ) -> DoeffTraceback:
        from doeff.types import capture_traceback, get_captured_traceback

        if _seen_payload_ids is None:
            _seen_payload_ids = set()

        payload_id = id(trace_entries)
        if payload_id in _seen_payload_ids:
            trace_entries = []
        else:
            _seen_payload_ids.add(payload_id)

        raw_entries, metadata, spawned_payload = _parse_trace_payload(trace_entries)
        coerced_entries = coerce_trace_entries(raw_entries)
        segment_allow_active = allow_active or isinstance(metadata.get("task_id"), int)
        projected = project_trace(coerced_entries, allow_active=segment_allow_active)

        captured = _captured_traceback
        if captured is None:
            captured = get_captured_traceback(exception)
            if captured is None:
                captured = capture_traceback(exception)

        spawned_tb = None
        if spawned_payload is not None:
            spawned_tb = build_doeff_traceback(
                exception,
                spawned_payload,
                allow_active=segment_allow_active,
                _captured_traceback=captured,
                _seen_payload_ids=_seen_payload_ids,
            )

        task_id = metadata.get("task_id")
        parent_task = metadata.get("parent_task")
        return DoeffTraceback(
            chain=tuple(projected),
            raw_trace=tuple(coerced_entries),
            python_traceback=captured,
            exception=exception,
            spawned_from=spawned_tb,
            task_id=task_id if isinstance(task_id, int) else None,
            parent_task=parent_task if isinstance(parent_task, int) else None,
            spawn_site=metadata.get("spawn_site"),
        )

    def attach_doeff_traceback(
        exception: BaseException,
        *,
        allow_active: bool = False,
    ) -> DoeffTraceback | None:
        existing = getattr(exception, "__doeff_traceback__", None)
        if isinstance(existing, DoeffTraceback):
            return existing

        raw_trace = getattr(exception, "__doeff_traceback_data__", None)
        if raw_trace is None:
            return None
        if not isinstance(raw_trace, (list, tuple, dict)):
            return None

        tb = build_doeff_traceback(exception, raw_trace, allow_active=allow_active)
        exception.__doeff_traceback__ = tb
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
