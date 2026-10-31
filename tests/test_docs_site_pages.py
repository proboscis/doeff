"""docs の site(mkdocs.yml)の page の組が、docs/index.md の目次と同じである事の検(取り組み doeff-publish・card ki-ba5f61eb7962)。

site に入るのは docs/index.md の目次から辿る利用者向けの page だけで、開発の記録(docs/adr・docs/specs ほか)は
入れない。page の組は mkdocs.yml の 2 か所(exclude_docs の許す一覧と nav)に在り、2 つの食い違いは site の build
(mkdocs build --strict)が落とす。この検は、目次と nav の食い違いを落とす — 目次に page を足して nav に足し忘れると、その page は
site に出ない(build は落ちず、hook が GitHub への link に替える)。

目次 = docs/index.md の節「Table of Contents」と「Pages That Need Other Components」。docs/ の外の link(packages/ の README)は
nav に URL で載せるので、ここでは比べない。
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
MKDOCS = ROOT / "mkdocs.yml"
TABLE_SECTIONS = ("## Table of Contents", "## Pages That Need Other Components")
# docs/ の下の dir に在って site が公開する page(docs/ からの名)。docs/positioning の他の file は内向きの売り方の資料で、
# 公開しない。説明の軸の定義の page は、公開の文(README・docs・記事)の出典として site に出す(取り組み doeff-publish の
# 区切り 17・card ki-3a3b13c1d16e)。
PUBLISHED_SUBDIR_PAGES = ("positioning/what-doeff-is.md",)
LINK = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")


class _MkdocsLoader(yaml.SafeLoader):
    """mkdocs.yml を読むため — `!!python/...` の札(toc の slugify・mermaid の囲み)は値を作らずに読み飛ばす。"""


_MkdocsLoader.add_multi_constructor(
    "tag:yaml.org,2002:python/", lambda _loader, _suffix, _node: None
)


def _mkdocs() -> dict:
    """site の設定(nav と exclude_docs)を、mkdocs を入れずに読むため。"""
    return yaml.load(MKDOCS.read_text(encoding="utf-8"), Loader=_MkdocsLoader)


def _section_lines(lines: list[str], heading: str) -> list[str]:
    """目次の `## ` の節 1 つの中の行だけを比べに使うため(次の `## ` の見出しの前まで)。"""
    start = lines.index(heading) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
    return lines[start:end]


def _table_pages() -> list[str]:
    """目次の 2 つの節が link する docs/ の中の page を、出た順に重ねずに返す(site の page の組の定義元)。"""
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
    """nav の木の葉の page を上から順に返す(http の URL の葉は除く)。"""
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
    """exclude_docs の `!/<page>.md` の行の page を返す(最初の行 `*` が他の全部を外す・`!/assets/**` は theme の file)。"""
    return [
        line.removeprefix("!/")
        for line in exclude_docs.splitlines()
        if line.startswith("!/") and line.endswith(".md")
    ]


def test_nav_follows_the_index_table() -> None:
    """目次に足した page が site に出ない(nav に足し忘れる)事を落とすため。"""
    nav = _nav_pages(_mkdocs()["nav"])
    assert nav[0] == "index.md", "mkdocs.yml の nav の頭は index.md(Home)"
    assert nav[1:] == _table_pages(), (
        "mkdocs.yml の nav の page が docs/index.md の目次(Table of Contents と Pages That Need Other Components)と"
        "同じ順で揃っていない — 目次に page を足したら nav と exclude_docs にも同じ所へ足す"
    )


def test_site_holds_only_the_nav_pages() -> None:
    """nav に無い docs/ の file(開発の記録ほか)が site に出る事を、build を走らせずに落とすため。"""
    config = _mkdocs()
    exclude_docs = config["exclude_docs"]
    assert exclude_docs.splitlines()[0] == "*", (
        "exclude_docs の最初の行は `*`(許す一覧の外の docs/ の file を site に入れない)"
    )
    assert sorted(_allowed_pages(exclude_docs)) == sorted(_nav_pages(config["nav"])), (
        "mkdocs.yml の exclude_docs の許す一覧と nav の page が揃っていない"
    )


def test_site_pages_are_top_level_docs() -> None:
    """開発の記録(docs/adr・docs/specs ほか、docs/ の下の dir)を site に入れないため。"""
    pages = _nav_pages(_mkdocs()["nav"])
    assert [page for page in pages if "/" in page and page not in PUBLISHED_SUBDIR_PAGES] == []
    assert [page for page in pages if not (DOCS / page).is_file()] == []
    assert [page for page in PUBLISHED_SUBDIR_PAGES if page not in pages] == [], (
        "PUBLISHED_SUBDIR_PAGES の page が nav に無い — 外したなら一覧からも消す"
    )
