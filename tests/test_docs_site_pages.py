"""Check that the set of pages of the docs site (mkdocs.yml) matches the table of contents of docs/index.md (initiative doeff-publish).

Only user-facing pages reachable from the table of contents of docs/index.md go into the site; development records (docs/adr, docs/specs, etc.) are
not included. The page set lives in two places in mkdocs.yml (the allow list of exclude_docs and nav), and a mismatch between the two makes the site build
(mkdocs build --strict) fail. This check fails on a mismatch between the table of contents and nav -- if a page is added to the table of contents but not to nav, that page does not appear in the
site (the build does not fail; a hook replaces it with a link to GitHub).

Table of contents = the sections "Table of Contents" and "Pages That Need Other Components" of docs/index.md. Links outside docs/ (READMEs under packages/) are
listed in nav as URLs, so they are not compared here.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MKDOCS = ROOT / "mkdocs.yml"
TABLE_SECTIONS = ("## Table of Contents", "## Pages That Need Other Components")
# Pages in dirs under docs/ that the site publishes (names relative to docs/). The other files of docs/positioning are internal sales material and
# are not published. The page defining the axes of explanation is put on the site as the source of public text (README, docs, articles) (section 17 of the
# initiative doeff-publish). The English articles are put on the site so that their click-through figures work on the site.
PUBLISHED_SUBDIR_PAGES = (
    "positioning/what-doeff-is.md",
    "articles/algebraic-effects-in-python.md",
    "articles/yield-and-async.md",
    "articles/time-and-clocks.md",
)
LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")


class _MkdocsLoader(yaml.SafeLoader):
    """To read mkdocs.yml -- tags like `!!python/...` (toc slugify, mermaid fences) are skipped without building values."""


_MkdocsLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/", lambda _loader, _suffix, _node: None
)


def _mkdocs() -> dict:
    """To read the site settings (nav and exclude_docs) without installing mkdocs."""
    return yaml.load(MKDOCS.read_text(encoding="utf-8"), Loader=_MkdocsLoader)


def _section_lines(lines: list[str], heading: str) -> list[str]:
    """To compare using only the lines inside one `## ` section of the table of contents (up to the next `## ` heading)."""
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start:end]


def _table_pages() -> list[str]:
    """Return, without duplicates and in order of appearance, the docs/ pages linked from the two sections of the table of contents (the source of the definition of the site page set)."""
    lines = (DOCS / "index.md").read_text(encoding="utf-8").splitlines()
    links = [
        link
        for heading in TABLE_SECTIONS
        for line in _section_lines(lines, heading)
        for link in LINK.findall(line)
    ]
    inside = [link for link in links if (DOCS / link).resolve().is_relative_to(DOCS.resolve())]
    return list(dict.fromkeys(inside))


def _nav_pages(nav: list) -> list[str]:
    """Return the leaf pages of the nav tree from the top (excluding http URL leaves)."""
    leaves = [
        value for entry in nav for value in (entry.values() if isinstance(entry, dict) else [entry])
    ]
    return [
        page
        for leaf in leaves
        for page in (_nav_pages(leaf) if isinstance(leaf, list) else [leaf])
        if not page.startswith(("http://", "https://"))
    ]


def _allowed_pages(exclude_docs: str) -> list[str]:
    """Return the pages on `!/<page>.md` lines of exclude_docs (the first line `*` excludes everything else; `!/assets/**` is the theme's files)."""
    return [
        line.removeprefix("!/")
        for line in exclude_docs.splitlines()
        if line.startswith("!/") and line.endswith(".md")
    ]


def test_nav_follows_the_index_table() -> None:
    """To fail when a page added to the table of contents does not appear on the site (forgot to add to nav)."""
    nav = _nav_pages(_mkdocs()["nav"])
    assert nav[0] == "index.md", "the head of nav in mkdocs.yml is index.md (Home)"
    assert nav[1:] == _table_pages(), (
        "the pages of nav in mkdocs.yml are not aligned with the table of contents of docs/index.md (Table of Contents and Pages That Need Other Components) in"
        " the same order -- when adding a page to the table of contents, add it to nav and exclude_docs in the same place"
    )


def test_site_holds_only_the_nav_pages() -> None:
    """To fail, without running the build, when a docs/ file not in nav (development records, etc.) would appear on the site."""
    config = _mkdocs()
    exclude_docs = config["exclude_docs"]
    assert exclude_docs.splitlines()[0] == "*", (
        "the first line of exclude_docs is `*` (do not put docs/ files outside the allow list into the site)"
    )
    assert sorted(_allowed_pages(exclude_docs)) == sorted(_nav_pages(config["nav"])), (
        "the allow list of exclude_docs in mkdocs.yml and the pages of nav are not aligned"
    )


def test_site_pages_are_top_level_docs() -> None:
    """To keep development records (dirs under docs/ such as docs/adr and docs/specs) out of the site."""
    pages = _nav_pages(_mkdocs()["nav"])
    assert [page for page in pages if "/" in page and page not in PUBLISHED_SUBDIR_PAGES] == []
    assert [page for page in pages if not (DOCS / page).is_file()] == []
    assert [page for page in PUBLISHED_SUBDIR_PAGES if page not in pages] == [], (
        "a page of PUBLISHED_SUBDIR_PAGES is not in nav -- if it was removed, remove it from the list too"
    )
