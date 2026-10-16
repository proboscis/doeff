"""Release ownership contract for public workspace packages."""

from __future__ import annotations

import re
import shlex
from pathlib import Path

import tomllib
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "release-publish-runbook.md"
WORKFLOWS = ROOT / ".github" / "workflows"
PUBLISH_WORKFLOW = WORKFLOWS / "publish.yml"

NATIVE_ROOT_RELEASE_PACKAGES = {"doeff-vm", "doeff-indexer"}

PYPI_UPLOAD_ACTION = "pypa/gh-action-pypi-publish"
UNPUBLISHED_CHECK_TOOL = "tools/check_pypi_unpublished.py"
PUBLISH_CHECK_JOB = "check-versions-unpublished"
NATIVE_RELEASE_WORKFLOWS = {
    "build-vm.yml": "packages/doeff-vm",
    "build-indexer.yml": "packages/doeff-indexer",
}


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


def _package_name(package_dir: str) -> str:
    pyproject = ROOT / package_dir / "pyproject.toml"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["name"]


def _workflow_jobs(workflow: Path) -> dict[str, dict]:
    return yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]


def _needs(job: dict) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def _uploads_to_pypi(job: dict) -> bool:
    return any(step.get("uses", "").startswith(PYPI_UPLOAD_ACTION) for step in job.get("steps", []))


def _unpublished_check_targets(job: dict) -> tuple[str, ...]:
    """The package dirs one job hands to the unpublished-version check (empty if it runs none)."""
    for step in job.get("steps", []):
        words = shlex.split(step.get("run", "").replace("\\\n", " "))
        if UNPUBLISHED_CHECK_TOOL in words:
            arguments = words[words.index(UNPUBLISHED_CHECK_TOOL) + 1 :]
            return tuple(word for word in arguments if not word.startswith("-"))
    return ()


def _unpublished_check_jobs(jobs: dict[str, dict]) -> dict[str, tuple[str, ...]]:
    return {
        name: targets for name, job in jobs.items() if (targets := _unpublished_check_targets(job))
    }


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


def test_every_pypi_upload_waits_for_the_unpublished_version_check() -> None:
    """Uploads run with skip-existing, so an unbumped version would upload nothing and pass.

    Every job that uploads must therefore need a job that ran tools/check_pypi_unpublished.py.
    """
    workflows = {workflow.name: _workflow_jobs(workflow) for workflow in WORKFLOWS.glob("*.yml")}
    unchecked_uploads = {
        f"{workflow}:{name}": sorted(_needs(job))
        for workflow, jobs in workflows.items()
        for name, job in jobs.items()
        if _uploads_to_pypi(job) and not _needs(job) & _unpublished_check_jobs(jobs).keys()
    }
    uploads = [
        name for jobs in workflows.values() for name, job in jobs.items() if _uploads_to_pypi(job)
    ]

    assert uploads
    assert unchecked_uploads == {}, f"these jobs upload without needing {UNPUBLISHED_CHECK_TOOL}"


def test_publish_workflow_checks_every_root_tag_package_before_any_upload() -> None:
    """One forgotten bump must stop the whole root release before its first upload."""
    jobs = _workflow_jobs(PUBLISH_WORKFLOW)
    check_jobs = _unpublished_check_jobs(jobs)

    assert set(check_jobs) == {PUBLISH_CHECK_JOB}
    assert "if" not in jobs[PUBLISH_CHECK_JOB], "the check also runs for build-only dispatches"
    checked_packages = {_package_name(package_dir) for package_dir in check_jobs[PUBLISH_CHECK_JOB]}
    assert checked_packages == _markdown_package_table("### Root Tag Release Packages")

    for name, job in jobs.items():
        if name in {PUBLISH_CHECK_JOB, "build-python-release-dists"}:
            continue
        assert PUBLISH_CHECK_JOB in _needs(job), f"publish.yml:{name} runs before the check"

    assert UNPUBLISHED_CHECK_TOOL in RUNBOOK.read_text(encoding="utf-8")


