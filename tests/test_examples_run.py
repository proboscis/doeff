"""Checks that the example files in examples/ run to the end as scripts with the current doeff and print the results they document (initiative doeff-publish).

The examples in examples/ are reading material that users copy and run, and no test ran them, so nobody noticed when the API changed:
after `Program.of`, `ask`, `default_handlers` and `async_run` were removed, all 5 files failed at import or at the start of execution
(docs report 2026-10-17-doeff-docs-examples-run.html, "translation table for old usage").

Every `.py` directly under examples/ is covered by this test (a new example is picked up just by writing it, and turns red if EXPECTED_LINES has no lines for it).
The exit code alone is not enough: traceback_demo raises a failure and catches it, so it exits 0 even if the failure it should raise does not happen.
So for each example, EXPECTED_LINES holds the lines that must appear on stdout, and the test checks that all of them appear. If you change an example's output, fix it here too.
Examples run in the development environment (llm_multi_provider_stacking uses the components outside the core: doeff-gemini, doeff-llm, doeff-openai,
doeff-openrouter). That it runs in a fresh environment with only the 5 core packages was confirmed by writing the result in a card comment.
Examples that import components outside the core list their import names in NEEDS_OUTSIDE_CORE: in an environment without those components (the venv of the CI
Python Compatibility job that installs only the 5 core packages — .github/workflows/python-compatibility.yml) they are skipped, and in the development environment where they are installed they run
(without the skip, the example's import failed with ModuleNotFoundError and turned the whole main CI red — 2026-10-17).
Examples that take long per run go into SLOW with a reason and get the slow marker (`-m "not e2e and not slow"` in `make test` excludes them — they run if named explicitly).

A `.py` whose name starts with test_ is an example of a pytest test that verifies an example (test_llm_agent.py for llm_agent.py — initiative doeff-publish), and is not run as a script. Users copy the example files and type `pytest test_<name>.py`,
so only the `.py` files of examples/ are copied to a directory outside the repo and run with pytest, and all must pass (the repo's conftest.py and the pyproject.toml
pytest settings are not read). Example tests promise to use no LLM model key and no network, so they run with the OPENAI_ environment variables removed.

The example split into its own directory, examples/github_repo/ (a program that calls the GitHub API with the doeff[http] handler, and an example of pytest tests with a fake handler), reaches the real network if its script is run, so it is not part of the top-level example test. The 2 tests below cover it:
a test that runs, in a child process, the handler set the script installs in production (live — http_production_handler) pointed at a fake GitHub API stood up locally
(the example test does not run live, so without this nobody would notice that the production path of the example breaks when the http handler usage changes), and
a test that runs the README command `pytest examples/github_repo` in a child without pytest plugins from outside (the example dir's pytest.ini keeps rootdir at
the example dir and stops it from reading the repo root conftest — the situation of someone who cloned and installed only `pip install "doeff[http]" pytest`).

The example split into its own directory, examples/testing_without_mocks/ (all files of the introductory article "Testing Python code without mocks"), is not run here. Readers of the article run it against the PyPI release installed by `pip install doeff`, and ADR-DOE-API-002 R3 does not
rewrite this directory until a release with the value-shaped API is on PyPI, so running it against the repo version would fail every time the repo API changes. The checks of the printed output and of the dir's
`pytest` belong only to item 8 of tools/verify_core_install.py --pypi, which runs daily against the PyPI release.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.cli.cli_child import with_settings

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
# Time limit for one child, before scaling by load (seconds). One run takes about 1-3 seconds (mostly the doeff import).
CHILD_DEADLINE_SECONDS = 50

# Per-example lines that must appear on stdout (compared as whole lines). Pick the lines that print result values.
EXPECTED_LINES: dict[str, tuple[str, ...]] = {
    "llm_agent.py": (
        "question: Should a small team build its own job queue?",
        "answer: No: an existing queue already does all of it, for little cost.",
    ),
    "llm_multi_provider_stacking.py": (
        "analysis: verdict='clean' score=8",
        "summary: Gemini summary",
    ),
    "marker_demo.py": (
        "Simple interpreter result: 42",
        "Async interpreter result: 42",
        "Factory interpreter result: run by factory_interpreter",
        "Pipeline result: {'processed': {'a': 2, 'b': 5.0}, 'path': '/tmp/results.json', "
        "'log': ['Processed 2 items', 'Transformation complete', "
        "'would write 2 results to /tmp/results.json']}",
        "Chained programs: 42",
        "Executor result: run by ProgramExecutor",
    ),
    "sample_entrypoints.py": (
        "  hello_world_program -> 'Hello, World!'",
        "  fetch_user_42_program -> {'id': 42, 'name': 'User 42'}",
        "  full_pipeline_program -> {'count': 2, 'ids': [1, 2]}",
        "  formatted_result -> 'Result: 20'",
        "  strategy_pipeline -> [{'id': 1, 'processed': True}, {'id': 2, 'processed': True}, "
        "{'id': 3, 'processed': True}]",
        "  with_logging(hello_world_program) -> 'Hello, World!'",
    ),
    "traceback_demo.py": (
        "caught RuntimeError — run() printed its doeff traceback above",
        "caught MissingEnvKeyError — run() printed its doeff traceback above",
        "caught ValueError — run() printed its doeff traceback above",
    ),
}


# Examples that take long per run, and the reason.
SLOW = {
    "llm_multi_provider_stacking.py": "the openai SDK import alone takes about 25 seconds (development environment of 2026-10-17, Python 3.14t)",
}


# Examples that import components outside the core, and the import names of those components.
NEEDS_OUTSIDE_CORE = {
    "llm_multi_provider_stacking.py": ("doeff_gemini", "doeff_llm", "doeff_openai", "doeff_openrouter"),
}


def _examples() -> list[str]:
    """Examples to run as scripts (names starting with test_ are pytest test examples, so they are excluded)."""
    return sorted(path.name for path in EXAMPLES.glob("*.py") if not path.name.startswith("test_"))


def _example_tests() -> list[str]:
    """Pytest test examples that verify an example."""
    return sorted(path.name for path in EXAMPLES.glob("test_*.py"))


def _marks(name: str) -> tuple[pytest.MarkDecorator, ...]:
    # find_spec does not load the component (the openai SDK import takes about 25 seconds, so only look it up).
    absent = tuple(m for m in NEEDS_OUTSIDE_CORE.get(name, ()) if importlib.util.find_spec(m) is None)
    slow = (pytest.mark.slow,) if name in SLOW else ()
    skip = (pytest.mark.skip(reason=f"components outside the core {', '.join(absent)} are not installed in this environment"),) if absent else ()
    return slow + skip


def _params() -> list[object]:
    return [pytest.param(name, marks=_marks(name)) for name in _examples()]


@pytest.mark.parametrize("name", _params())
def test_example_runs_and_prints_its_results(name: str, deadline_scale_factor: float) -> None:
    assert name in EXPECTED_LINES, (
        f"put the lines examples/{name} must print on stdout into EXPECTED_LINES in tests/test_examples_run.py"
    )
    completed = subprocess.run(
        with_settings([sys.executable, str(EXAMPLES / name)], {"DOEFF_PROFILE": None}),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )
    assert completed.returncode == 0, (
        f"examples/{name} failed with exit code {completed.returncode}:\n{completed.stderr[-3000:]}"
    )
    printed = completed.stdout.splitlines()
    missing = [line for line in EXPECTED_LINES[name] if line not in printed]
    assert missing == [], (
        f"lines examples/{name} did not print: {missing}\nprinted:\n{completed.stdout}"
    )


@pytest.mark.parametrize("name", _example_tests())
def test_example_tests_pass_with_only_the_example_files(
    name: str, tmp_path: Path, deadline_scale_factor: float
) -> None:
    # Same form as a user typing `pytest test_<name>.py` in a dir where they copied the example files: check that the example tests pass.
    for path in EXAMPLES.glob("*.py"):
        shutil.copy(path, tmp_path / path.name)
    no_keys = {variable: None for variable in os.environ if variable.startswith("OPENAI_")}  # noqa: DOEFF004 — the one place where the test removes model keys from the child process environment
    completed = subprocess.run(
        with_settings(
            [sys.executable, "-m", "pytest", "-q", name], {"DOEFF_PROFILE": None, **no_keys}
        ),
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )
    assert completed.returncode == 0, (
        f"pytest for examples/{name} failed with exit code {completed.returncode}:\n"
        f"{completed.stdout[-3000:]}{completed.stderr[-3000:]}"
    )
    lines = completed.stdout.splitlines()
    assert lines, f"pytest for examples/{name} printed nothing:\n{completed.stderr[-3000:]}"
    assert re.match(r"\d+ passed", lines[-1]), (
        f"summary line of pytest for examples/{name} does not start with passed: {lines[-1]!r}"
    )


def test_expected_lines_name_only_existing_examples() -> None:
    assert sorted(EXPECTED_LINES) == _examples()
    assert set(SLOW) <= set(EXPECTED_LINES)
    assert set(NEEDS_OUTSIDE_CORE) <= set(EXPECTED_LINES)


GITHUB_REPO_EXAMPLE = EXAMPLES / "github_repo" / "repo_summary.py"
# Paths the local fake GitHub API answers, and the JSON bodies (404 Not Found for unknown paths).
FAKE_GITHUB_API: dict[str, object] = {
    "/repos/octo/hello": {
        "full_name": "octo/hello",
        "description": "Says hello",
        "stargazers_count": 42,
        "forks_count": 7,
        "open_issues_count": 3,
    },
    "/repos/octo/hello/releases/latest": {"tag_name": "v1.2.0"},
}


class _FakeGitHubApi(BaseHTTPRequestHandler):
    """A stand-in for the GitHub API that answers only on 127.0.0.1, returning the FAKE_GITHUB_API body as JSON."""

    def do_GET(self) -> None:
        found = self.path in FAKE_GITHUB_API
        body = FAKE_GITHUB_API[self.path] if found else {"message": "Not Found"}
        payload = json.dumps(body).encode()
        self.send_response(200 if found else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: object) -> None:  # do not write per-request access logs to the test's stderr
        return None


def test_github_repo_example_runs_its_live_handlers_against_a_local_api(
    deadline_scale_factor: float,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeGitHubApi)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    api = f"http://127.0.0.1:{server.server_port}"
    try:
        # httpx reads the proxy env, so keep 127.0.0.1 outside the proxy (go straight to the local API even on a machine behind a proxy).
        completed = subprocess.run(
            with_settings(
                [sys.executable, str(GITHUB_REPO_EXAMPLE), "octo/hello", "--api", api],
                {"DOEFF_PROFILE": None, "NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"},
            ),
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
            check=False,
        )
    finally:
        server.shutdown()
        server.server_close()
    assert completed.returncode == 0, (
        f"examples/github_repo/repo_summary.py failed with exit code {completed.returncode}:\n"
        f"{completed.stderr[-3000:]}"
    )
    assert completed.stdout.splitlines() == [
        "octo/hello: Says hello",
        "42 stars, 7 forks, 3 open issues and pull requests, latest release: v1.2.0",
    ]


def test_github_repo_example_tests_run_with_pytest_alone(deadline_scale_factor: float) -> None:
    # Run the README's `pytest examples/github_repo` without pytest plugins from outside, like someone who cloned doeff and installed only `pip install "doeff[http]" pytest`.
    # The example dir's pytest.ini keeps rootdir at the example dir, so the repo root
    # conftest.py (which needs development tools such as pytest-timeout for deadlines) is not read — without pytest.ini, the root conftest
    # stopped with an INTERNALERROR `no option named 'timeout'` (2026-10-17; the same in an environment with only PyPI 0.6.0 and pytest).
    pytest_alone = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    completed = subprocess.run(
        with_settings(
            [*pytest_alone, "examples/github_repo"],
            {"DOEFF_PROFILE": None, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTEST_ADDOPTS": None},
        ),
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )
    assert completed.returncode == 0, (
        f"pytest examples/github_repo failed with exit code {completed.returncode}:\n"
        f"{completed.stdout[-3000:]}\n{completed.stderr[-3000:]}"
    )
    summary = completed.stdout.splitlines()[-1]  # last line of -q: `4 passed in …s`
    assert summary.startswith("4 passed"), completed.stdout

