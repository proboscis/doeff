"""Import coverage for Hy handler modules used by doeff-ml-nexus."""

from __future__ import annotations

import importlib

import hy  # noqa: F401
import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "doeff_docker.handlers.dockerfile",
        "doeff_docker.handlers.docker",
        "doeff_ml_nexus.handlers.file",
        "doeff_ml_nexus.handlers.docker",
        "doeff_ml_nexus.handlers.rsync",
        "doeff_ml_nexus.handlers.resolve",
    ],
)
def test_handler_modules_import_under_current_macro_guard(module_name: str) -> None:
    importlib.import_module(module_name)
