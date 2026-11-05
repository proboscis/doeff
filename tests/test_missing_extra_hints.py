"""Failure case: when a Hy module or HTTP handler is loaded in an environment without Hy or httpx, a sentence naming the extra to add is shown (section 8 of
initiative doeff-publish).

`pip install doeff` installs only the 4 core packages; the Hy language (doeff-hy) is added with extra hy, and the HTTP client handler (written in Hy, sending with httpx)
is added with extra http (tests/test_core_install_extras.py). So that a user who has not added them does not stop at only `No module named 'doeff_hy'`,
the sentence ends with a line saying how to install, such as `pip install "doeff[hy]"`. The sentences are held by a single table in doeff_core_effects/_extra_hints.py.

In a child process, a finder that returns ModuleNotFoundError for the blocked names' imports is placed first before the import is run. The CLI's doeff run
scenario is tests/cli/test_cli_without_hy.py.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
CHILD_DEADLINE_SECONDS = 60
NO_HY = ("hy", "hyrule", "doeff_hy", "doeff_hy_pytest")
NO_HTTPX = ("httpx",)

# Place first a finder that returns ModuleNotFoundError for imports of the names to block (comma-separated in argv[1]), run the statement in argv[2], and output the
# ModuleNotFoundError's name and sentence as JSON. If the name was already loaded before blocking it is not really blocked, so exit with 99; if nothing came out, exit with 98.
BLOCK_THEN_IMPORT = """
import json
import sys

blocked = tuple(sys.argv[1].split(","))
if any(name.split(".")[0] in blocked for name in sys.modules):
    sys.exit(99)


class _Blocked:
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in blocked:
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)


sys.meta_path.insert(0, _Blocked())

try:
    exec(sys.argv[2])
except ModuleNotFoundError as error:
    print(json.dumps({"name": error.name, "message": str(error)}))
    sys.exit(0)
sys.exit(98)
"""


@pytest.mark.parametrize(
    ("blocked", "statement", "extra"),
    [
        (NO_HY, "import doeff_core_effects._os_process", "hy"),
        (NO_HY, "from doeff_core_effects._cache import cache", "hy"),
        (NO_HY, "from doeff_core_effects import http_production_handler", "http"),
        (NO_HY, "from doeff_core_effects._http_handlers import http_fixture_handler", "http"),
        (NO_HY, "from doeff_core_effects import HttpRequest", "http"),
        (NO_HTTPX, "from doeff_core_effects import http_production_handler", "http"),
        (NO_HTTPX, "from doeff_core_effects._http_handlers import http_fixture_handler", "http"),
    ],
    ids=[
        "hy-module",
        "cache",
        "http-handler-without-hy",
        "http-handlers-module-without-hy",
        "http-effect-without-hy",
        "http-handler-without-httpx",
        "http-handlers-module-without-httpx",
    ],
)
def test_a_missing_extra_names_the_extra_to_install(
    deadline_scale_factor: float, blocked: tuple[str, ...], statement: str, extra: str
) -> None:
    completed = subprocess.run(
        [sys.executable, "-c", BLOCK_THEN_IMPORT, ",".join(blocked), statement],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )

    assert completed.returncode == 0, (completed.returncode, completed.stderr)
    error = json.loads(completed.stdout)
    # The name stays that of the blocked module (do not break callers that read `except ModuleNotFoundError as e: if e.name == "httpx"`).
    assert error["name"].split(".")[0] in blocked
    assert f"No module named {error['name']!r}" in error["message"]
    assert error["message"].endswith(f'pip install "doeff[{extra}]"'), error["message"]


def test_import_doeff_still_needs_neither_hy_nor_httpx(deadline_scale_factor: float) -> None:
    # The mechanism that prints the install line must not start loading Hy or httpx in the core import.
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            BLOCK_THEN_IMPORT,
            ",".join(NO_HY + NO_HTTPX),
            "import doeff, doeff_core_effects, doeff_core_effects._extra_hints",
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=CHILD_DEADLINE_SECONDS * deadline_scale_factor,
        check=False,
    )

    assert completed.returncode == 98, (completed.stdout, completed.stderr)
