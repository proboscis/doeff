"""Failure cases for the distributions of the 5 core packages (doeff, doeff-vm, doeff-core-effects, doeff-hy, doeff-hy-pytest) carrying the same MIT LICENSE as the root
(section 2 of the doeff-publish effort).

The license field is written as a PEP 639 SPDX expression (`license = "MIT"`) plus `license-files`. The build backend turns these into the core metadata
License-Expression and License-File, and puts LICENSE in the wheel's dist-info/licenses and the root of the sdist. In PEP 639 the license
classifier is deprecated and is not listed alongside License-Expression. Old versions of build backends do not write License-Expression (hatchling 1.26's default
metadata is before 2.4, and maturin 1.9.2 does not put license-files in the sdist), so the lower bound of build-system rejects those versions.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import tomllib
from packaging.requirements import Requirement

REPO = Path(__file__).resolve().parents[1]
ROOT_LICENSE = REPO / "LICENSE"

# Names of the 5 core packages -> the dir where pyproject.toml lives.
CORE = {
    "doeff": REPO,
    "doeff-vm": REPO / "packages" / "doeff-vm",
    "doeff-core-effects": REPO / "packages" / "doeff-core-effects",
    "doeff-hy": REPO / "packages" / "doeff-hy",
    "doeff-hy-pytest": REPO / "packages" / "doeff-hy-pytest",
}

# Build backend name -> the last version that does not write the PEP 639 fields as License-Expression and License-File (the lower bound rejects this version).
LAST_BACKEND_WITHOUT_PEP639 = {"hatchling": "1.26.3", "maturin": "1.9.2"}


def _table(directory: Path, *path: str) -> dict[str, object]:
    """Extract nested tables of a package's pyproject.toml (project, build-system, tool.hatch.build.targets.sdist),
    failing with the table name if one is absent."""
    table: dict[str, object] = tomllib.loads(
        (directory / "pyproject.toml").read_text(encoding="utf-8")
    )
    for key in path:
        match table.get(key):
            case dict() as inner:
                table = inner
            case _:
                pytest.fail(f"{directory / 'pyproject.toml'} has no table {'.'.join(path)}")
    return table


def _strings(table: dict[str, object], key: str) -> list[str]:
    """Read a table's list of strings (classifiers, requires, include) (empty if absent)."""
    match table.get(key, []):
        case list() as items if all(isinstance(item, str) for item in items):
            return [str(item) for item in items]
        case other:
            pytest.fail(f"{key} is not a list of strings: {other!r}")


@pytest.mark.parametrize("name", sorted(CORE))
def test_each_core_package_declares_mit_and_ships_the_root_license(name: str) -> None:
    directory = CORE[name]
    project = _table(directory, "project")
    assert project.get("name") == name
    assert project.get("license") == "MIT", (
        f"the license field of {name} is not the SPDX expression MIT: {project.get('license')!r}"
    )
    assert project.get("license-files") == ["LICENSE"], (
        f"the license-files of {name} is not LICENSE: {project.get('license-files')!r}"
    )
    license_file = directory / "LICENSE"
    assert license_file.is_file(), f"the dir of {name} has no LICENSE"
    assert license_file.read_bytes() == ROOT_LICENSE.read_bytes(), (
        f"the LICENSE of {name} differs from the root LICENSE"
    )
    classifiers = [
        line for line in _strings(project, "classifiers") if line.startswith("License ::")
    ]
    assert not classifiers, (
        f"{name} lists License-Expression alongside a license classifier (deprecated by PEP 639): {classifiers}"
    )


@pytest.mark.parametrize("name", sorted(CORE))
def test_each_core_package_build_backend_writes_the_license_expression(name: str) -> None:
    requires = [
        Requirement(line) for line in _strings(_table(CORE[name], "build-system"), "requires")
    ]
    backends = [
        requirement for requirement in requires if requirement.name in LAST_BACKEND_WITHOUT_PEP639
    ]
    assert len(backends) == 1, (
        f"the requires of {name}'s build-system does not have exactly one build backend: {requires}"
    )
    (backend,) = backends
    old = LAST_BACKEND_WITHOUT_PEP639[backend.name]
    assert not backend.specifier.contains(old), (
        f"the lower bound {backend} of {name}'s build backend allows {old}, which does not write License-Expression"
    )


def test_the_doeff_sdist_include_is_anchored_to_the_root() -> None:
    # A pattern without a leading / matches the same name at any depth in gitignore form, and even the LICENSE (not MIT) in other components' test data
    # went into doeff's sdist.
    sdist = _table(REPO, "tool", "hatch", "build", "targets", "sdist")
    loose = [pattern for pattern in _strings(sdist, "include") if not pattern.startswith("/")]
    assert not loose, f"doeff's sdist include has patterns not anchored to the root: {loose}"
