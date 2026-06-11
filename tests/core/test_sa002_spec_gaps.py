"""Failing tests for SA-002 spec-gap audit (Phase 4).

Each test maps to one SA-002 gap and is expected to fail on current code.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUST_SRC = ROOT / "packages" / "doeff-vm" / "src"
RUST_CORE_SRC = ROOT / "packages" / "doeff-vm-core" / "src"
CORE_EFFECTS_SRC = ROOT / "packages" / "doeff-core-effects" / "src"


def _read(path: Path) -> str:
    if not path.exists() and path.parent == RUST_SRC:
        fallback = {
            path.name: RUST_CORE_SRC / path.name,
            "effect.rs": CORE_EFFECTS_SRC / "effects" / "mod.rs",
            "handler.rs": CORE_EFFECTS_SRC / "handlers" / "mod.rs",
            "scheduler.rs": CORE_EFFECTS_SRC / "scheduler" / "mod.rs",
        }.get(path.name)
        if fallback is not None and fallback.exists():
            path = fallback
    return path.read_text(encoding="utf-8")


def _extract_fn_body(source: str, fn_name: str) -> str:
    m = re.search(rf"fn\s+{re.escape(fn_name)}\s*\(", source)
    assert m, f"function not found: {fn_name}"
    start = m.start()
    brace = source.find("{", start)
    assert brace != -1, f"function body start not found: {fn_name}"
    depth = 0
    for i in range(brace, len(source)):
        ch = source[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return source[brace : i + 1]
    raise AssertionError(f"function body end not found: {fn_name}")




def test_SA_002_G03_no_implicit_kpc_install_in_vm_new() -> None:  # noqa: N802 - public or spec test name is intentionally stable
    src = _read(RUST_SRC / "pyvm.rs")
    body = _extract_fn_body(src, "new")
    assert "KpcHandlerFactory" not in body
    assert "install_handler" not in body