def test_native_release_workflows_check_their_own_package_before_upload() -> None:
    """build-vm.yml / build-indexer.yml also publish from their own package tags."""
    for workflow_name, package_dir in NATIVE_RELEASE_WORKFLOWS.items():
        jobs = _workflow_jobs(WORKFLOWS / workflow_name)
        check_jobs = _unpublished_check_jobs(jobs)

        assert list(check_jobs.values()) == [(package_dir,)], workflow_name
        assert all("if" not in jobs[name] for name in check_jobs), workflow_name


def test_independent_public_packages_have_documented_dist_verification() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    independent_release_packages = _markdown_package_table("### Independent Public Packages")

    assert "uv build --package <package> --wheel --sdist" in runbook
    assert "uv run python tools/verify_dist_metadata.py" in runbook
    assert "tag name: `<package>/vX.Y.Z`" in runbook
    assert independent_release_packages


PYPI_ENVIRONMENT = "pypi"
PYPI_TOKEN_PASSWORD = "${{ secrets.PYPI_API_TOKEN }}"


def _upload_steps(job: dict) -> list[dict]:
    return [step for step in job.get("steps", []) if step.get("uses", "").startswith(PYPI_UPLOAD_ACTION)]


def _environment_name(job: dict) -> str | None:
    environment = job.get("environment")
    return environment.get("name") if isinstance(environment, dict) else environment


def _local_reusable_workflow(job: dict) -> Path | None:
    uses = job.get("uses", "")
    return ROOT / uses if uses.startswith("./.github/workflows/") else None


def test_every_pypi_upload_job_can_use_trusted_publishing() -> None:
    """Trusted Publishing needs an OIDC token; the token path stays until the secret is removed.

    The action uses the API token while secrets.PYPI_API_TOKEN is set and Trusted Publishing
    (OIDC) when it is empty, so every upload passes exactly that secret and nothing else.
    """
    workflows = {workflow.name: _workflow_jobs(workflow) for workflow in WORKFLOWS.glob("*.yml")}
    upload_jobs = {
        f"{workflow}:{name}": job
        for workflow, jobs in workflows.items()
        for name, job in jobs.items()
        if _uploads_to_pypi(job)
    }

    assert upload_jobs
    for name, job in upload_jobs.items():
        assert job.get("permissions", {}).get("id-token") == "write", name
        assert _environment_name(job) == PYPI_ENVIRONMENT, name
        for step in _upload_steps(job):
            assert step["with"].get("password") == PYPI_TOKEN_PASSWORD, name
            assert step["with"].get("user", "__token__") == "__token__", name


def test_publish_workflow_uploads_native_packages_from_its_own_jobs() -> None:
    """PyPI rejects Trusted Publishing from inside a reusable workflow.

    publish.yml therefore calls build-vm.yml / build-indexer.yml only to build, and uploads
    their wheels from a job of its own.
    """
    jobs = _workflow_jobs(PUBLISH_WORKFLOW)

    for name, job in jobs.items():
        if _local_reusable_workflow(job) is not None:
            assert job.get("with", {}).get("publish") is False, name
            assert "secrets" not in job, f"{name} hands secrets to a build-only call"

    for package in sorted(NATIVE_ROOT_RELEASE_PACKAGES):
        assert _uploads_to_pypi(jobs[f"publish-{package}"]), package


def test_reusable_calls_grant_the_id_token_their_nested_uploads_request() -> None:
    """GitHub caps a called workflow's permissions at its caller's and refuses the run otherwise."""
    jobs = _workflow_jobs(PUBLISH_WORKFLOW)

    for name, job in jobs.items():
        workflow = _local_reusable_workflow(job)
        if workflow is None:
            continue
        nested = _workflow_jobs(workflow)
        if any(nested_job.get("permissions", {}).get("id-token") == "write" for nested_job in nested.values()):
            assert job.get("permissions", {}).get("id-token") == "write", name


def test_runbook_documents_both_pypi_credentials() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "## PyPI Credentials" in runbook
    assert "Trusted Publishing" in runbook
    assert "pending publisher" in runbook
    assert "| Workflow name | `publish.yml` |" in runbook
    assert f"| Environment name | `{PYPI_ENVIRONMENT}` |" in runbook
    assert "`PYPI_API_TOKEN`" in runbook
