"""Make the pure-Python front end importable without building the Rust extension.

The package is a maturin mixed project (``python/`` + the ``_native`` extension).
The Python front end (``program_effects`` / ``handler_effects``) does not need
the extension, so tests put ``python/`` on ``sys.path`` when the package is not
installed.

``make_package`` writes a throwaway package (``{pkg}`` in a file's text is the
package name) under ``tmp_path`` and removes it from ``sys.modules`` afterwards.
"""

import importlib
import importlib.util
import sys
import textwrap
import uuid
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path

import pytest

if importlib.util.find_spec("doeff_effect_analyzer") is None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "python"))


@pytest.fixture
def make_package(tmp_path: Path) -> Iterator[Callable[[Mapping[str, str]], str]]:
    import hy  # noqa: F401 - .hy import hook

    names: list[str] = []

    def make(files: Mapping[str, str]) -> str:
        name = f"fx_{uuid.uuid4().hex[:8]}"
        root = tmp_path / name
        root.mkdir()
        for filename, text in {"__init__.py": "", **files}.items():
            body = text.replace("{pkg}", name)
            (root / filename).write_text(textwrap.dedent(body), encoding="utf-8")
        names.append(name)
        return name

    sys.path.insert(0, str(tmp_path))
    importlib.invalidate_caches()
    yield make
    sys.path.remove(str(tmp_path))
    for name in names:
        for module in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
            del sys.modules[module]
