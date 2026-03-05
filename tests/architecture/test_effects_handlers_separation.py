from __future__ import annotations

import ast
from pathlib import Path

EFFECTS_DIR = Path(__file__).resolve().parents[2] / "doeff" / "effects"


def _handler_definitions_in_effects() -> list[tuple[str, str, int]]:
    violations: list[tuple[str, str, int]] = []
    for py_file in EFFECTS_DIR.glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            arg_names = [arg.arg for arg in node.args.args]
            if "effect" not in arg_names or "k" not in arg_names:
                continue
            if any(
                isinstance(decorator, ast.Name) and decorator.id == "do"
                for decorator in node.decorator_list
            ):
                violations.append((py_file.name, node.name, node.lineno))
    return sorted(violations)


def test_no_handler_functions_in_effects_directory() -> None:
    """No module-level @do handlers (effect, k) should live in effects/."""
    violations = [
        f"{filename}:{line_number} {function_name}"
        for filename, function_name, line_number in _handler_definitions_in_effects()
    ]
    assert violations == [], f"Handler functions found in effects/: {violations}"


def test_effects_directory_has_no_handler_named_exports() -> None:
    """effects/__init__.py should not export anything with 'handler' in the name."""
    import doeff.effects as effects_pkg

    handler_exports = [name for name in dir(effects_pkg) if "handler" in name.lower()]
    assert handler_exports == [], f"Handler exports in effects/: {handler_exports}"
