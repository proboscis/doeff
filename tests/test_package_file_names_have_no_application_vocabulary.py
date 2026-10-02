"""汎用の package の file の名に、上に載る業務の系の語が無いこと。

file の中身は semgrep の規則 doeff-packages-have-no-application-vocabulary が commit の hook で止める。
semgrep は中身しか見ないので、名だけをこの検が見る。語の一覧・許す綴り・対象の package は規則から読み、
ここに写さない(定義元は .semgrep.yaml の規則 1 つ — agora-redesign #2875)。
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RULE_ID = "doeff-packages-have-no-application-vocabulary"
PACKAGE_INCLUDE = re.compile(r"^/(packages/[^/*]+)/\*\*$")


@dataclass(frozen=True)
class VocabularyRule:
    """規則から読んだ 3 つ: 業務の語・許す綴り・対象の package(repo の根からの dir)。"""

    forbidden: re.Pattern[str]
    allowed: re.Pattern[str]
    packages: tuple[str, ...]


def _mapping(value: object, where: str) -> dict[object, object]:
    if not isinstance(value, dict):
        raise AssertionError(f"{where} が mapping でない: {value!r}")
    return value


def _sequence(value: object, where: str) -> list[object]:
    if not isinstance(value, list):
        raise AssertionError(f"{where} が list でない: {value!r}")
    return value


def _text(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise AssertionError(f"{where} が文字列でない: {value!r}")
    return value


def _clause(patterns: list[object], key: str) -> re.Pattern[str]:
    for clause in patterns:
        found = _mapping(clause, f"{RULE_ID} の patterns の項").get(key)
        if found is not None:
            return re.compile(_text(found, f"{RULE_ID} の {key}"))
    raise AssertionError(f"規則 {RULE_ID} に {key} が無い")


def read_rule() -> VocabularyRule:
    document = _mapping(yaml.safe_load((REPO_ROOT / ".semgrep.yaml").read_text(encoding="utf-8")), ".semgrep.yaml")
    for item in _sequence(document.get("rules"), ".semgrep.yaml の rules"):
        rule = _mapping(item, ".semgrep.yaml の規則")
        if rule.get("id") != RULE_ID:
            continue
        patterns = _sequence(rule.get("patterns"), f"{RULE_ID} の patterns")
        include = _sequence(_mapping(rule.get("paths"), f"{RULE_ID} の paths").get("include"), f"{RULE_ID} の include")
        # include のうち repo の package を名指す項だけ(検体の写しの項は除く)。
        packages = tuple(
            found.group(1)
            for entry in include
            if (found := PACKAGE_INCLUDE.match(_text(entry, f"{RULE_ID} の include の項")))
        )
        return VocabularyRule(_clause(patterns, "pattern-regex"), _clause(patterns, "pattern-not-regex"), packages)
    raise AssertionError(f".semgrep.yaml に規則 {RULE_ID} が無い")


def words_in_name(rule: VocabularyRule, name: str) -> list[str]:
    """name の中の業務の語。許す綴りの一致に覆われた語は数えない(semgrep の pattern-not-regex と同じ扱い)。"""
    spared = [match.span() for match in rule.allowed.finditer(name)]
    return [
        match.group(1).lower()
        for match in rule.forbidden.finditer(name)
        if not any(start <= match.start() and match.end() <= end for start, end in spared)
    ]


def tracked_names(package: str) -> list[str]:
    """package の下の追跡されている file の、package の根からの path。"""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", package],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return [path.removeprefix(f"{package}/") for path in listed.split("\0") if path]


def test_the_rule_names_the_three_packages() -> None:
    assert read_rule().packages == ("packages/doeff-cluster", "packages/doeff-records", "packages/doeff-claude-code")


def test_a_name_with_an_application_word_is_found_and_a_generic_name_is_not() -> None:
    rule = read_rule()
    assert words_in_name(rule, "src/doeff_cluster/kanban_view.hy") == ["kanban"]
    assert words_in_name(rule, "tests/test_Agora_reads.py") == ["agora"]
    assert words_in_name(rule, "docs/_acp.md") == ["acp"]
    # 前が英字の綴りと、doeff-records の宣言の欄の名 size-budget は当たらない。
    assert words_in_name(rule, "src/doeff_cluster/xacp.hy") == []
    assert words_in_name(rule, "src/doeff_records/size_budget.hy") == []
    assert words_in_name(rule, "src/size-budget/budget.hy") == ["budget"]


def test_the_scan_covers_the_packages() -> None:
    # 母集団が空で緑にならない: 各 package の source・型の stub・文書・配備の材料が入っている。
    wanted = {
        "packages/doeff-cluster": ["README.md", "pyproject.toml", "src/doeff_cluster/shared/entry/cluster_foundation.hy"],
        "packages/doeff-records": ["README.md", "pyproject.toml", "src/doeff_records/values.pyi"],
        "packages/doeff-claude-code": ["pyproject.toml"],
    }
    for package, names in wanted.items():
        tracked = set(tracked_names(package))
        for name in names:
            assert name in tracked, f"{package}/{name}"


def test_no_file_name_in_the_packages_has_an_application_word() -> None:
    rule = read_rule()
    found = [
        f"{package}/{name}: {word}"
        for package in rule.packages
        for name in tracked_names(package)
        for word in words_in_name(rule, name)
    ]
    assert found == [], "\n".join(found)
