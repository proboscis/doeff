"""ADR-DOE-ENFORCE-001 R8: a wired test tree whose package is not installed is worse than a dark one.

A tree in ``testpaths`` whose package is missing from the dev environment does
not go quiet — it errors on every single run, and the error is not about the
code under test.  Eight workspace packages were in exactly that position before
2026-09-19 (doeff-docker, doeff-events, doeff-google-secret-manager,
doeff-ml-nexus, doeff-notify, doeff-secret, doeff-seedream, doeff-test-target):
absent from the root dev group, so ``make sync`` never installed them, so their
tests could only ever have been collected as ImportErrors.

This test names that requirement directly instead of leaving it to be inferred
from a green collection: every package that contributes a tree to the canonical
gate must be importable from the environment ``make sync`` builds.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def _wired_package_modules() -> list[tuple[str, str]]:
    """(package directory, top-level module) for every package in testpaths."""
    ini = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    testpaths = ini["tool"]["pytest"]["ini_options"]["testpaths"]
    packages = sorted(
        {path.split("/")[1] for path in testpaths if path.startswith("packages/")}
    )
    wired: list[tuple[str, str]] = []
    for package in packages:
        base = ROOT / "packages" / package
        # src layout first, flat layout second; a package that ships no Python
        # module (a Rust crate whose tests read its sources) contributes none.
        inits = sorted((base / "src").glob("*/__init__.py")) + sorted(
            base.glob("*/__init__.py")
        )
        for init in inits:
            module = init.parent.name
            if module.startswith((".", "test")):
                continue
            wired.append((package, module))
    return wired


@pytest.mark.parametrize(
    ("package", "module"),
    _wired_package_modules(),
    ids=lambda value: value,
)
def test_package_behind_a_wired_test_tree_is_importable(package: str, module: str) -> None:
    try:
        importlib.import_module(module)
    except ImportError as exc:
        pytest.fail(
            f"packages/{package} contributes a tree to testpaths but {module} does not "
            f"import from this environment: {exc}\n"
            "Either add the package to [dependency-groups].dev (and a workspace entry "
            "under [tool.uv.sources]) so `make sync` installs it, or take its tree out "
            "of testpaths and declare it in doeff_adr_wiring_exclude with the reason "
            "(ADR-DOE-ENFORCE-001 R8).",
            pytrace=False,
        )
