"""The version numbers of the 5 core packages (doeff, doeff-vm, doeff-core-effects, doeff-hy, doeff-hy-pytest) agree on one number, and their mutual dependencies are limited to the
series of that number: failure cases (section 3 of the initiative doeff-publish).

The 5 are released under one number -- one number tells which combination fits (section "How versions are assigned" of the scope-and-procedure report). There are 8 places that hold a version:
project.version of the 5 pyprojects, __version__ of doeff, package.version of doeff-vm's Cargo.toml, and the doeff-vm row of Cargo.lock.
Mutual dependencies (dependencies and optional-dependencies) are limited to the 2-digit series of that number (>=0.5.0,<0.6 for 0.5.0) -- so that when installed from PyPI,
parts of a different series are not combined. In local development the workspace specification in [tool.uv.sources] resolves those dependencies to the working-tree parts,
so it is kept alongside the version specification.
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest
import tomllib
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO = Path(__file__).resolve().parents[1]
VM = REPO / "packages" / "doeff-vm"

# Names of the 5 core packages -> the dir holding pyproject.toml (the same set as tests/test_core_packages_license.py).
CORE = {
    "doeff": REPO,
    "doeff-vm": VM,
    "doeff-core-effects": REPO / "packages" / "doeff-core-effects",
    "doeff-hy": REPO / "packages" / "doeff-hy",
    "doeff-hy-pytest": REPO / "packages" / "doeff-hy-pytest",
}


def _toml(path: Path) -> dict[str, object]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _project(name: str) -> dict[str, object]:
    match _toml(CORE[name] / "pyproject.toml").get("project"):
        case dict() as project:
            return project
        case _:
            pytest.fail(f"pyproject.toml of {name} has no project table")


def _pyproject_version(name: str) -> str:
    match _project(name).get("version"):
        case str() as version:
            return version
        case other:
            pytest.fail(
                f"project.version of {name} is not a string (this check cannot read a dynamic version): {other!r}"
            )


def _dunder_version() -> str:
    tree = ast.parse((REPO / "doeff" / "__init__.py").read_text(encoding="utf-8"))
    values = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    assert len(values) == 1, (
        f"there is not exactly one top-level __version__ = '<version>' in doeff/__init__.py: {values}"
    )
    return values[0]


def _cargo_version() -> str:
    match _toml(VM / "Cargo.toml").get("package"):
        case {"version": str() as version}:
            return version
        case other:
            pytest.fail(
                f"package.version of packages/doeff-vm/Cargo.toml is not a string: {other!r}"
            )


def _cargo_lock_version() -> str:
    match _toml(VM / "Cargo.lock").get("package"):
        case list() as packages:
            versions = [
                package.get("version")
                for package in packages
                if isinstance(package, dict) and package.get("name") == "doeff-vm"
            ]
        case other:
            pytest.fail(f"packages/doeff-vm/Cargo.lock has no package list: {other!r}")
    match versions:
        case [str() as version]:
            return version
        case _:
            pytest.fail(f"packages/doeff-vm/Cargo.lock does not have exactly one doeff-vm row: {versions}")


# Places that hold a version (path from the repo root and field) -> a function that reads the version there. The reference is the version in the doeff pyproject (the number of the public tag v<version>).
PLACES: dict[str, Callable[[], str]] = {
    **{
        f"{(CORE[name] / 'pyproject.toml').relative_to(REPO)}:project.version": partial(
            _pyproject_version, name
        )
        for name in CORE
        if name != "doeff"
    },
    "doeff/__init__.py:__version__": _dunder_version,
    "packages/doeff-vm/Cargo.toml:package.version": _cargo_version,
    "packages/doeff-vm/Cargo.lock:package.doeff-vm": _cargo_lock_version,
}


def _series(version: str) -> SpecifierSet:
    """The 2-digit series of a version number (0.5.0 -> >=0.5.0,<0.6)."""
    major, minor = (*Version(version).release, 0)[:2]
    return SpecifierSet(f">={major}.{minor}.0,<{major}.{minor + 1}")


def _strings(table: dict[str, object], key: str, where: str) -> list[str]:
    match table.get(key, []):
        case list() as items if all(isinstance(item, str) for item in items):
            return [str(item) for item in items]
        case other:
            pytest.fail(f"{where} is not a list of strings: {other!r}")


def _core_requirements(name: str) -> list[tuple[str, Requirement]]:
    """To read, together with the field name where they were written, the dependencies of name (dependencies and each extra of optional-dependencies) that point to core packages."""
    project = _project(name)
    rows = [
        ("dependencies", line)
        for line in _strings(project, "dependencies", f"dependencies of {name}")
    ]
    match project.get("optional-dependencies", {}):
        case dict() as extras:
            for extra in sorted(extras):
                where = f"optional-dependencies.{extra}"
                rows.extend((where, line) for line in _strings(extras, extra, f"{where} of {name}"))
        case other:
            pytest.fail(f"optional-dependencies of {name} is not a table: {other!r}")
    requirements = [(where, Requirement(line)) for where, line in rows]
    return [(where, req) for where, req in requirements if canonicalize_name(req.name) in CORE]


def _sources(directory: Path) -> dict[str, object]:
    match _toml(directory / "pyproject.toml").get("tool", {}):
        case {"uv": {"sources": dict() as sources}}:
            return {canonicalize_name(key): value for key, value in sources.items()}
        case _:
            return {}


# The checks below are not split per place or per package; they list every drifted place and fail as one -- they run every time on the whole-repo row of the contract check table, so the count is
# kept small (18 checks split per place and package took 16.7 seconds together with the root conftest under a load of about 115).


def test_every_place_holding_a_core_version_holds_the_doeff_version() -> None:
    expected = _pyproject_version("doeff")
    drifted = {place: version for place, read in PLACES.items() if (version := read()) != expected}
    assert not drifted, f"the 5 core packages are released under one number -- places that differ from the doeff version {expected}: {drifted}"


def test_each_core_dependency_is_limited_to_the_series_of_the_doeff_version() -> None:
    series = _series(_pyproject_version("doeff"))
    wrong = [
        f"{where} of {name}: {req}"
        for name in CORE
        for where, req in _core_requirements(name)
        if req.url is not None or req.specifier != series
    ]
    assert not wrong, f"dependencies on core packages are not of series {series}: {wrong}"


def test_each_core_dependency_resolves_to_the_workspace_member_in_development() -> None:
    root = _sources(REPO)
    loose = [
        f"{name} → {req.name}"
        for name in CORE
        for _, req in _core_requirements(name)
        # uv looks up the member's [tool.uv.sources] first, and for names not there, the workspace root's [tool.uv.sources].
        if {**root, **_sources(CORE[name])}.get(canonicalize_name(req.name)) != {"workspace": True}
    ]
    assert not loose, (
        f"dependencies on core packages {loose} are not resolved to workspace sources -- local development would pull the PyPI version"
    )


def test_the_dependency_checks_read_the_core_edges() -> None:
    # If the dependency reading picked up nothing, the two checks above would silently go green (for example, when moved to a field that does not read dependencies).
    edges = sorted(
        (name, canonicalize_name(req.name)) for name in CORE for _, req in _core_requirements(name)
    )
    assert ("doeff", "doeff-vm") in edges, (
        f"the dependency reading of core packages lacks doeff -> doeff-vm: {edges}"
    )
