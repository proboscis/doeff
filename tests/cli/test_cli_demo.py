"""`python -m doeff demo` works right after `pip install doeff` (only the 4 core packages), runs the same program under 4 handlers, and shows how the meaning and the answer of an effect change depending on how the handler treats
k (the continuation) (initiative doeff-publish).
Swapping real IO for a fake is one of the 4 (live_world); the others are a trace that inspects and passes effects on, and a budget(1) that discards k midway.

The command to try is the one the README gives to type after `pip install doeff` (the first screen is how to install, the entry to the docs, and a 30-second example;
the walkthrough is in a later section). If even one thing outside the 4 core packages (doeff, doeff-vm, doeff-core-effects,
frozendict) is imported, it fails right after `pip install doeff`. The dev environment has Hy, httpx and other parts installed,
so in a child process every import outside the standard library and the core modules is blocked, then it is run by the same route as `python -m doeff` (runpy).
The check that runs in a real fresh venv is check_demo in tools/verify_core_install.py (CI job core-only-install).

The page live_world fetches is passed as a file:// URL -- urlopen reads files by the same route as HTTP, so the check does not touch the network.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from doeff.__main__ import main
from tests.cli.cli_child import PROJECT_ROOT, with_settings

pytestmark = pytest.mark.cli

README = PROJECT_ROOT / "README.md"
CHILD_DEADLINE_SECONDS = 60
PAGE = "<h1>Hello, live</h1>"

# Code of the child process: puts at the front a finder that turns every top-level module outside those installed by the 4 core distributions (doeff-core-effects also installs doeff_lifeline) and the standard library into
# ModuleNotFoundError, then runs doeff's __main__ the same as `python -m doeff`.
# Things already loaded at startup (such as the editable finder read by the site .pth) are let through. If Hy or httpx is already loaded the block is meaningless,
# so exit 99; if httpx can still be imported after blocking, the finder is not effective, so exit 98.
RUN_DEMO_WITH_THE_CORE_ONLY = """
import runpy
import sys

CORE = {"doeff", "doeff_vm", "doeff_core_effects", "doeff_lifeline", "frozendict"}
loaded_at_start = {name.split(".")[0] for name in sys.modules}
if {"hy", "httpx", "doeff_hy"} & loaded_at_start:
    sys.exit(99)
allowed = set(sys.stdlib_module_names) | CORE | loaded_at_start


class _CoreOnly:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] not in allowed:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)


sys.meta_path.insert(0, _CoreOnly())
try:
    import httpx
except ModuleNotFoundError:
    pass
else:
    sys.exit(98)
sys.argv = ["doeff", *sys.argv[1:]]
runpy.run_module("doeff", run_name="__main__", alter_sys=True)
"""


def test_demo_runs_with_the_core_only_and_each_handler_gives_the_program_another_meaning(
    tmp_path: Path, deadline_scale_factor: float
) -> None:
    page = tmp_path / "page.html"
    page.write_text(PAGE, encoding="utf-8")
    command = with_settings(
        [sys.executable, "-c", RUN_DEMO_WITH_THE_CORE_ONLY, "demo", "--url", page.as_uri()],
        {"DOEFF_PROFILE": None},
    )

    completed = subprocess.run(
        command,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    shown = completed.stdout
    live = f"{len(PAGE)} chars"
    # 4 answers for the same digest. canned returns '14 chars' without reading the page, trace sees and passes 2 effects, budget(1) discards k at the
    # 2nd effect (Summarize) and stops the digest, live_world reads the file's page (20 chars).
    runs = (
        ">>> run(handler(canned)(digest(url)))\n'14 chars'\n",
        ">>> run(handler(canned)(handler(trace)(digest(url))))\n"
        f"trace: Fetch(url={page.as_uri()!r})\n"
        "trace: Summarize(text='<h1>Hello</h1>')\n"
        "'14 chars'\n",
        ">>> run(handler(canned)(budget(1)(digest(url))))\n'over budget at Summarize'\n",
        f">>> run(handler(canned)(handler(live_world)(digest(url))))\n{live!r}\n",
    )
    places = [shown.index(run) for run in runs]
    assert places == sorted(places)
    assert f'>>> url = "{page.as_uri()}"' in shown
    # Closing lines: one line for what was the same, and one line each for what changed per handler (what was done with k, the answer).
    closing = shown[places[-1] :]
    assert "Same in all 4 runs: digest, not one line of it changed.\n" in closing
    for handler_name, answer in (
        ("canned", "'14 chars'"),
        ("trace", "'14 chars'"),
        ("budget(1)", "'over budget at Summarize'"),
        ("live_world", repr(live)),
    ):
        assert re.search(
            rf"^  {re.escape(handler_name)} +\S.* {re.escape(answer)}$", closing, re.MULTILINE
        ), handler_name
    # Show the source of the program that was run and of the handlers (the same digest as the README's handler example).
    for source in (
        "def digest(url):",
        "def canned(effect, k):",
        "def trace(effect, k):",
        "def budget(effects):",
        "def live_world(effect, k):",
    ):
        assert source in shown


def test_demo_names_the_page_it_could_not_fetch_after_the_runs_without_the_network(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = (tmp_path / "missing.html").as_uri()

    assert main(["demo", "--url", missing]) == 1

    captured = capsys.readouterr()
    assert "'14 chars'" in captured.out
    assert "trace: Summarize(text='<h1>Hello</h1>')" in captured.out
    assert "'over budget at Summarize'" in captured.out
    assert f"live_world could not fetch {missing}" in captured.err


def test_demo_refuses_a_url_without_a_scheme_before_running(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["demo", "--url", "example.com"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "https://example.com" in captured.err


def test_readme_points_to_the_demo_as_the_command_after_pip_install() -> None:
    readme = README.read_text(encoding="utf-8")
    install = re.search(r"^```bash\n(?:pip install .*\n)+```\n", readme, re.MULTILINE)
    assert install is not None, "README has no pip install block"
    paragraphs = readme[install.end() :].split("\n\n")
    demo = next((paragraph for paragraph in paragraphs if "`python -m doeff demo`" in paragraph), None)
    assert demo is not None, "README has no `python -m doeff demo` guidance after the first pip install block"
    assert "`pip install doeff`" in demo, demo
