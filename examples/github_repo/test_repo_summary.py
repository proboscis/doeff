"""Tests for repo_summary.py that never touch the network.

Each test runs the same ``repo_summary`` as the live script, under a fake handler in place of
the HTTP client handler. The fake answers each ``HttpRequest`` from a dict of canned GitHub
API answers; a request for a URL the dict does not have fails the test.

    pip install "doeff[http]" pytest
    pytest examples/github_repo
"""

import json

import pytest
from doeff_core_effects import HttpError, HttpRequest, HttpResponse
from repo_summary import GITHUB_API, RepoNotFoundError, RepoSummary, repo_summary

from doeff import Resume, handler, run

REPO_URL = f"{GITHUB_API}/repos/octo/hello"
RELEASE_URL = f"{REPO_URL}/releases/latest"
HELLO = {
    "full_name": "octo/hello",
    "description": "Says hello",
    "stargazers_count": 42,
    "forks_count": 7,
    "open_issues_count": 3,
}
NOT_FOUND = (404, {"message": "Not Found"})


def fake_github(answers):
    """A handler that answers each HttpRequest from ``answers`` ({url: (status, JSON body)}).
    It never opens a connection."""

    @handler
    def answer(effect: HttpRequest, k):  # the annotation picks the effects it answers
        status, body = answers[effect.url]  # a URL the test did not expect: KeyError
        text = json.dumps(body)
        return (yield Resume(k, HttpResponse(status, {}, text.encode(), text, effect.url, 0.0)))

    return handler(answer)


def test_summarizes_the_repository_and_its_latest_release():
    answers = {REPO_URL: (200, HELLO), RELEASE_URL: (200, {"tag_name": "v1.2.0"})}

    summary = run(fake_github(answers)(repo_summary("octo/hello")))

    assert summary == RepoSummary("octo/hello", "Says hello", 42, 7, 3, "v1.2.0")


def test_a_repository_without_a_release():
    answers = {REPO_URL: (200, HELLO), RELEASE_URL: NOT_FOUND}

    summary = run(fake_github(answers)(repo_summary("octo/hello")))

    assert summary.latest_release is None
    assert str(summary).endswith("latest release: none yet")


def test_an_unknown_repository_is_not_asked_for_its_release():
    # Only the repository URL has an answer: asking for the release would raise KeyError.
    with pytest.raises(RepoNotFoundError):
        run(fake_github({REPO_URL: NOT_FOUND})(repo_summary("octo/hello")))


def test_a_rate_limited_answer_raises_http_error():
    answers = {REPO_URL: (403, {"message": "API rate limit exceeded"})}

    with pytest.raises(HttpError) as raised:
        run(fake_github(answers)(repo_summary("octo/hello")))

    assert raised.value.status == 403
