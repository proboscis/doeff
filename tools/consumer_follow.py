"""Run the consumer probe (a repo that reads doeff) in the measured doeff tree's environment and turn reds into report lines (a later stage of the daily full verification).

Rule: a change that removes or moves a public doeff name can break consumers even when doeff's own verification is green.
A consumer's verification runs on the doeff version the consumer pinned, so a break caused by a change on doeff's mainline stays invisible until the consumer bumps its version. This tool
runs the consumer probe (PROBE -- a check for whether the doeff_adr names imported by the .hy files in the consumer's docs/adr exist) with the python that ran this tool (the measured doeff tree's
environment), and emits each red check as one line

    consumer_follow: 赤 <consumer> <first 12 chars of consumer commit> <check id> — <first line of the reason>

to standard output. The daily task collects them as a later stage report (AfterReport of agora-controllers) and puts them on a red card.

Consumers are derived, not listed in a table: among the places to search (home dotfiles and each repo under home repos), repos whose root or one-level-down pyproject.toml depends on a doeff distribution
(doeff, doeff-*). Only consumers that have the probe are run. The consumer's conftest reads the consumer's environment dependencies, so it is not read (--noconftest) --
a check that requires a fixture from the consumer's conftest counts as an "unmeasured check" and is not red. Finally, as a marker that the later stage ran to the end, `consumer_follow: 数え …` is
emitted (so that a run with 0 report lines is read as "fixed"). A run with no consumer that has the probe turns the disappearance of the measurement into a red line (it is not read as green).
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tomllib

REPORT_PREFIX = "consumer_follow: 赤 "  # wire format: read by the later-stage collector, keep as is
COUNT_PREFIX = "consumer_follow: 数え "  # wire format, keep as is
PROBE = Path("docs/adr/test_doeff_adr_api_follows.py")
# Upper limit for one probe run. Measured: one probe of dotfiles in a cold environment took 172 seconds (2026-10-30) -- about 7 times that.
PROBE_TIMEOUT_S = 1200
_MESSAGE_MAX = 400
_REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_MISSING_FIXTURE_RE = re.compile(r"fixture '[^']+' not found")
CaseKind = Literal["red", "measured", "unmeasured"]


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of one consumer's probe (red = list of (id, reason) of red checks; measured = number of measured checks; unmeasured = number of unmeasured checks)."""

    red: tuple[tuple[str, str], ...]
    measured: int
    unmeasured: int


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_names(pyproject: Path) -> set[str]:
    """Set of distribution names in the dependencies of pyproject.toml (project dependencies, optional-dependencies, and dependency-groups)."""
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return set()
    project = data.get("project", {})
    groups = (project.get("dependencies", []), *project.get("optional-dependencies", {}).values(),
              *data.get("dependency-groups", {}).values())  # fmt: skip
    found = (
        _REQUIREMENT_NAME_RE.match(spec)
        for group in groups
        for spec in group
        if isinstance(spec, str)
    )
    return {_normalized(match.group(1)) for match in found if match}


def _project_name(pyproject: Path) -> str:
    try:
        return _normalized(
            str(
                tomllib.loads(pyproject.read_text(encoding="utf-8"))
                .get("project", {})
                .get("name", "")
            )
        )
    except (OSError, tomllib.TOMLDecodeError):
        return ""


def depends_on_doeff(repo: Path) -> bool:
    """Whether the root or one-level-down pyproject.toml of the repo depends on a doeff distribution. doeff itself (root project named doeff) is not a consumer."""
    if _project_name(repo / "pyproject.toml") == "doeff":
        return False
    return any(
        name == "doeff" or name.startswith("doeff-")
        for pyproject in (repo / "pyproject.toml", *sorted(repo.glob("*/pyproject.toml")))
        for name in _requirement_names(pyproject)
    )


def candidate_roots(home: Path) -> list[Path]:
    """Where to look for consumers: home dotfiles and each repo under home repos (git checkouts only; the same entity once)."""
    repos = home / "repos"
    found = [home / "dotfiles", *(sorted(repos.iterdir()) if repos.is_dir() else [])]
    checkouts = [root for root in found if (root / ".git").exists()]
    return [
        root
        for i, root in enumerate(checkouts)
        if root.resolve() not in {r.resolve() for r in checkouts[:i]}
    ]


def consumers(roots: list[Path]) -> list[Path]:
    return [root for root in roots if depends_on_doeff(root)]


