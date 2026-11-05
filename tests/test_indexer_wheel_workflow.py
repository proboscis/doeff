"""Test that the CI that builds doeff-indexer wheels (build-indexer.yml) does not upload a wheel with a wrong layout (a defect found in the doeff-publish effort).


The PyPI 0.1.3 wheel held the command binary at `python/doeff_indexer/bin/`, not at `doeff_indexer/bin/` where the command entrypoint looks.
maturin 1.12 puts the `include` files of pyproject.toml in at their path from the project root, and from 1.13 puts them under python-source.
0.1.3 was built in a CI where maturin-action pulled that day's latest 1.12, but build-indexer.yml did not pin the version
(for the history and tools, see tests/test_indexer_wheel_layout.py and tools/verify_indexer_wheel.py).

This test reads two workflow-side defenses:

1. Every maturin-action step pins maturin-version to 1.13 or later (the layout does not change with that day's "latest").
2. After all wheel builds, a job runs that checks the layout with tools/verify_indexer_wheel.py, installs the Linux x86_64 wheel, and fires the command,
   and the upload job waits for that job (a wrong wheel stops before upload).
"""

from __future__ import annotations

from pathlib import Path

import yaml

from tests.test_indexer_wheel_layout import MATURIN_FLOOR, _versions

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "build-indexer.yml"
MATURIN_ACTION = "PyO3/maturin-action"
TOOL_PATH = "tools/verify_indexer_wheel.py"


def _jobs() -> dict[str, dict]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _steps_using(job: dict, action: str) -> list[dict]:
    return [step for step in job.get("steps", []) if str(step.get("uses", "")).startswith(action)]


def _needs(job: dict) -> set[str]:
    needs = job.get("needs", [])
    return {needs} if isinstance(needs, str) else set(needs)


def test_every_maturin_action_step_pins_maturin_from_the_layout_fix() -> None:
    build_steps = [
        (name, step) for name, job in _jobs().items() for step in _steps_using(job, MATURIN_ACTION)
    ]
    assert build_steps, "build-indexer.yml has no maturin-action step"
    unpinned = [name for name, step in build_steps if "maturin-version" not in step.get("with", {})]
    assert unpinned == [], f"jobs with no maturin-version pin: {unpinned}"
    too_old = [
        name
        for name, step in build_steps
        if _versions(step["with"]["maturin-version"]) < MATURIN_FLOOR
    ]
    assert too_old == [], f"jobs pinning a version older than maturin 1.13: {too_old}"


def test_wheels_are_checked_and_run_before_the_upload() -> None:
    jobs = _jobs()
    # Jobs that build wheels (the sdist job builds a tar.gz with the same action -- not one to check the layout of).
    builders = {
        name
        for name, job in jobs.items()
        if any(
            step.get("with", {}).get("command") != "sdist"
            for step in _steps_using(job, MATURIN_ACTION)
        )
    }
    verify_jobs = {
        name
        for name, job in jobs.items()
        if any(TOOL_PATH in step.get("run", "") for step in job.get("steps", []))
    }
    assert len(verify_jobs) == 1, f"the number of jobs checking the layout is not 1: {sorted(verify_jobs)}"
    (verify,) = verify_jobs
    assert builders <= _needs(jobs[verify]), "does not run after all builds"
    command = next(
        step["run"] for step in jobs[verify]["steps"] if TOOL_PATH in step.get("run", "")
    )
    # The wheel that is installed and fired is the Linux x86_64 wheel that works on this job's runner (ubuntu-latest, x86_64).
    assert "--smoke" in command
    assert "manylinux" in command
    assert "x86_64" in command
    assert verify in _needs(jobs["publish"]), "upload does not wait for the layout check"


def test_the_upload_waits_for_a_check_that_all_fifteen_wheels_exist() -> None:
    # The first manual run of 0.2.0 built only 10 wheels, yet every job and the layout check passed. Unless the job before publishing holds the list of wheels to build (--expect-all),
    # the same shortfall silently passes in the next publication too.
    jobs = _jobs()
    command = next(
        step["run"]
        for job in jobs.values()
        for step in job.get("steps", [])
        if TOOL_PATH in step.get("run", "")
    )
    assert "--expect-all" in command


def test_a_job_that_names_python_3_13t_pins_a_maturin_that_accepts_it() -> None:
    # maturin 1.14 and later reject `-i python3.13t` with "free-threaded is from 3.14" (1.13.0 and 1.13.1 accept it).
    # A job pinning a rejecting version fails because it cannot build the wheel for 3.13, or silently comes up short.
    names_313t = [
        (name, step)
        for name, job in _jobs().items()
        for step in _steps_using(job, MATURIN_ACTION)
        if "python3.13t" in str(step.get("with", {}).get("args", ""))
    ]
    assert names_313t, "no job names python3.13t (the wheel for 3.13 is not built)"
    too_new = [
        name for name, step in names_313t if _versions(step["with"]["maturin-version"]) >= (1, 14, 0)
    ]
    assert too_new == [], f"jobs pinning maturin 1.14 or later name python3.13t: {too_new}"
