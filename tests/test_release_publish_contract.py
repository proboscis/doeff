"""Release ownership contract for public workspace packages.

The root tag release (`vX.Y.Z`, `.github/workflows/publish.yml`) publishes exactly the core five
packages (CORE_RELEASE_PACKAGES). Every other workspace package is published on its own or not at
all; docs/release-publish-runbook.md classifies each one.
"""

from __future__ import annotations

import ast
import re
import shlex
from collections.abc import Callable
from pathlib import Path

import tomllib
import yaml

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = ROOT / "docs" / "release-publish-runbook.md"
WORKFLOWS = ROOT / ".github" / "workflows"
PUBLISH_WORKFLOW = WORKFLOWS / "publish.yml"

# The runbook's three release classification tables and its post-publish section.
ROOT_RELEASE_TABLE = "### root の tag で出す部品"
INDEPENDENT_RELEASE_TABLE = "### 単独で出す部品"
NOT_PUBLISHED_TABLE = "### 公開しない部品"
POST_PUBLISH_SECTION = "## 出した後の確かめ"

# What the root tag publishes: the packages `pip install doeff` always installs together. The owner
# chose this range, the current names and Python 3.11-3.14 (request lt-8D749ZHBM6VF2HQEDE2SDMNASF,
# 2026-10-16); doeff-agents, doeff-time and doeff-indexer left the root release then.
CORE_RELEASE_PACKAGES = frozenset(
    {"doeff", "doeff-vm", "doeff-core-effects", "doeff-hy", "doeff-hy-pytest"}
)
NATIVE_ROOT_RELEASE_PACKAGES = frozenset({"doeff-vm"})
# The reusable workflow publish.yml calls (from job build-<package>) to build each native root
# package; publish-<package> uploads its wheels.
NATIVE_ROOT_RELEASE_WORKFLOWS = {"doeff-vm": "./.github/workflows/build-vm.yml"}

PYPI_UPLOAD_ACTION = "pypa/gh-action-pypi-publish"
DOWNLOAD_ARTIFACT_ACTION = "actions/download-artifact"
UNPUBLISHED_CHECK_TOOL = "tools/check_pypi_unpublished.py"
PUBLISH_CHECK_JOB = "check-versions-unpublished"
PYTHON_DISTS_JOB = "build-python-release-dists"
# Installs the core packages into fresh venvs (from this checkout, wheels or PyPI) and runs their
# README examples; its CORE names the packages it checks.
VERIFY_RELEASE_TOOL = "tools/verify_release_install.py"
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


def _markdown_section(heading: str) -> str:
    """The runbook text from a `## ` heading up to the next `## ` heading."""
    lines = RUNBOOK.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index(heading)
    except ValueError as exc:
        raise AssertionError(f"Runbook is missing heading: {heading}") from exc
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start + 1 : end])


def _verify_tool_core() -> frozenset[str]:
    """The packages tools/verify_release_install.py installs and checks (its literal CORE)."""
    tree = ast.parse((ROOT / VERIFY_RELEASE_TOOL).read_text(encoding="utf-8"))
    return frozenset(
        next(
            ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and [target.id for target in node.targets if isinstance(target, ast.Name)] == ["CORE"]
        )
    )


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


def _runtime_dependencies() -> dict[str, set[str]]:
    """Each workspace package (the root `doeff` too) -> the names of its runtime dependencies."""
    dependencies: dict[str, set[str]] = {}
    for pyproject in [ROOT / "pyproject.toml", *ROOT.glob("packages/*/pyproject.toml")]:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        dependencies[project["name"]] = {
            re.split(r"[<>=!~;\[ ]", requirement, maxsplit=1)[0]
            for requirement in project.get("dependencies", [])
        }
    return dependencies


def _reachable(start: str, edges: Callable[[str], set[str]]) -> set[str]:
    """Everything reachable from `start` along `edges` (`start` itself only through a cycle)."""
    found: frozenset[str] = frozenset()
    frontier = edges(start)
    while frontier:
        found = found | frontier
        frontier = {after for node in frontier for after in edges(node)} - found
    return set(found)


def _core_dependency_closure(package: str, dependencies: dict[str, set[str]]) -> set[str]:
    """The core packages `package` needs, directly or through other core packages."""
    return _reachable(package, lambda name: dependencies[name] & CORE_RELEASE_PACKAGES)


def _uploads_needed_first(package: str, dependencies: dict[str, set[str]]) -> set[str]:
    """The core uploads that must finish before `package` is uploaded."""
    if package == "doeff":
        return set(CORE_RELEASE_PACKAGES - {"doeff"})
    return {
        dependency
        for dependency in _core_dependency_closure(package, dependencies) - {package}
        if package not in _core_dependency_closure(dependency, dependencies)
    }


def _workflow_jobs(workflow: Path) -> dict[str, dict]:
    return yaml.safe_load(workflow.read_text(encoding="utf-8"))["jobs"]


def _needs(job: dict) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def _transitive_needs(jobs: dict[str, dict], name: str) -> set[str]:
    return _reachable(name, lambda job_name: _needs(jobs[job_name]))


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
        _markdown_package_table(ROOT_RELEASE_TABLE)
        | _markdown_package_table(INDEPENDENT_RELEASE_TABLE)
        | _markdown_package_table(NOT_PUBLISHED_TABLE)
    )

    assert _workspace_package_names() <= classified_packages


def test_public_readme_install_surface_has_release_owner() -> None:
    public_install_packages = _readme_install_packages()
    root_release_packages = _markdown_package_table(ROOT_RELEASE_TABLE)
    independent_release_packages = _markdown_package_table(INDEPENDENT_RELEASE_TABLE)

    assert public_install_packages <= root_release_packages | independent_release_packages


