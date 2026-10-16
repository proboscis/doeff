"""Release contract for the native doeff-vm package."""

from __future__ import annotations

import re
from pathlib import Path

import tomllib
import yaml
from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def _read_toml(path: Path) -> dict:
    return tomllib.loads(path.read_text())


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _dependency_lower_bound(dependencies: list[str], package_name: str) -> str:
    # The core packages also cap each other at the next minor series
    # (tests/test_core_packages_version.py), so the requirement carries a `<` bound next to `>=`.
    for requirement in map(Requirement, dependencies):
        lower = [spec.version for spec in requirement.specifier if spec.operator == ">="]
        if requirement.name == package_name and len(lower) == 1:
            return lower[0]
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


def test_publish_workflow_releases_root_workspace_runtime_dependencies() -> None:
    root_pyproject = _read_toml(ROOT / "pyproject.toml")
    workflow = (ROOT / ".github" / "workflows" / "publish.yml").read_text()

    workspace_sources = set(root_pyproject["tool"]["uv"]["sources"])
    runtime_workspace_deps = _dependency_names(root_pyproject["project"]["dependencies"]) & workspace_sources

    for package_name in runtime_workspace_deps - {"doeff-vm", "doeff-indexer"}:
        assert re.search(rf"package:\s+{re.escape(package_name)}\b", workflow)
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


def test_linux_aarch64_doeff_vm_wheels_build_natively_on_an_arm_runner() -> None:
    """Cross-compiling aarch64 on an x86_64 runner uses rust-cross/manylinux2014-cross, whose GCC 4.8
    rejects the -Wno-error=date-time flag that libmimalloc-sys passes (Build doeff-vm run 38937296520)."""
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "build-vm.yml").read_text(encoding="utf-8"))["jobs"]

    for name in ("build-linux", "build-linux-freethreaded"):
        entries = jobs[name]["strategy"]["matrix"]["include"]
        runners = {entry["target"]: entry["runner"] for entry in entries}

        assert jobs[name]["runs-on"] == "${{ matrix.runner }}"
        assert runners == {"x86_64": "ubuntu-latest", "aarch64": "ubuntu-24.04-arm"}


def test_linux_free_threaded_doeff_vm_wheels_name_only_python_3_14t() -> None:
    """maturin 1.15 refuses a named 3.13t interpreter in the manylinux images ("Free-threaded Python
    interpreter is only supported on 3.14 and later" — Build doeff-vm run 38939832924)."""
    jobs = yaml.safe_load((ROOT / ".github" / "workflows" / "build-vm.yml").read_text(encoding="utf-8"))["jobs"]
    build = next(step for step in jobs["build-linux-freethreaded"]["steps"] if step.get("name") == "Build free-threaded wheels")

    assert build["with"]["args"] == "--release --out dist -i python3.14t"


def test_pypi_publish_actions_use_current_skip_existing_input_name() -> None:
    for path in [
        ROOT / ".github" / "workflows" / "build-vm.yml",
        ROOT / ".github" / "workflows" / "build-indexer.yml",
        ROOT / ".github" / "workflows" / "publish.yml",
    ]:
        text = path.read_text(encoding="utf-8")

        assert "skip-existing: true" in text
        assert "skip_existing:" not in text
