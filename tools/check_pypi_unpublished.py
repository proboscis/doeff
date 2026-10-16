"""Fail a release before its first upload when a package's version is already on PyPI.

The publish workflows upload with ``skip-existing: true`` (so "Re-run failed jobs" can resume an
upload that stopped midway). With it, a tag pushed without a version bump uploads nothing and still
reports success. This check reads each package's static ``[project]`` name and version from its
``pyproject.toml`` and asks the PyPI JSON API (``/pypi/<name>/<version>/json`` — PyPI normalizes
both the name and the version) whether that release exists: 404 means it can be uploaded, 200 means
the version must be bumped first, and any other answer fails too, because an unconfirmed release
must not go out.

Standard library only, so CI runs it without syncing the workspace:

    uv run --no-project python tools/check_pypi_unpublished.py packages/doeff-vm . ...
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import assert_never

import tomllib

PYPI_JSON_API = "https://pypi.org/pypi"
TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Release:
    """The release one package dir would upload."""

    package_dir: Path
    name: str
    version: str

    def __str__(self) -> str:
        return f"{self.name} {self.version}"


@dataclass(frozen=True)
class NotStatic:
    """The dir's pyproject.toml does not name a static version, so there is nothing to look up."""

    package_dir: Path
    reason: str


@dataclass(frozen=True)
class Unpublished:
    """PyPI has no such release (404): the only verdict that lets the release go on."""

    release: Release


@dataclass(frozen=True)
class Published:
    """PyPI already has this version: the release would upload nothing, so it must stop."""

    release: Release
    url: str


@dataclass(frozen=True)
class Unanswered:
    """PyPI gave neither 200 nor 404 (error status, network failure): the release must stop."""

    release: Release
    url: str
    reason: str


Verdict = NotStatic | Unpublished | Published | Unanswered


def _read_release(package_dir: Path) -> Release | NotStatic:
    """Find which name and version this dir would upload, from the same pyproject the build reads."""
    pyproject = package_dir / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return NotStatic(package_dir, f"cannot read {pyproject}: {exc}")
    name, version = project.get("name"), project.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        return NotStatic(
            package_dir,
            f"{pyproject} has no static [project] name and version (dynamic versions are not "
            "checked; declare the version in pyproject.toml)",
        )
    return Release(package_dir, name, version)


def _ask_pypi(index_url: str, release: Release) -> Verdict:
    """The one network boundary: 200 = published, 404 = free, anything else = unanswered."""
    url = f"{index_url.rstrip('/')}/{release.name}/{release.version}/json"
    request = urllib.request.Request(url, headers={"User-Agent": "doeff-release-check"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS):
            return Published(release, url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return Unpublished(release)
        return Unanswered(release, url, f"HTTP {exc.code}")
    except (urllib.error.URLError, TimeoutError) as exc:
        return Unanswered(release, url, str(exc))


def _verdict(index_url: str, package_dir: Path) -> Verdict:
    """Decide whether one package dir may be released."""
    match _read_release(package_dir):
        case Release() as release:
            return _ask_pypi(index_url, release)
        case NotStatic() as not_static:
            return not_static


def _line(verdict: Verdict) -> str:
    """Say in the CI log what the release needs from a person for this package."""
    match verdict:
        case Unpublished(release):
            return f"[OK] {release} is not on PyPI yet"
        case Published(release, url):
            return (
                f"[FAIL] {release} is already on PyPI ({url}) — bump the version before tagging; "
                "the upload would skip every file and still report success"
            )
        case Unanswered(release, url, reason):
            return (
                f"[FAIL] {release}: PyPI did not confirm the version is free ({reason} from {url})"
            )
        case NotStatic(package_dir, reason):
            return f"[FAIL] {package_dir}: {reason}"
        case _:
            assert_never(verdict)


def main(argv: list[str]) -> int:
    """Report every package (not only the first failure) and exit 1 when any may not be released."""
    parser = argparse.ArgumentParser(
        description="Fail when a package's pyproject.toml version is already published on PyPI.",
    )
    parser.add_argument(
        "--index-url", default=PYPI_JSON_API, help=f"PyPI JSON API root (default {PYPI_JSON_API})"
    )
    parser.add_argument("package_dirs", nargs="+", type=Path, help="dirs holding pyproject.toml")
    args = parser.parse_args(argv)

    verdicts = tuple(_verdict(args.index_url, package_dir) for package_dir in args.package_dirs)
    for verdict in verdicts:
        print(_line(verdict))
    failed = sum(not isinstance(verdict, Unpublished) for verdict in verdicts)
    if failed:
        print(
            f"{failed} of {len(verdicts)} packages cannot be released; fix them before uploading."
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