def test_root_tag_release_is_the_core_five() -> None:
    assert _markdown_package_table(ROOT_RELEASE_TABLE) == CORE_RELEASE_PACKAGES


def test_publish_workflow_builds_and_uploads_only_the_core_five() -> None:
    """A package outside the core five must not ride along on a root tag (doeff-agents did)."""
    jobs = _workflow_jobs(PUBLISH_WORKFLOW)
    workflow = PUBLISH_WORKFLOW.read_text(encoding="utf-8")
    matrix = jobs[PYTHON_DISTS_JOB]["strategy"]["matrix"]["include"]
    python_packages = CORE_RELEASE_PACKAGES - NATIVE_ROOT_RELEASE_PACKAGES

    assert "uv build --package ${{ matrix.package }}" in workflow
    assert "tools/verify_dist_metadata.py ${{ matrix.out-dir }}/*.whl" in workflow
    assert {entry["package"] for entry in matrix} == python_packages
    assert {name for name in jobs if name.startswith("publish-")} == {
        f"publish-{package}" for package in CORE_RELEASE_PACKAGES
    }
    assert {name for name, job in jobs.items() if _uploads_to_pypi(job)} == {
        f"publish-{package}" for package in CORE_RELEASE_PACKAGES
    }
    assert {name: job["uses"] for name, job in jobs.items() if "uses" in job} == {
        f"build-{package}": workflow_path
        for package, workflow_path in NATIVE_ROOT_RELEASE_WORKFLOWS.items()
    }
    for entry in matrix:
        downloads = [
            step["with"]["name"]
            for step in jobs[f"publish-{entry['package']}"]["steps"]
            if step.get("uses", "").startswith(DOWNLOAD_ARTIFACT_ACTION)
        ]
        assert downloads == [entry["artifact"]], entry["package"]


def test_publish_workflow_uploads_each_core_package_after_its_dependencies() -> None:
    """An upload must not make a release installable before the packages it needs are on PyPI.

    doeff, doeff-core-effects and doeff-hy need each other, so none of them can go before the
    others; only one-way dependencies are ordered. doeff goes last, so `pip install doeff` picks
    the new version only after the other four are up.
    """
    jobs = _workflow_jobs(PUBLISH_WORKFLOW)
    dependencies = _runtime_dependencies()
    out_of_order = {
        package: missing
        for package in sorted(CORE_RELEASE_PACKAGES)
        if (
            missing := sorted(
                dependency
                for dependency in _uploads_needed_first(package, dependencies)
                if f"publish-{dependency}" not in _transitive_needs(jobs, f"publish-{package}")
            )
        )
    }

    assert out_of_order == {}, "publish-<package> must (transitively) need these uploads"


def test_no_core_upload_starts_before_every_python_dist_is_built() -> None:
    """A failed Python build must stop the release before doeff-vm goes up alone."""
    jobs = _workflow_jobs(PUBLISH_WORKFLOW)
    early = sorted(
        f"publish-{package}"
        for package in CORE_RELEASE_PACKAGES
        if PYTHON_DISTS_JOB not in _transitive_needs(jobs, f"publish-{package}")
    )

    assert early == [], f"these uploads can start before {PYTHON_DISTS_JOB} succeeds"


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
    assert checked_packages == _markdown_package_table(ROOT_RELEASE_TABLE)

    for name, job in jobs.items():
        if name in {PUBLISH_CHECK_JOB, PYTHON_DISTS_JOB}:
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


def test_runbook_post_publish_check_reinstalls_the_core_five_from_pypi() -> None:
    """After the upload, the runbook reinstalls the release from PyPI and runs its READMEs.

    tools/verify_release_install.py --pypi <version> does that for the packages in its CORE, so
    CORE must be the same five packages the root tag publishes.
    """
    section = _markdown_section(POST_PUBLISH_SECTION)

    assert re.search(rf"{re.escape(VERIFY_RELEASE_TOOL)} --pypi \d+\.\d+\.\d+", section)
    assert _verify_tool_core() == CORE_RELEASE_PACKAGES


def test_independent_public_packages_have_documented_dist_verification() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")
    independent_release_packages = _markdown_package_table(INDEPENDENT_RELEASE_TABLE)

    assert "uv build --package <package> --wheel --sdist" in runbook
    assert "uv run python tools/verify_dist_metadata.py" in runbook
    assert "`<package>/vX.Y.Z`" in runbook
    assert independent_release_packages


PYPI_ENVIRONMENT = "pypi"
PYPI_TOKEN_PASSWORD = "${{ secrets.PYPI_API_TOKEN }}"


def _upload_steps(job: dict) -> list[dict]:
    return [
        step for step in job.get("steps", []) if step.get("uses", "").startswith(PYPI_UPLOAD_ACTION)
    ]


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
        if any(
            nested_job.get("permissions", {}).get("id-token") == "write"
            for nested_job in nested.values()
        ):
            assert job.get("permissions", {}).get("id-token") == "write", name


def test_runbook_documents_both_pypi_credentials() -> None:
    runbook = RUNBOOK.read_text(encoding="utf-8")

    assert "## PyPI の資格" in runbook
    assert "Trusted Publishing" in runbook
    assert "pending publisher" in runbook
    assert "| Workflow name | `publish.yml` |" in runbook
    assert f"| Environment name | `{PYPI_ENVIRONMENT}` |" in runbook
    assert "`PYPI_API_TOKEN`" in runbook
