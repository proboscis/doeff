"""Release ownership contract for public workspace packages."""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "release-publish-runbook.md"
PUBLISH_WORKFLOW = ROOT / ".github" / "workflows" / "publish.yml"

NATIVE_ROOT_RELEASE_PACKAGES = {"doeff-vm", "doeff-indexer"}


def _markdown_package_table(heading: str) -> set[str]:
    text = RUNBOOK.read_text(encoding="utf-8")
    lines = text.splitlines()
    try:
        start = lines.index(heading)
    except ValueError as exc:
        raise AssertionError(f"Runbook is missing heading: {heading}") from exc

    packages: set[str] = set()
    for line in lines[start + 1 :]:
        if line.startswith(("## ", "### ")):
            break
        if not line.startswith("|"):
            continue
        if "---" in line:
            continue
        first_cell = line.split("|")[1].strip()
        match = re.fullmatch(r"`([^`]+)`", first_cell)
        if match:
            packages.add(match.group(1))
    if not packages:
        raise AssertionError(f"Runbook table under {heading} has no package rows")
    return packages


def _readme_install_packages() -> set[str]:
    packages: set[str] = set()
    pattern = re.compile(r"\b(?:pip install|uv add)\s+(doeff-[a-z0-9-]+)\b")
    for readme in ROOT.glob("packages/*/README.md"):
        packages.update(pattern.findall(readme.read_text(encoding="utf-8")))
    return packages


def _workspace_package_names() -> set[str]:
    packages: set[str] = set()
    for pyproject in ROOT.glob("packages/*/pyproject.toml"):
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        packages.add(data["project"]["name"])
    return packages


def test_every_workspace_package_has_release_classification() -> None:
    classified_packages = (
        _markdown_package_table("### Root Tag Release Packages")
        | _markdown_package_table("### Independent Public Packages")
        | _markdown_package_table("### Not Publicly Published")
    )

    assert _workspace_package_names() <= classified_packages


def test_public_readme_install_surface_has_release_owner() -> None:
    public_install_packages = _readme_install_packages()
    root_release_packages = _markdown_package_table("### Root Tag Release Packages")
    independent_release_packages = _markdown_package_table("### Independent Public Packages")

    assert public_install_packages <= root_release_packages | independent_release_packages


def test_publish_workflow_matches_root_tag_release_package_contract() -> None:
    root_release_packages = _markdown_package_table("### Root Tag Release Packages")
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")

    assert "build-python-release-dists" in workflow
    assert "uv build --package ${{ matrix.package }}" in workflow
    assert "tools/verify_dist_metadata.py ${{ matrix.out-dir }}/*.whl" in workflow

    for package in sorted(root_release_packages - NATIVE_ROOT_RELEASE_PACKAGES):
        assert re.search(rf"package:\s+{re.escape(package)}\b", workflow)
        assert f"{package}-dist" in workflow
        assert f"publish-{package}" in workflow

    for package in sorted(NATIVE_ROOT_RELEASE_PACKAGES):
        assert f"publish-{package}" in workflow


def test_independent_public_packages_have_documented_dist_verification() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    independent_release_packages = _markdown_package_table("### Independent Public Packages")

    assert "uv build --package <package> --wheel --sdist" in runbook
    assert "uv run python tools/verify_dist_metadata.py" in runbook
    assert "tag name: `<package>/vX.Y.Z`" in runbook
    assert independent_release_packages
