"""Invariant tests: the doeff-hy pytest plugin lives in a distribution that has no .hy files.

pytest marks every module of a distribution with a pytest11 entry point for assert rewriting
(`_pytest.config._iter_rewritable_modules`). When doeff-hy itself had an entry point, a repo installed from a wheel
tried to rewrite `doeff_hy/macros.hy` as Python and the import failed with SyntaxError (measured 2026-09-26).
"""

from __future__ import annotations

import importlib
import importlib.metadata as metadata
from pathlib import Path

import pytest
import tomllib
from packaging.requirements import Requirement

PACKAGES = Path(__file__).resolve().parents[2]


def _project(package: str) -> dict[str, object]:
    """Read the distribution declaration ([project] in pyproject.toml) - so the check holds whether installed editable or as a wheel."""
    with (PACKAGES / package / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_doeff_hy_itself_declares_no_pytest_plugin() -> None:
    """doeff-hy, the distribution that has .hy files, has no pytest11 entry point (otherwise pytest would mark the .hy files for rewriting)."""
    assert "pytest11" not in _project("doeff-hy").get("entry-points", {})


def test_the_plugin_lives_in_a_distribution_without_hy_sources() -> None:
    """The plugin name doeff_hy is owned by doeff-hy-pytest, whose source has no .hy. doeff-hy pulls it in as a dependency."""
    entry_points = _project("doeff-hy-pytest")["entry-points"]
    assert entry_points["pytest11"] == {"doeff_hy": "doeff_hy_pytest"}
    assert not list((PACKAGES / "doeff-hy-pytest" / "src").rglob("*.hy"))
    # The dependency carries a version-range spec (the >=0.7.0,<0.8 form - tests/test_core_packages_version.py), so compare by PEP 508 name.
    assert "doeff-hy-pytest" in {
        Requirement(line).name for line in _project("doeff-hy")["dependencies"]
    }


def test_installed_plugin_entry_point_belongs_to_doeff_hy_pytest() -> None:
    """Even in the installed environment, exactly one distribution claims the pytest11 name doeff_hy: doeff-hy-pytest."""
    owners = [
        dist.metadata["Name"]
        for dist in metadata.distributions()
        for entry in dist.entry_points
        if entry.group == "pytest11" and entry.name == "doeff_hy"
    ]
    assert owners == ["doeff-hy-pytest"], owners


def test_the_plugin_shows_only_the_two_declarations() -> None:
    """The entry point exposes only the two per-test declarations; the hooks are attributes pytest picks up. The measuring internals live in
    modules starting with _, and there is no module without _ - the move of the old paths (budget, python_calls) was removed too."""
    import doeff_hy_pytest

    assert doeff_hy_pytest.__all__ == ["declare_process_tree_cpu", "declare_step_budget"]
    assert callable(doeff_hy_pytest.pytest_terminal_summary)
    source = PACKAGES / "doeff-hy-pytest" / "src" / "doeff_hy_pytest"
    open_modules = sorted(
        path.stem for path in source.glob("*.py") if not path.name.startswith("_")
    )
    assert open_modules == []
    for name in ("budget", "python_calls"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"doeff_hy_pytest.{name}")


def test_the_plugin_package_ships_its_types() -> None:
    """doeff_hy_pytest has the py.typed marker - so that when a user test imports declare_step_budget, pyright strict does not go red on
    a module without type stubs (reportMissingTypeStubs)."""
    assert (PACKAGES / "doeff-hy-pytest" / "src" / "doeff_hy_pytest" / "py.typed").is_file()
