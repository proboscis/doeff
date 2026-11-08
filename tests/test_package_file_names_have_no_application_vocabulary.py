"""Names of files in the generic packages contain none of the application-domain words listed above.

File contents are blocked by the semgrep rule doeff-packages-have-no-application-vocabulary in the commit hook.
Semgrep only looks at contents, so this test looks at names only. The word list, allowed spellings, and target packages are read from the rule
and not copied here (the single source of definition is one rule in .semgrep.yaml).
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RULE_ID = "doeff-packages-have-no-application-vocabulary"
PACKAGE_INCLUDE = re.compile(r"^/(packages/[^/*]+)/\*\*$")


@dataclass(frozen=True)
class VocabularyRule:
    """Three things read from the rule: the application words, the allowed spellings, and the target packages (dirs from the repo root)."""

    forbidden: re.Pattern[str]
    allowed: re.Pattern[str]
    packages: tuple[str, ...]


def _mapping(value: object, where: str) -> dict[object, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{where} is not a mapping: {value!r}")
    return value


def _sequence(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise AssertionError(f"{where} is not a list: {value!r}")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise AssertionError(f"{where} is not a string: {value!r}")
    return value


def _clause(patterns: list[object], key: str) -> re.Pattern[str]:
    for clause in patterns:
        found = _mapping(clause, f"item of patterns of {RULE_ID}").get(key)
        if found is not None:
            return re.compile(_text(found, f"{key} of {RULE_ID}"))
    raise AssertionError(f"rule {RULE_ID} has no {key}")


def read_rule() -> VocabularyRule:
    document = _mapping(yaml.safe_load((REPO_ROOT / ".semgrep.yaml").read_text(encoding="utf-8")), ".semgrep.yaml")
    for item in _sequence(document.get("rules"), "rules of .semgrep.yaml"):
        rule = _mapping(item, "a rule of .semgrep.yaml")
        if rule.get("id") != RULE_ID:
            continue
        patterns = _sequence(rule.get("patterns"), f"patterns of {RULE_ID}")
        include = _sequence(_mapping(rule.get("paths"), f"paths of {RULE_ID}").get("include"), f"include of {RULE_ID}")
        # Only the include entries that name a repo package (excluding entries for fixture copies).
        packages = tuple(
            found.group(1)
            for entry in include
            if (found := PACKAGE_INCLUDE.match(_text(entry, f"include entry of {RULE_ID}")))
        )
        return VocabularyRule(_clause(patterns, "pattern-regex"), _clause(patterns, "pattern-not-regex"), packages)
    raise AssertionError(f".semgrep.yaml has no rule {RULE_ID}")


def words_in_name(rule: VocabularyRule, name: str) -> list[str]:
    """Application words in name. Words covered by a match of an allowed spelling are not counted (same treatment as semgrep's pattern-not-regex)."""
    spared = [match.span() for match in rule.allowed.finditer(name)]
    return [
        match.group(1).lower()
        for match in rule.forbidden.finditer(name)
        if not any(start <= match.start() and match.end() <= end for start, end in spared)
    ]


def ignored_dirs(root: Path) -> tuple[str, ...]:
    """Name patterns of dirs that semgrep does not look at (the `<pattern>/` lines of the root .semgrepignore) — to align the population of this name test with the population of semgrep, which looks at contents.

    If a line other than a dir pattern (a file pattern, a `!` negation) appears in .semgrepignore, stop instead of widening this reading (do not silently change the population)."""
    lines = (root / ".semgrepignore").read_text(encoding="utf-8").splitlines()
    patterns = [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]
    odd = [pattern for pattern in patterns if not pattern.endswith("/") or pattern.startswith("!")]
    assert odd == [], f".semgrepignore has lines other than dir patterns (extend this test's reading): {odd}"
    return tuple(pattern.removesuffix("/") for pattern in patterns)


def scanned_names(root: Path, package: str) -> list[str]:
    """Paths of files under a package, relative to the package root (excluding dirs semgrep does not look at) — to build the population whose names are checked.

    Walk the tree instead of using the git listing (git ls-files): the daily full verification copies the tree to a remote machine using only the files in the git listing
    and does not carry .git (agentcli/remote_check.py in dotfiles), so a reading that depends on git fails only in the daily run (#1201 — exit code 128).
    The same rule as doeff-adr's semgrep reading (_find_tree_root in registry.py — the meaning of the test is determined by the tree alone)."""
    skipped = ignored_dirs(root)
    base = root / package
    return sorted(
        str(path.relative_to(base))
        for path in base.rglob("*")
        if path.is_file()
        and not any(fnmatch.fnmatch(part, pattern) for part in path.relative_to(root).parts[:-1] for pattern in skipped)
    )


def test_the_rule_names_the_generic_packages() -> None:
    assert read_rule().packages == (
        "packages/doeff-cluster",
        "packages/doeff-records",
    )


def test_a_name_with_an_application_word_is_found_and_a_generic_name_is_not() -> None:
    rule = read_rule()
    assert words_in_name(rule, "src/doeff_cluster/kanban_view.hy") == ["kanban"]
    assert words_in_name(rule, "tests/test_Agora_reads.py") == ["agora"]
    assert words_in_name(rule, "docs/_acp.md") == ["acp"]
    # Spellings preceded by a letter, and size-budget / idle-budget (names of declaration fields of doeff-records / doeff-cluster), do not match.
    assert words_in_name(rule, "src/doeff_cluster/xacp.hy") == []
    assert words_in_name(rule, "src/doeff_records/size_budget.hy") == []
    assert words_in_name(rule, "src/size-budget/budget.hy") == ["budget"]
    assert words_in_name(rule, "tests/test_idle_budget.hy") == []


def test_the_scan_covers_the_packages() -> None:
    # The test does not go green on an empty population: each package's source, type stubs, documents, and deployment materials are included.
    wanted = {
        "packages/doeff-cluster": ["README.md", "pyproject.toml", "src/doeff_cluster/_shared/entry/cluster_foundation.hy"],
        "packages/doeff-records": ["README.md", "pyproject.toml", "src/doeff_records/_values.pyi"],
    }
    for package, names in wanted.items():
        scanned = set(scanned_names(REPO_ROOT, package))
        for name in names:
            assert name in scanned, f"{package}/{name}"


def test_the_scan_reads_a_tree_without_git_and_skips_the_ignored_dirs(tmp_path: Path) -> None:
    # Failure case (#1201): the tree in the daily full verification has no .git — the form reading via the git listing failed here with exit code 128.
    # The name test reads only the tree and does not count files inside dirs semgrep does not look at (.semgrepignore).
    (tmp_path / ".semgrepignore").write_text("__pycache__/\n*.egg-info/\n", encoding="utf-8")
    for name in ("src/doeff_cluster/kanban_view.hy", "src/doeff_cluster/__pycache__/agora.cpython-314.pyc",
                 "src/doeff_cluster.egg-info/acp.txt", "README.md"):
        (tmp_path / "packages/doeff-cluster" / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "packages/doeff-cluster" / name).write_text("", encoding="utf-8")
    assert not (tmp_path / ".git").exists()
    assert scanned_names(tmp_path, "packages/doeff-cluster") == ["README.md", "src/doeff_cluster/kanban_view.hy"]
    rule = read_rule()
    found = [word for name in scanned_names(tmp_path, "packages/doeff-cluster") for word in words_in_name(rule, name)]
    assert found == ["kanban"]


def test_a_semgrepignore_line_outside_the_dir_form_stops_the_scan(tmp_path: Path) -> None:
    # Lines other than dir patterns (file patterns, negations) stop the reading instead of widening it — do not silently change the population.
    (tmp_path / ".semgrepignore").write_text("__pycache__/\n*.log\n", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"\*\.log"):
        ignored_dirs(tmp_path)


def test_no_file_name_in_the_packages_has_an_application_word() -> None:
    rule = read_rule()
    found = [
        f"{package}/{name}: {word}"
        for package in rule.packages
        for name in scanned_names(REPO_ROOT, package)
        for word in words_in_name(rule, name)
    ]
    assert found == [], "\n".join(found)
