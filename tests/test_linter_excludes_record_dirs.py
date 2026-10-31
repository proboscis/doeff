"""設計の記録の dir は doeff-linter の対象の外 — 定義の 2 か所(record-dirs と linter の exclude)が食い違わないこと。

設計の記録は、その時点の証拠: 測りの script・盲検の A・B が書いた物の写しを置く。書き直すと記録が
変わるので、業務の code と検に当てる規則(DOEFF002・004・006・007 ほか)の対象にしない。記録の dir の定義は root の pyproject.toml の
`[tool.doeff] record-dirs` の 1 点で、linter の `[tool.doeff-linter] exclude` は path の部品 1 つ(dir の名)と一致する時だけ外すので、
record-dirs の末尾の名を exclude に写してある。

- 記録の dir を足したのに exclude に末尾の名を足さないと、その dir に置いた script の所見が日次の lint(基点の比べ)を赤にする。
- 逆に、exclude に足した名が記録の dir の外の dir にも在ると、そこの code が黙って規則の外になる。
"""

from __future__ import annotations

import subprocess
from pathlib import Path, PurePosixPath
from typing import Any

import tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]

#: exclude の中で、記録の dir の名ではない行(道具の検が読む見本の入力の dir — pyproject.toml の註)。
FIXTURES = "fixtures"


def _pyproject() -> dict[str, Any]:
    """tomllib が返す写像のまま(TOML の境界の値 — 使う 2 つの欄はこの検が形を確かめる)。"""
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))


def _record_dirs() -> list[str]:
    """設計の記録の dir(`/` で終わる repo の根からの path)。"""
    return list(_pyproject()["tool"]["doeff"]["record-dirs"])


def _excluded_names() -> set[str]:
    return set(_pyproject()["tool"]["doeff-linter"]["exclude"])


def _tracked_python() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", "*.py", "*.pyi"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    )
    return [path for path in listed.stdout.split("\0") if path]


def test_every_record_dir_is_out_of_the_linters_scope() -> None:
    """記録の dir の末尾の名は、linter の exclude に在る。"""
    names = {PurePosixPath(record).name for record in _record_dirs()}
    missing = sorted(names - _excluded_names())
    assert missing == [], (
        f"設計の記録の dir {missing} が linter の対象に入っている — pyproject.toml の [tool.doeff-linter] exclude へ"
        "その末尾の名を足す(記録は書き直せないので、規則の対象にしない)"
    )


def test_no_excluded_name_hides_a_directory_outside_the_record_dirs() -> None:
    """記録の dir の名を exclude に足したことで、記録の dir の外の code が規則の外にならない。"""
    record_names = {PurePosixPath(record).name for record in _record_dirs()}
    hidden = sorted(
        path
        for path in _tracked_python()
        if (set(PurePosixPath(path).parts[:-1]) & (_excluded_names() - {FIXTURES}) & record_names)
        and not any(path.startswith(record) for record in _record_dirs())
    )
    assert hidden == [], (
        f"記録の dir の名と同じ名の dir に在る code が linter の対象の外になっている: {hidden[:5]} — 記録の dir の外には"
        "その名の dir を置かない(置くなら exclude の仕組みを path の接頭辞に広げる)"
    )
