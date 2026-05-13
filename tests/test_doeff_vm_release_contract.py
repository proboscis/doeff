"""Release contract for the native doeff-vm package."""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def _read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _dependency_lower_bound(dependencies: list[str], package_name: str) -> str:
    for dep in dependencies:
        match = re.fullmatch(rf"{re.escape(package_name)}>=([0-9]+(?:\.[0-9]+)*)", dep)
        if match:
            return match.group(1)
    raise AssertionError(f"{package_name} dependency must use an explicit >= lower bound")


def _dependency_names(dependencies: list[str]) -> set[str]:
    return {re.split(r"[<>=!~; ]", dependency, maxsplit=1)[0] for dependency in dependencies}


def test_doeff_vm_release_version_is_newer_than_published_stale_wheel() -> None:
    """0.1.0 is already published; reusing it leaves Windows hosts on stale wheels."""
    vm_pyproject = _read_toml(ROOT / "packages" / "doeff-vm" / "pyproject.toml")
    vm_cargo = _read_toml(ROOT / "packages" / "doeff-vm" / "Cargo.toml")

    python_version = vm_pyproject["project"]["version"]
    cargo_version = vm_cargo["package"]["version"]

    assert python_version == cargo_version
    assert _version_tuple(python_version) >= (0, 2, 0)


def test_root_doeff_requires_current_doeff_vm_release() -> None:
    """The pure-Python package must not accept a VM older than the local ABI/export surface."""
    root_pyproject = _read_toml(ROOT / "pyproject.toml")
    vm_pyproject = _read_toml(ROOT / "packages" / "doeff-vm" / "pyproject.toml")

    lower_bound = _dependency_lower_bound(root_pyproject["project"]["dependencies"], "doeff-vm")

    assert _version_tuple(lower_bound) >= _version_tuple(vm_pyproject["project"]["version"])


def test_windows_doeff_vm_wheel_smoke_workflow_exists() -> None:
    workflow = ROOT / ".github" / "workflows" / "doeff-vm-windows-wheel.yml"
    text = workflow.read_text()

    assert "runs-on: windows-latest" in text
    assert "PyO3/maturin-action" in text
    assert 'python-version: "3.12"' in text
    assert "doeff_vm.GetHandlers" in text


def test_publish_workflow_releases_doeff_agents() -> None:
    workflow = ROOT / ".github" / "workflows" / "publish.yml"
    text = workflow.read_text()

    assert "build-doeff-agents-dist" in text
    assert "uv build --package doeff-agents" in text
    assert "doeff-agents-dist" in text
    assert "publish-doeff-agents" in text


def test_publish_workflow_releases_root_workspace_runtime_dependencies() -> None:
    root_pyproject = _read_toml(ROOT / "pyproject.toml")
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text()

    workspace_sources = set(root_pyproject["tool"]["uv"]["sources"])
    runtime_workspace_deps = _dependency_names(root_pyproject["project"]["dependencies"]) & workspace_sources

    for package_name in runtime_workspace_deps - {"doeff-vm", "doeff-indexer"}:
        assert f"uv build --package {package_name}" in workflow
        assert f"{package_name}-dist" in workflow
        assert f"publish-{package_name}" in workflow


def test_reusable_native_publish_workflows_honor_publish_input() -> None:
    for path in [
        ROOT / ".github" / "workflows" / "build-vm.yml",
        ROOT / ".github" / "workflows" / "build-indexer.yml",
    ]:
        text = path.read_text(encoding="utf-8")

        assert "|| inputs.publish" in text
        assert "github.event_name == 'workflow_call' && inputs.publish" not in text


def test_pypi_publish_actions_use_current_skip_existing_input_name() -> None:
    for path in [
        ROOT / ".github" / "workflows" / "build-vm.yml",
        ROOT / ".github" / "workflows" / "build-indexer.yml",
        ROOT / ".github" / "workflows" / "publish.yml",
    ]:
        text = path.read_text(encoding="utf-8")

        assert "skip-existing: true" in text
        assert "skip_existing:" not in text
