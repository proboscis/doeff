"""Expand Hy source into Python source that Python tools (Semgrep) can read.

Hy has no Semgrep grammar.  Reading Hy text with regular expressions misses
anything a macro produces and anything reached through an alias
(``(import foo [open :as o]) (o p)``).  The sound reading is the one Hy itself
uses: read the forms, expand every macro (including the project's own macros,
e.g. a ``defservice`` that wraps ``defk``), compile to a Python AST, and hand
that AST's source to the Python tool.

Line numbers: ``ast.unparse`` renumbers lines.  The unparsed text is parsed
again and paired with the Hy-compiled tree field by field (statement structure
survives the round trip; a sub-expression that re-splits, such as an f-string,
is skipped on its own), so each output node finds the node it came from.
Nodes produced by a macro carry the macro call's line and nodes that came from
the user's own forms keep their own line, so the most specific (largest) Hy line
seen on an output line is the one reported.
"""

import ast
import sys
import types
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path


class HyExpansionError(Exception):
    """A Hy file could not be read, macro-expanded, or compiled."""

    def __init__(self, filename: str, cause: BaseException) -> None:
        super().__init__(f"{filename}: could not expand Hy: {type(cause).__name__}: {cause}")
        self.filename = filename
        self.cause = cause


@dataclass(frozen=True)
class ExpandedHy:
    """Python source compiled from one Hy file, with a map back to Hy lines."""

    filename: str
    python_source: str
    line_map: Mapping[int, int] = field(default_factory=dict)

    def hy_line(self, python_line: int) -> int:
        """The Hy line a Python line came from (nearest mapped line at or above)."""
        for line in range(python_line, 0, -1):
            mapped = self.line_map.get(line)
            if mapped is not None:
                return mapped
        return 1


def module_name_for(path: Path, root: Path) -> str:
    """Dotted module name of ``path`` relative to ``root`` (``a/b/c.hy`` → ``a.b.c``)."""
    relative = path.resolve().relative_to(root.resolve()).with_suffix("")
    parts = [part for part in relative.parts if part != "__init__"]
    return ".".join(parts) if parts else relative.stem


@contextmanager
def _prepended_sys_path(entries: Sequence[str]) -> Iterator[None]:
    added = [entry for entry in entries if entry not in sys.path]
    sys.path[:0] = added
    try:
        yield
    finally:
        for entry in added:
            if entry in sys.path:
                sys.path.remove(entry)


def expand_hy_source(
    source: str,
    *,
    filename: str,
    module_name: str,
    python_path: Sequence[str] = (),
) -> ExpandedHy:
    """Macro-expand and compile Hy ``source`` into Python source.

    ``python_path`` entries are importable while expanding, so ``(require …)`` of
    project macros resolves.  Nothing but ``require`` (and code a macro runs at
    expansion time) is executed.
    """
    import hy
    import hy.compiler

    module = types.ModuleType(module_name)
    module.__file__ = filename
    try:
        with _prepended_sys_path(python_path):
            forms = hy.read_many(source, filename=filename)
            compiled = hy.compiler.hy_compile(forms, module, filename=filename, source=source)
    except Exception as exc:
        raise HyExpansionError(filename, exc) from exc
    if not isinstance(compiled, ast.Module):
        raise HyExpansionError(filename, TypeError(f"expected a module, got {type(compiled)!r}"))
    python_source = ast.unparse(compiled) + "\n"
    return ExpandedHy(
        filename=filename,
        python_source=python_source,
        line_map=_line_map(compiled, ast.parse(python_source)),
    )


def expand_hy_file(
    path: Path,
    *,
    module_name: str | None = None,
    python_path: Sequence[str] = (),
) -> ExpandedHy:
    source = path.read_text(encoding="utf-8")
    return expand_hy_source(
        source,
        filename=str(path),
        module_name=module_name or path.stem,
        python_path=python_path,
    )


def _line_map(compiled: ast.AST, reparsed: ast.AST) -> dict[int, int]:
    best: dict[int, int] = {}
    for out_line, hy_line in _aligned_lines(compiled, reparsed):
        best[out_line] = max(best.get(out_line, hy_line), hy_line)
    return best


def _aligned_lines(original: ast.AST, output: ast.AST) -> Iterator[tuple[int, int]]:
    """(output line, Hy line) for every node pair, pairing the trees field by field.

    ``unparse`` → ``parse`` keeps statement structure but may re-split some
    expressions (f-strings); a field whose children differ in number or type is
    skipped on its own instead of abandoning the whole file.
    """
    if type(original) is not type(output):
        return
    out_line = getattr(output, "lineno", None)
    hy_line = getattr(original, "lineno", None)
    if isinstance(out_line, int) and isinstance(hy_line, int):
        yield out_line, hy_line
    for name in original._fields:
        left = getattr(original, name, None)
        right = getattr(output, name, None)
        if isinstance(left, ast.AST) and isinstance(right, ast.AST):
            yield from _aligned_lines(left, right)
        elif isinstance(left, list) and isinstance(right, list) and len(left) == len(right):
            for a, b in zip(left, right, strict=True):
                if isinstance(a, ast.AST) and isinstance(b, ast.AST):
                    yield from _aligned_lines(a, b)
