"""doeff-agentic は program を包む関数を持たず、工場 agentic_effectful_handlers(…)(p) と doeff の stacked_handlers(*handlers)(p) で
当てる(ADR-DOE-API-001 R6・R16)。with_agentic_effectful_handlers・with_handlers と submodule doeff_agentic.runtime は消えた。

root の tests/test_handler_wrapper_factories.py(program に handler を被せていた 8 名)から、doeff-agentic の分を分けた
(2026-11-04 — root の tests が内輪の package を import しないため)。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import doeff_agentic
import pytest

from doeff import stacked_handlers


def _marked(value: object) -> bool:
    return getattr(value, "_doeff_is_handler_fn", False) is True


def test_agentic_wraps_through_its_factory_and_doeff_stacked_handlers(tmp_path: Path) -> None:
    assert not hasattr(doeff_agentic, "with_agentic_effectful_handlers")
    assert not hasattr(doeff_agentic, "with_handlers")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("doeff_agentic.runtime")
    agentic = doeff_agentic.agentic_effectful_handlers(
        workflow_name="factory", state_dir=tmp_path, agent_runtime=stacked_handlers()
    )
    assert _marked(agentic)
