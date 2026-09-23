"""Run Semgrep's Python rules over Hy code by scanning its macro-expanded Python.

``scan_with_hy_expansion(config, root, targets)``:

1. collects ``.hy`` and ``.py`` files under ``targets`` (paths relative to ``root``);
2. expands every ``.hy`` file with Hy's own compiler (macros included, see
   :mod:`doeff_adr.hy_expand`) and writes the Python text to a scan tree at the
   same relative path with the suffix ``.py``; ``.py`` files are copied as is;
3. runs Semgrep with ``--project-root`` = the scan tree, so ``paths.include`` /
   ``paths.exclude`` in the config are matched against the same relative paths;
4. reports each finding at the original file and — for Hy — the Hy line.

Rules meant for expanded Hy are ordinary ``languages: [python]`` rules.  Path
filters should not depend on the ``.hy`` suffix (the scanned file is ``.py``);
filter on directories or on the file stem instead.

A file that fails to expand is an error, not a silent pass.
"""

import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from doeff_adr.hy_expand import ExpandedHy, HyExpansionError, expand_hy_file, module_name_for


@dataclass(frozen=True)
class SemgrepFinding:
    """One Semgrep result, located in the file the user wrote."""

    path: str
    line: int
    rule_id: str
    message: str
    scanned_line: int

    @property
    def short_rule_id(self) -> str:
        return self.rule_id.rsplit(".", 1)[-1]


class HyScanExpansionError(Exception):
    """One or more Hy files could not be expanded; the scan did not run."""

    def __init__(self, failures: tuple[HyExpansionError, ...]) -> None:
        super().__init__(
            "Hy files could not be expanded for Semgrep:\n"
            + "\n".join(f"  {failure}" for failure in failures)
        )
        self.failures = failures


_SKIPPED_DIRS = {"__pycache__", ".git", ".venv", "node_modules"}


def _collect_sources(root: Path, targets: Sequence[str | Path]) -> list[Path]:
    """Relative paths of ``.hy`` / ``.py`` files under ``targets``, in a stable order."""
    found: set[Path] = set()
    for target in targets:
        absolute = (root / target).resolve()
        if absolute.is_file():
            candidates = [absolute]
        elif absolute.is_dir():
            candidates = [
                path
                for path in absolute.rglob("*")
                if path.is_file() and not _SKIPPED_DIRS.intersection(path.parts)
            ]
        else:
            raise FileNotFoundError(f"semgrep target does not exist: {absolute}")
        found.update(
            path.relative_to(root.resolve()) for path in candidates if path.suffix in {".hy", ".py"}
        )
    return sorted(found)


def _scan_relative_path(relative: Path) -> Path:
    return relative.with_suffix(".py")


def scan_with_hy_expansion(
    config: Path,
    root: Path,
    targets: Sequence[str | Path],
    *,
    python_path: Sequence[str] = (),
    semgrep: str | None = None,
) -> list[SemgrepFinding]:
    """Semgrep ``config`` over ``targets`` under ``root``, reading Hy through expansion.

    ``python_path`` entries are importable while expanding (project macros).
    The project root itself is always importable.
    """
    from doeff_adr.registry import _run_semgrep

    executable = semgrep or shutil.which("semgrep")
    if executable is None:
        raise AssertionError("semgrep executable is required for Hy-expanded Semgrep scans")
    root = root.resolve()
    sources = _collect_sources(root, targets)
    importable = (str(root), *python_path)
    expanded: dict[Path, ExpandedHy] = {}
    failures: list[HyExpansionError] = []
    for relative in sources:
        if relative.suffix != ".hy":
            continue
        try:
            expanded[relative] = expand_hy_file(
                root / relative,
                module_name=module_name_for(root / relative, root),
                python_path=importable,
            )
        except HyExpansionError as error:
            failures.append(error)
    if failures:
        raise HyScanExpansionError(tuple(failures))

    origin: dict[Path, Path] = {}
    with tempfile.TemporaryDirectory(prefix="doeff-adr-hy-semgrep-") as tmp:
        scan_root = Path(tmp)
        for relative in sources:
            scanned = _scan_relative_path(relative)
            if scanned in origin:
                raise AssertionError(
                    f"{relative} and {origin[scanned]} both scan as {scanned}; rename one"
                )
            origin[scanned] = relative
            destination = scan_root / scanned
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative in expanded:
                destination.write_text(expanded[relative].python_source, encoding="utf-8")
            else:
                shutil.copyfile(root / relative, destination)
        if not origin:
            return []
        results = _run_semgrep(
            executable,
            config.resolve(),
            sorted(origin),
            cwd=scan_root,
            project_root=scan_root,
        )

    findings: list[SemgrepFinding] = []
    for result in results:
        scanned = Path(result["path"])
        relative = origin.get(scanned, scanned)
        scanned_line = int(result["start"]["line"])
        expansion = expanded.get(relative)
        findings.append(
            SemgrepFinding(
                path=relative.as_posix(),
                line=expansion.hy_line(scanned_line) if expansion else scanned_line,
                rule_id=str(result["check_id"]),
                message=str(result.get("extra", {}).get("message", "")),
                scanned_line=scanned_line,
            )
        )
    return sorted(findings, key=lambda f: (f.path, f.line, f.rule_id))
