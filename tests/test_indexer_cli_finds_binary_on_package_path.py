"""Test that the entry of the doeff-indexer CLI (packages/doeff-indexer/python/doeff_indexer/_cli.py) searches for the bundled binary in the order of the package search path.
The editable install of the build entry puts the build artifact (this binary) in the venv's
`__editable__.doeff_indexer.native/` and makes the package search path artifact dir -> working tree dir. Before, it looked only at `Path(__file__).parent`,
so with an editable install it saw only the working tree dir and could not find the binary.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / "packages" / "doeff-indexer" / "python" / "doeff_indexer" / "_cli.py"


def _cli(monkeypatch: pytest.MonkeyPatch, places: list[Path]) -> types.ModuleType:
    """Load _cli under a package doeff_indexer whose search path is places."""
    package = types.ModuleType("doeff_indexer")
    package.__path__ = [str(place) for place in places]
    monkeypatch.setitem(sys.modules, "doeff_indexer", package)
    spec = importlib.util.spec_from_file_location("doeff_indexer._cli", CLI)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_binary_is_found_in_the_first_package_place_that_has_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    native, source = tmp_path / "native" / "doeff_indexer", tmp_path / "source" / "doeff_indexer"
    (native / "bin").mkdir(parents=True)
    source.mkdir(parents=True)
    module = _cli(monkeypatch, [native, source])
    assert module._get_binary_path() is None
    binary = native / "bin" / module._get_binary_name()
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    assert module._get_binary_path() == binary
