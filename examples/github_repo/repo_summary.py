"""Summarize a GitHub repository: real HTTP when you run it, a fake handler in its tests.

``repo_summary`` asks for what it needs by yielding ``HttpRequest`` effects and never opens a
connection itself. Who answers is chosen where the program runs:

- run this file, and ``live`` installs the HTTP client handler from ``doeff[http]``: the
  requests go to the GitHub API;
- run ``test_repo_summary.py``, and a fake handler answers the same requests from a dict:
  no network, nothing patched, and ``repo_summary`` is the same code.

    pip install "doeff[http]" pytest
    python examples/github_repo/repo_summary.py proboscis/doeff   # live: calls api.github.com
    pytest examples/github_repo                                   # tests: no network
"""

import argparse
import json
from dataclasses import dataclass

from doeff_core_effects import HttpRequest, await_handler, http_production_handler, scheduled

from doeff import do, run

GITHUB_API = "https://api.github.com"


@dataclass(frozen=True)
class RepoSummary:
    name: str
    description: str
    stars: int
    forks: int
    open_issues: int  # GitHub counts open pull requests here too
    latest_release: str | None  # None when the repository has no release

    def __str__(self) -> str:
        return (
            f"{self.name}: {self.description}\n"
            f"{self.stars} stars, {self.forks} forks, "
            f"{self.open_issues} open issues and pull requests, "
            f"latest release: {self.latest_release or 'none yet'}"
        )


class RepoNotFoundError(LookupError):
    """The API has no public repository by this name."""


@do
def get_json(url):
    """GET a GitHub API URL and decode its JSON body; None when the API answers 404."""
    response = yield HttpRequest("GET", url, headers={"Accept": "application/vnd.github+json"})
    if response.status == 404:
        return None
    response.raise_for_status()  # any other 4xx or 5xx raises HttpError
    return json.loads(response.text)


@do
def repo_summary(name, api=GITHUB_API):
    """Read a repository and its latest release, and summarize them. No I/O in here."""
    repo = yield get_json(f"{api}/repos/{name}")
    if repo is None:
        raise RepoNotFoundError(name)
    release = yield get_json(f"{api}/repos/{name}/releases/latest")
    return RepoSummary(
        name=repo["full_name"],
        description=repo["description"] or "(no description)",
        stars=repo["stargazers_count"],
        forks=repo["forks_count"],
        open_issues=repo["open_issues_count"],
        latest_release=None if release is None else release["tag_name"],
    )


def live(program):
    """Install the production handlers: each HttpRequest goes over real HTTP (httpx).

    The HTTP handler awaits httpx's async client, so ``await_handler`` and the scheduler sit
    outside it.
    """
    return scheduled(await_handler(http_production_handler()(program)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize a GitHub repository.")
    parser.add_argument("name", nargs="?", default="proboscis/doeff", help="owner/repo")
    parser.add_argument(
        "--api",
        default=GITHUB_API,
        help="the API root (for GitHub Enterprise: https://HOST/api/v3)",
    )
    args = parser.parse_args()
    print(run(live(repo_summary(args.name, args.api))))


if __name__ == "__main__":
    main()
