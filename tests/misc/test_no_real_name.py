"""公開 repo の作者の名義に operator の実名を書かないことを確かめる検。

operator の指示(2026-09-29 "this real name must never be used when making any PR ever" /
"real name must not be used without any approval")で、package の metadata・作者の欄・使用許諾の文書に
実名を書かず、名義は Proboscis <3684241+proboscis@users.noreply.github.com> だけにする。

この検の中にも実名を書かないため、2 つの形で照らす:
- 作者の欄(pyproject.toml の authors / maintainers・package.json の author / contributors)は、許す名義の一覧に
  載っている物だけを通す(名を書かずに済む許しの一覧)。
- 作者の欄の外の文(AUTHORS・LICENSE・CITATION・README・docs/index.md の作者の節)は、語の小文字の sha256 が
  実名の姓の hash と一致する語が 1 つでも在れば赤にする(hash だけを持ち、名そのものは持たない)。
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# 作者の欄に許す名義(これ以外の名と email は赤)。
ALLOWED_NAME = "Proboscis"
ALLOWED_EMAIL = "3684241+proboscis@users.noreply.github.com"

# 実名の姓の小文字の sha256(名そのものは持たない)。
FORBIDDEN_TOKEN_HASHES = frozenset({"8de42984b32a1d5f161c938ee941f300f7d829049d9dc368c7721d4a4f17051a"})

# 作者の欄の外で名を照らす file(git の追跡している file の名で選ぶ)。
PROSE_NAME_PATTERN = re.compile(r"(^|/)(AUTHORS[^/]*|LICENSE[^/]*|CITATION[^/]*|README[^/]*)$")
PROSE_EXTRA = frozenset({"docs/index.md"})
WORD = re.compile(r"[A-Za-z]+")


def listed_files(root: Path) -> list[str]:
    """root の照らす file の path の一覧を返すため(作業樹の外の物・無視する物を照らさない)。

    git の作業樹なら git が追跡している file。.git の無い写し(日次の全体検証は remote_check.py --tree . で .git を運ばずに
    同期して撃つ — agora-redesign #1201)では、空の git の dir を一時に作って写しを作業樹に指し、写しの .gitignore に従う
    「無視しない file」を数える。写しは元の作業樹の追跡する file と無視しない未追跡の file だけを持つので、同じ集合になる
    (写しの .venv などの生成物は .gitignore が外す)。root の外の親の repo の index は読まない。
    """
    if (root / ".git").exists():
        listed = subprocess.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True)
    else:
        with tempfile.TemporaryDirectory(prefix="no-real-name-git-") as scratch:
            subprocess.run(["git", "init", "--quiet", scratch], capture_output=True, text=True, check=True)
            listed = subprocess.run(
                ["git", "--git-dir", str(Path(scratch) / ".git"), "--work-tree", str(root),
                 "ls-files", "--others", "--exclude-standard"],
                cwd=root, capture_output=True, text=True, check=True)
    return sorted(line for line in listed.stdout.splitlines() if line)


def tracked_files() -> list[str]:
    """この repo の照らす file の path の一覧を返すため(listed_files を repo の root で)。"""
    return listed_files(ROOT)


def author_entries(path: str) -> list[object]:
    """metadata の file 1 つから作者の欄の項を全部取り出すため(pyproject.toml と package.json)。"""
    text = (ROOT / path).read_text(encoding="utf-8")
    if path.endswith("pyproject.toml"):
        project = tomllib.loads(text).get("project", {})
        return [*project.get("authors", []), *project.get("maintainers", [])]
    document = json.loads(text)
    author = document.get("author")
    return [*([author] if author is not None else []), *document.get("contributors", [])]


def disallowed_author(entry: object) -> bool:
    """作者の欄の項 1 つが、許す名義の一覧の外か(package.json の文字列の形 "名 <email>" も読む)。"""
    if isinstance(entry, dict):
        return entry.get("name") != ALLOWED_NAME or entry.get("email", ALLOWED_EMAIL) != ALLOWED_EMAIL
    if isinstance(entry, str):
        return entry not in {ALLOWED_NAME, f"{ALLOWED_NAME} <{ALLOWED_EMAIL}>"}
    return True


def test_author_fields_name_only_the_allowed_identity() -> None:
    # pyproject.toml と package.json の作者の欄の全部の項が、許す名義 Proboscis(と noreply の email)だけ。
    metadata = [p for p in tracked_files() if p.endswith(("pyproject.toml", "package.json"))
                and "/node_modules/" not in p and "/tests/data/" not in p]
    found = [f"{path}: {entry!r}" for path in metadata for entry in author_entries(path) if disallowed_author(entry)]
    assert found == [], "作者の欄に許す名義の外の項:\n" + "\n".join(found)


def test_prose_files_do_not_carry_the_real_name() -> None:
    # 作者の欄の外の文(AUTHORS・LICENSE・CITATION・README・docs/index.md)に、実名の姓の語が 1 つも無い。
    prose = [p for p in tracked_files() if (PROSE_NAME_PATTERN.search(p) or p in PROSE_EXTRA) and "/tests/data/" not in p]
    found = []
    for path in prose:
        text = (ROOT / path).read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            if any(hashlib.sha256(word.lower().encode()).hexdigest() in FORBIDDEN_TOKEN_HASHES for word in WORD.findall(line)):
                found.append(f"{path}:{number}")
    assert found == [], "実名の姓の語が在る行(名は出さない):\n" + "\n".join(found)


def test_the_listing_reads_a_copy_without_git() -> None:
    # 失敗ケース(#1201): 日次の全体検証は .git を運ばない写しで撃つ。写しでも元の作業樹の git ls-files と同じ集合を数え、
    # .gitignore が外す生成物(写しで作られる .venv の中の README)は数えない。直す前の形(写しで git ls-files)は
    # 「not a git repository」(status 128)で落ちる。
    with tempfile.TemporaryDirectory(prefix="no-real-name-") as scratch:
        source = Path(scratch) / "source"
        for path, text in {".gitignore": ".venv/\n", "README.md": "doc\n", "packages/a/LICENSE": "license\n",
                           "packages/a/pyproject.toml": "[project]\n"}.items():
            (source / path).parent.mkdir(parents=True, exist_ok=True)
            (source / path).write_text(text, encoding="utf-8")
        subprocess.run(["git", "init", "--quiet", str(source)], capture_output=True, check=True)
        subprocess.run(["git", "-C", str(source), "add", "--all"], capture_output=True, check=True)
        copy = Path(scratch) / "copy"
        shutil.copytree(source, copy, ignore=shutil.ignore_patterns(".git"))
        (copy / ".venv" / "lib").mkdir(parents=True)
        (copy / ".venv" / "lib" / "README.md").write_text("vendored\n", encoding="utf-8")
        assert not (copy / ".git").exists()
        expected = [".gitignore", "README.md", "packages/a/LICENSE", "packages/a/pyproject.toml"]
        assert listed_files(source) == expected
        assert listed_files(copy) == expected


def test_the_name_check_catches_a_planted_name() -> None:
    # 照らしが効くこと: 名の hash と一致する語は赤・許す名義は通る(名そのものは書かず、hash の元の語を作らない)。
    assert disallowed_author({"name": "Somebody Else", "email": ALLOWED_EMAIL})
    assert disallowed_author({"name": ALLOWED_NAME, "email": "someone@example.com"})
    assert not disallowed_author({"name": ALLOWED_NAME, "email": ALLOWED_EMAIL})
    assert not disallowed_author(ALLOWED_NAME)
    assert hashlib.sha256(b"proboscis").hexdigest() not in FORBIDDEN_TOKEN_HASHES