def _case_outcome(case: ET.Element) -> tuple[CaseKind, str, str]:
    """Turn one junit testcase into (kind, check name, reason). A missing-fixture error is a check that requires a fixture from the consumer's conftest --
    it can only be measured in the consumer's environment, so it is "unmeasured"."""
    name = case.get("name", "")
    failure = case.find("failure")
    found = failure if failure is not None else case.find("error")
    if found is None:
        return ("unmeasured" if case.find("skipped") is not None else "measured", name, "")
    message = found.get("message", "")
    text = found.text or ""
    if failure is None and _MISSING_FIXTURE_RE.search(message + "\n" + text):
        return ("unmeasured", name, "")
    # The message of a collection failure is just "collection failure", so take the reason from the last exception line of the body (pytest's `E   ` line)
    raised = [line.removeprefix("E").strip() for line in text.splitlines() if line.startswith("E ")]
    return (
        "red",
        name,
        _first_line(raised[-1] if message == "collection failure" and raised else message or text),
    )


def classify(junit_xml: str) -> ProbeResult:
    """From pytest's junit XML, read the red checks (failures, and errors that are not missing fixtures) and the numbers of measured and unmeasured checks."""
    outcomes = [_case_outcome(case) for case in ET.fromstring(junit_xml).iter("testcase")]
    return ProbeResult(
        red=tuple((name, why) for kind, name, why in outcomes if kind == "red"),
        measured=sum(1 for kind, _name, _why in outcomes if kind != "unmeasured"),
        unmeasured=sum(1 for kind, _name, _why in outcomes if kind == "unmeasured"),
    )


def _first_line(text: str) -> str:
    line = next((line.strip() for line in text.splitlines() if line.strip()), "(no reason)")
    return line if len(line) <= _MESSAGE_MAX else line[: _MESSAGE_MAX - 1] + "…"


def run_probe(
    consumer: Path, python: str = sys.executable, timeout_s: float = PROBE_TIMEOUT_S
) -> ProbeResult:
    """Run the consumer's probe once in the python environment and read the outcome. If it does not run, does not finish, or has no checks at all, turn that into one red item."""
    probe = consumer / PROBE
    with tempfile.TemporaryDirectory(prefix="consumer-follow-") as tmp:
        junit = Path(tmp) / "probe.xml"
        command = [
            python, "-m", "pytest", str(probe), "-q", "--noconftest", "-p", "no:cacheprovider",
            "-o", "doeff_adr_wiring=off", "--rootdir", str(probe.parent), f"--junitxml={junit}",
        ]  # fmt: skip
        try:
            out = subprocess.run(
                command,
                cwd=probe.parent,
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return ProbeResult(
                red=(("(probe)", f"the probe did not finish in {int(timeout_s)} seconds"),),
                measured=0,
                unmeasured=0,
            )
        if not junit.exists():
            # The reason it did not run is at the end of the output, so take the first non-empty line from the end
            tail = _first_line("\n".join(reversed((out.stdout + out.stderr).splitlines())))
            return ProbeResult(
                red=(("(probe)", f"the probe did not run (pytest exit {out.returncode}) -- {tail}"),),
                measured=0,
                unmeasured=0,
            )
        result = classify(junit.read_text(encoding="utf-8"))
    if not result.red and result.measured == 0:
        return ProbeResult(
            red=(("(probe)", "the probe measured no checks at all"),), measured=0, unmeasured=result.unmeasured
        )
    return result


def _head(repo: Path) -> str:
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=False
    ).stdout
    return out.strip()[:12] or "(unknown)"


def report_lines(home: Path, python: str = sys.executable) -> list[str]:
    """To build the red report line per consumer and the final count line."""
    roots = candidate_roots(home)
    found = consumers(roots)
    probed = [consumer for consumer in found if (consumer / PROBE).is_file()]
    results = [(consumer, _head(consumer), run_probe(consumer, python)) for consumer in probed]
    red = [
        f"{REPORT_PREFIX}{consumer.name} {head} {PROBE.as_posix()}::{name} — {why}"
        for consumer, head, result in results
        for name, why in result.red
    ]
    places = "・".join(str(root) for root in roots) or "(no places to search)"
    absent = (
        []
        if probed
        else [
            f"{REPORT_PREFIX}(no consumer) - {PROBE.as_posix()} — no consumer with a probe was found (searched = {places})"
        ]
    )
    lines = [*red, *absent]
    count = (
        f"{COUNT_PREFIX}赤 {len(lines)} lines ({len(found)} consumers, {len(probed)} with a probe, "
        f"{sum(r.measured for _c, _h, r in results)} checks measured, {sum(r.unmeasured for _c, _h, r in results)} unmeasured)"
    )
    return [*lines, count]


def main() -> int:
    """To emit the report lines and the count line that marks that the later stage ran to the end. The later stage does not change the outcome, so it always exits 0."""
    for line in report_lines(Path.home()):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
