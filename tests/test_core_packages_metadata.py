"""The PyPI metadata of the five core packages (doeff, doeff-vm, doeff-core-effects, doeff-hy, doeff-hy-pytest),
and of doeff-time which the README installs from PyPI, reads on the same axis as the README: algebraic effects
(docs/positioning/what-doeff-is.md).

A PyPI page shows `description` as its one-line summary, searches by `keywords`, and lists `project.urls` as its
links. Before this check the summary was the old "Pragmatic do-notation and effects system for Python", the
keywords were monad and free-monad, and the only link was Homepage (card ki-9e64d26d0140).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import tomllib

REPO = Path(__file__).resolve().parents[1]

# Core package name -> the directory of its pyproject.toml.
CORE = {
    "doeff": REPO,
    "doeff-vm": REPO / "packages" / "doeff-vm",
    "doeff-core-effects": REPO / "packages" / "doeff-core-effects",
    "doeff-hy": REPO / "packages" / "doeff-hy",
    "doeff-hy-pytest": REPO / "packages" / "doeff-hy-pytest",
}

# Packages released on their own tag whose PyPI page the README sends readers to (`pip install doeff-time`).
# doeff-time 0.1.0 went to PyPI with the summary "Provider-agnostic time effects for doeff", no keywords, no
# links and no classifiers; its next release carries the same metadata as the core (card ki-bdc3c0fec9b0).
ON_PYPI_ALONE = {
    "doeff-time": REPO / "packages" / "doeff-time",
}

PACKAGES = CORE | ON_PYPI_ALONE

# The axis every summary starts with — the opening of the README's first line.
AXIS = "Algebraic effects for Python: "

# Words of the framing doeff left behind (do-notation over a free monad).
RETIRED_KEYWORDS = re.compile(r"monad|do-notation", re.IGNORECASE)
AXIS_KEYWORDS = {"algebraic-effects", "effects", "handlers", "continuations"}
REQUIRED_URLS = {"Documentation", "Changelog", "Issues"}
REQUIRED_CLASSIFIER_PREFIXES = ("Development Status :: ", "Intended Audience :: ", "Topic :: ")


def _project(name: str) -> dict[str, object]:
    path = PACKAGES[name] / "pyproject.toml"
    project = tomllib.loads(path.read_text(encoding="utf-8")).get("project")
    assert isinstance(project, dict), f"{path} has no [project] table"
    assert project.get("name") == name
    return project


def _strings(project: dict[str, object], key: str) -> list[str]:
    value = project.get(key, [])
    assert isinstance(value, list), f"{project.get('name')}: {key} is not a list: {value!r}"
    assert all(isinstance(item, str) for item in value), (
        f"{project.get('name')}: {key} is not a list of strings: {value!r}"
    )
    return [str(item) for item in value]


def test_the_readme_first_line_states_the_axis() -> None:
    lines = [
        line
        for line in (REPO / "README.md").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    # lines[0] is the "# doeff" heading; the next line is the one-sentence statement in bold.
    assert lines[1].strip("*").startswith(AXIS), lines[1]


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_description_is_one_line_on_the_axis(name: str) -> None:
    description = _project(name).get("description")
    assert isinstance(description, str)
    assert "\n" not in description
    assert description.startswith(AXIS), (
        f"{name}: description does not start with {AXIS!r}: {description!r}"
    )


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_keywords_name_the_axis_and_no_monad_words(name: str) -> None:
    keywords = _strings(_project(name), "keywords")
    assert set(keywords) >= AXIS_KEYWORDS, (
        f"{name}: keywords miss {sorted(AXIS_KEYWORDS - set(keywords))}"
    )
    retired = [keyword for keyword in keywords if RETIRED_KEYWORDS.search(keyword)]
    assert not retired, f"{name}: keywords keep the retired framing: {retired}"


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_urls_link_documentation_changelog_and_issues(name: str) -> None:
    urls = _project(name).get("urls")
    assert isinstance(urls, dict)
    assert urls.keys() >= REQUIRED_URLS, (
        f"{name}: project.urls miss {sorted(REQUIRED_URLS - urls.keys())}"
    )
    for label in sorted(REQUIRED_URLS):
        assert str(urls[label]).startswith("https://github.com/proboscis/doeff/"), (
            label,
            urls[label],
        )
    # The links point at files of this repository, so they must exist here.
    for label, path in (("Documentation", "docs/index.md"), ("Changelog", "CHANGELOG.md")):
        assert str(urls[label]).endswith(f"/blob/main/{path}"), (label, urls[label])
        assert (REPO / path).is_file(), path


@pytest.mark.parametrize("name", sorted(PACKAGES))
def test_classifiers_give_status_audience_and_topic(name: str) -> None:
    classifiers = _strings(_project(name), "classifiers")
    for prefix in REQUIRED_CLASSIFIER_PREFIXES:
        assert any(c.startswith(prefix) for c in classifiers), f"{name}: no {prefix!r} classifier"
    trove = pytest.importorskip("trove_classifiers")
    unknown = [c for c in classifiers if c not in trove.classifiers]
    assert not unknown, f"{name}: classifiers PyPI does not know: {unknown}"
