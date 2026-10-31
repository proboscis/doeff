"""入口の file に、社内の名が無いこと。

入口 = 初めて来た人が GitHub と PyPI で最初に読む file — 根の README.md・docs/index.md・docs/README.md・
CONTRIBUTING.md・GitHub の issue と pull request の雛形(.github/ISSUE_TEMPLATE/ の下・.github/PULL_REQUEST_TEMPLATE.md —
問いと報告の画面に出る)・packages/*/README.md(PyPI の説明の欄にもなる)・examples/ の下の file。
社内の名 = 4 種類 — 上に載る業務の系の名・作業ボードと会話の id・cluster の機体の名・人の home の path。

語の一覧の定義元はこの module の INTERNAL_NAMES 1 つ。semgrep の規則 doeff-packages-have-no-application-vocabulary の
語の一覧は読まない: あちらは汎用の package の中身の全部に当てる「業務の系の語」で、conversation・budget・artifact・herdr を
含む。入口ではこの 4 語が一般の意味で出る(doeff-agents の herdr の backend・doeff-hy-pytest の時間の budget)ので、
入口の一覧は名に絞って別に持つ。
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 語の一覧(この 1 か所)。種類ごとの正規表現で、大文字小文字を区別しない。前が英数字の綴り(macp・seos)は当たらない。
INTERNAL_NAMES: dict[str, str] = {
    # 上に載る業務の系の名と、その repo の dir。agora-redesign・agora-controllers も agora で当たる。後ろは見ない
    # (AcpProtocolError・kanban_view も当てる)。
    "業務の系の名": r"agora|kanban|acp|hypha|mediagen|sporocarp|controllers/|clients/hy",
    # 作業ボードの card と通過点の鍵(ki-・ms- と 16 進 12 桁)と、依頼・会話・発言の id(lt-・c-・m- と ULID の 26 字)。
    "作業ボードと会話の id": r"(?:ki|ms)-[0-9a-f]{12}(?![0-9a-z])|(?:lt|c|m)-[0-9a-hjkmnp-tv-z]{26}(?![0-9a-z])",
    # cluster の Node(kubectl get nodes の名)と、自前の判定器の名。後ろが英字の綴り(Atlassian)は当たらない。
    "cluster の機体の名": r"(?:zeus|atlas|eos|seimf|k3s-\d+)(?![a-z])",
    # 人の home の下の path(Linux と macOS)。大文字小文字を区別する — URL の /users/42 は当たらない。例の path は ~ か
    # $HOME で書く。
    "人の home の path": r"(?-i:/home/|/Users/)[A-Za-z0-9_.-]+",
}

_PATTERNS = {kind: re.compile(rf"(?i)(?<![a-z0-9])(?:{pattern})") for kind, pattern in INTERNAL_NAMES.items()}

# 決まった名の入口(根からの path)。.github/ISSUE_TEMPLATE/ の下・packages/*/README.md・examples/ の下は木から読む。
FIXED_ENTRY_FILES = (
    "README.md",
    "docs/index.md",
    "docs/README.md",
    "CONTRIBUTING.md",
    "CODE_OF_CONDUCT.md",
    "SECURITY.md",
    ".github/PULL_REQUEST_TEMPLATE.md",
)


def internal_names_in(text: str) -> list[str]:
    """text の中の社内の名を `<種類>: <綴り>` の形で、出た順に返す。"""
    found = [
        (match.start(), f"{kind}: {match.group(0)}")
        for kind, pattern in _PATTERNS.items()
        for match in pattern.finditer(text)
    ]
    return [name for _, name in sorted(found)]


def entry_files(root: Path) -> list[str]:
    """root の下の入口の file の、root からの path。examples/ の下は cache の dir(__pycache__・. で始まる dir)を除く全部。"""
    fixed = [name for name in FIXED_ENTRY_FILES if (root / name).is_file()]
    issue_templates = sorted(
        str(path.relative_to(root)) for path in root.glob(".github/ISSUE_TEMPLATE/*") if path.is_file()
    )
    packages = sorted(str(path.relative_to(root)) for path in root.glob("packages/*/README.md"))
    examples = sorted(
        str(path.relative_to(root))
        for path in (root / "examples").rglob("*")
        if path.is_file()
        and not any(part == "__pycache__" or part.startswith(".") for part in path.relative_to(root).parts[:-1])
    )
    return fixed + issue_templates + packages + examples


def internal_names_in_entry_files(root: Path) -> list[str]:
    """入口の file の中の社内の名を `<path>:<行>: <種類>: <綴り>` の形で返す。"""
    return [
        f"{name}:{number}: {found}"
        for name in entry_files(root)
        for number, line in enumerate(
            (root / name).read_text(encoding="utf-8", errors="replace").splitlines(), start=1
        )
        for found in internal_names_in(line)
    ]


#: 作り物の card と依頼の id(本物の id ではない)。綴りを分けて置き、この file 自身を id の形の検出に当てない。
FAKE_CARD_ID = "ki-" + "0123456789ab"
FAKE_REQUEST_ID = "lt-" + "0123456789ABCDEFGHJKMNPQRS"


def test_each_kind_finds_its_names() -> None:
    assert internal_names_in("see agora-redesign") == ["業務の系の名: agora"]
    assert internal_names_in("(raise (AcpProtocolError msg))") == ["業務の系の名: Acp"]
    assert internal_names_in("a Kanban card and the hypha view") == ["業務の系の名: Kanban", "業務の系の名: hypha"]
    assert internal_names_in(f"card {FAKE_CARD_ID}") == [f"作業ボードと会話の id: {FAKE_CARD_ID}"]
    assert internal_names_in(f"request {FAKE_REQUEST_ID}") == [f"作業ボードと会話の id: {FAKE_REQUEST_ID}"]
    assert internal_names_in("image: zeus:5000/doeff-worker on k3s-2") == [
        "cluster の機体の名: zeus",
        "cluster の機体の名: k3s-2",
    ]
    assert internal_names_in("cd /home/someone/repos && ls /Users/someone") == [
        "人の home の path: /home/someone",
        "人の home の path: /Users/someone",
    ]


def test_general_words_and_public_names_are_not_internal_names() -> None:
    # 入口が一般の意味で使う語・公開の名・前後が英字の綴り・URL の path は当たらない。
    spared = [
        "the herdr backend keeps one conversation per session",
        "a time budget for each test and the build artifact",
        "https://github.com/proboscis/doeff",
        "Atlassian Jira, the macp tool, seos",
        "GET https://api.example.com/users/42",
        "a 12-character id ab-4064da9af9eb and ~/project/README.md",
    ]
    assert [text for text in spared if internal_names_in(text)] == []


def test_the_scan_reads_the_entry_files_and_finds_a_planted_name(tmp_path: Path) -> None:
    # 入口の外(docs の他の page・package の src・GitHub の workflow)は読まない。examples/ の cache の dir は読まない。
    for name in (
        "README.md",
        "docs/index.md",
        "docs/README.md",
        "CONTRIBUTING.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/workflows/ci.yml",
        "docs/history/notes.md",
        "packages/doeff-a/README.md",
        "packages/doeff-a/src/agora.py",
        "examples/demo.py",
        "examples/nested/demo.hy",
        "examples/__pycache__/demo.cpython-314.pyc",
        "examples/.ipynb_checkpoints/demo.py",
    ):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("# zeus\n", encoding="utf-8")
    assert entry_files(tmp_path) == [
        "README.md",
        "docs/index.md",
        "docs/README.md",
        "CONTRIBUTING.md",
        ".github/PULL_REQUEST_TEMPLATE.md",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/config.yml",
        "packages/doeff-a/README.md",
        "examples/demo.py",
        "examples/nested/demo.hy",
    ]
    for name in entry_files(tmp_path):
        (tmp_path / name).write_text("# doeff\n", encoding="utf-8")
    assert internal_names_in_entry_files(tmp_path) == []
    (tmp_path / "packages/doeff-a/README.md").write_text("# doeff-a\n\nRuns on atlas.\n", encoding="utf-8")
    assert internal_names_in_entry_files(tmp_path) == ["packages/doeff-a/README.md:3: cluster の機体の名: atlas"]


def test_the_scan_covers_the_entry_files_of_this_repo() -> None:
    # 母集団が空で緑にならない: 決まった名の入口が全部在り、issue の雛形・部品の README・examples/ の file が入っている。
    scanned = entry_files(REPO_ROOT)
    assert [name for name in FIXED_ENTRY_FILES if name not in scanned] == []
    for name in (".github/ISSUE_TEMPLATE/bug_report.yml", ".github/ISSUE_TEMPLATE/question.yml"):
        assert name in scanned
    for name in ("packages/doeff-vm/README.md", "packages/doeff-agents/README.md", "packages/doeff-cluster/README.md"):
        assert name in scanned
    assert any(name.startswith("examples/") for name in scanned)


def test_no_entry_file_has_an_internal_name() -> None:
    found = internal_names_in_entry_files(REPO_ROOT)
    assert found == [], (
        "入口の file に社内の名がある — 一般の言葉に書き換える(語の一覧と訳は "
        "tests/test_entry_files_have_no_internal_names.py の INTERNAL_NAMES):\n" + "\n".join(found)
    )
