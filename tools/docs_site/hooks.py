"""mkdocs の hook — GitHub の上で読む形に書いた docs の page を、site の上でも同じに読めるように 2 つを直す。

1. site の外の repo の file へ張る相対の link を、GitHub の main の file への link に書き換える。docs の page は repo の file
   (packages/*/README.md・examples/・docs/history/ の仕様)へ相対の link を張るが、site に入るのは docs/index.md から辿る page
   だけ(mkdocs.yml の exclude_docs)なので、そのままでは site の上で切れる。在る file への link だけを書き換え、無い file への link は
   書き換えない — mkdocs が warning を出し、--strict の build が落ちる。囲みのコードと `` ` `` の中は書き換えない。
2. 囲みの頭の言語の後の語を落とす。docs の例の検は ```python excerpt・```hy file=greet.hy の印を読むが、pymdownx.superfences は
   言語の後の素の語を読めず、その囲みを囲みと見ないので、後ろの見出しまで崩れる。file=<名> は囲みの題(title)にする。
"""

from __future__ import annotations

import re
from itertools import accumulate
from pathlib import Path
from urllib.parse import unquote

from mkdocs.config.defaults import MkDocsConfig
from mkdocs.structure.files import Files
from mkdocs.structure.pages import Page

GITHUB_BRANCH = "main"
FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})(.*)$")
CODE_SPAN = re.compile(r"(`+[^`]*`+)")
INLINE_LINK = re.compile(r"(\]\()([^)\s]+)(\s+\"[^\"]*\")?(\))")
SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _github_url(
    target: str, page_dir: Path, repo_root: Path, docs_dir: Path, files: Files, repo_url: str
) -> str:
    """target が site の外の在る repo の file なら GitHub の URL を、そうでなければ target のままを返す。"""
    path, hash_mark, anchor = target.partition("#")
    if not path or path.startswith("/") or SCHEME.match(path):
        return target
    resolved = (page_dir / unquote(path)).resolve()
    if resolved.is_relative_to(docs_dir):
        site_file = files.get_file_from_path(resolved.relative_to(docs_dir).as_posix())
        if site_file is not None and not site_file.inclusion.is_excluded():
            return target
    if not resolved.exists() or not resolved.is_relative_to(repo_root):
        return target
    kind = "tree" if resolved.is_dir() else "blob"
    return f"{repo_url}/{kind}/{GITHUB_BRANCH}/{resolved.relative_to(repo_root).as_posix()}{hash_mark}{anchor}"


def _fence_line(line: str) -> str:
    """囲みの線を superfences が読める形にするため — 頭は言語だけと題、閉じの線はそのまま。"""
    match = FENCE.match(line)
    if match is None:
        return line
    indent, marks, info = match.groups()
    words = info.split()
    if not words:
        return line
    titles = [word.removeprefix("file=") for word in words[1:] if word.startswith("file=")]
    return f"{indent}{marks}{words[0]}" + "".join(f' title="{title}"' for title in titles[:1])


def on_page_markdown(markdown: str, page: Page, config: MkDocsConfig, files: Files) -> str:
    """GitHub の上で読む形に書いた page を、site の上でも同じに読ませるため(mkdocs の hook・直す 2 つは module の頭)。

    囲みの中かは線の数の偶奇で読む — docs の page の囲みは入れ子にしない(4 つの ` の囲みは無い)前提。
    """
    docs_dir = Path(config.docs_dir).resolve()
    repo_root = Path(config.config_file_path).resolve().parent
    page_dir = Path(page.file.abs_src_path).resolve().parent
    repo_url = config.repo_url.rstrip("/")

    def rewrite(match: re.Match[str]) -> str:
        """1 つの `](target)` の target だけを替え、題と括弧は残すため。"""
        target = _github_url(match.group(2), page_dir, repo_root, docs_dir, files, repo_url)
        return f"{match.group(1)}{target}{match.group(3) or ''}{match.group(4)}"

    def rewrite_line(line: str) -> str:
        """1 行の中で、`` ` `` の中のコードの例を書き換えないため(split の奇数番目がコード)。"""
        return "".join(
            part if index % 2 else INLINE_LINK.sub(rewrite, part)
            for index, part in enumerate(CODE_SPAN.split(line))
        )

    # 行が囲みの中か = その行までに出た囲みの線の数が奇数か(囲みの線そのものは link を書き換えない)。
    lines = markdown.split("\n")
    fences = [FENCE.match(line) is not None for line in lines]
    return "\n".join(
        _fence_line(line) if is_fence else line if fences_so_far % 2 else rewrite_line(line)
        for line, is_fence, fences_so_far in zip(lines, fences, accumulate(fences), strict=True)
    )
