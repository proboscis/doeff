"""doeff-indexer の CLI の入口(packages/doeff-indexer/python/doeff_indexer/_cli.py)が、同梱の binary を package の探し先の順に
探す事の検(agora-redesign #3860)。build の口の editable の入れは、組み立ての成果物(この binary)を venv の
`__editable__.doeff_indexer.native/` に入れ、package の探し先を 成果物の dir → 作業木の dir にする。前は `Path(__file__).parent` だけを見た
ので、editable の入れでは作業木の dir しか見ず、binary を見つけられない。
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parents[1] / "packages" / "doeff-indexer" / "python" / "doeff_indexer" / "_cli.py"


def _cli(monkeypatch: pytest.MonkeyPatch, places: list[Path]) -> types.ModuleType:
    """探し先が places の package doeff_indexer の下の _cli を読む。"""
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
