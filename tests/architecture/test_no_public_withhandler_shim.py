"""Guard against reintroducing the public WithHandler compatibility shim."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BANNED_IMPORTS = (
    re.compile(r"^\s*from\s+doeff\s+import\s+.*\bWithHandler\b", re.MULTILINE),
    re.compile(r"^\s*from\s+doeff\.program\s+import\s+.*\bWithHandler\b", re.MULTILINE),
    re.compile(r"\(import\s+doeff\s+\[[^\]]*\bWithHandler\b", re.MULTILINE),
    re.compile(r"\(import\s+doeff\.program\s+\[[^\]]*\bWithHandler\b", re.MULTILINE),
)
BANNED_CALLS = (
    re.compile(r"\bdoeff\.WithHandler\s*\("),
    re.compile(r"\bprogram\.WithHandler\s*\("),
)


def _source_files() -> list[Path]:
    roots = ("doeff", "packages", "tests", "examples")
    return [
        path
        for root in roots
        for path in (REPO_ROOT / root).rglob("*")
        if path.suffix in {".py", ".pyi", ".hy"}
        and path != Path(__file__).resolve()
        and ".venv" not in path.parts
        and "semgrep/fixtures" not in path.as_posix()
    ]


def test_public_modules_do_not_export_withhandler() -> None:
    import doeff
    import doeff.program

    assert not hasattr(doeff, "WithHandler")
    assert not hasattr(doeff.program, "WithHandler")


def test_public_withhandler_shim_imports_are_gone() -> None:
    offenders: list[str] = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        for pattern in (*BANNED_IMPORTS, *BANNED_CALLS):
            if pattern.search(text):
                offenders.append(str(path.relative_to(REPO_ROOT)))
                break

    assert offenders == []
