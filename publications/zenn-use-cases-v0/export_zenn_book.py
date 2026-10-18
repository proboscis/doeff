"""Zennの本の正本(books/<本のslug>/)と原稿から、ZennのGitHub連携に置けるbooks/とimages/の組を書き出す。

本の正本は、Zennの本と同じ形でこのディレクトリのbooks/<本のslug>/に置く。

- config.yaml: Zennの本の設定(title・summary・topics・published・price・chapters)。chaptersの並びが
  章立てで、そのまま写す。publishedがfalse、priceが0(無料)でなければ止める。
- <章のslug>.md: 章の雛形。front matterのsourceが、章にする原稿(doeff-*.md)の名。本文は、頭のつなぎ・
  印の行「<!-- 原稿の本文 -->」・終わりのつなぎの順に書く。
- rewrites.json: 連載の中でだけ通じる原稿の文を、本の文へ直す組。鍵「*」の組は全部の章に、原稿の名の鍵の
  組はその章だけに当てる。直す前の文が原稿に無ければ(原稿が変わった)、止める。
- cover.png: 表紙(幅と高さの比は1:1.4・1MBまで)。正本はcover.svgで、visuals/render_svg.mjsで撮る。

章の本文は、書き出すたびに原稿から作る。本のために原稿を写して直すことはしない。原稿の本文には次を当てる。

1. rewrites.jsonの組で、連載の回を指す文を直す。
2. 参考の節の「- 前の回:」「- 次の回:」の行を外す。本では、章の頭と終わりのつなぎが受け持つ。
3. 「この記事」を「この章」にする。
4. 頭と終わりのつなぎで挟み、本の章を指すリンク(「3本目の[題](doeff-time.md)」など)を「第3章「<章の題>」」に
   する。リンクの文字が「章」なら「第3章」だけにする。つなぎの中でも、前後の章はこのリンクで書く。
5. 残りのリンクは、render_release.pyと同じく公開状態に合わせて直す(本に無い記事は題だけを残す)。

連載の言葉(「N本目」「連載」「前の回:」「メイン記事」「この記事」)が残る章、Zennに置けない画像、
公開先から開けないリンクがあれば、何も書かずに全部を表示して止まる。出力先の他のファイルは消さない。
出力はzenn-check/check.mjs(zenn-cliのプレビューとzenn-modelの検査)で確かめる。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
from collections.abc import Callable
from dataclasses import dataclass
from functools import reduce
from pathlib import Path

import export_zenn as zenn
import render_release as release

SOURCE = Path(__file__).resolve().parent
BOOKS = SOURCE / "books"
# 章の雛形で、原稿の本文を入れる所の印の行。
BODY_MARK = "<!-- 原稿の本文 -->"
# rewrites.jsonで、全部の章に当てる組の鍵。
EVERY_CHAPTER = "*"
# Zennの本のconfig.yamlで書く項目と、その順。
CONFIG_KEYS = ("title", "summary", "topics", "published", "price", "chapters")
# Zennの本の表紙の大きさの上限と、幅に対する高さの比(zenn-modelの検査と同じく小数1桁で比べる)。
COVER_LIMIT_BYTES = 1024 * 1024
COVER_RATIO = 1.4
# Zennの章のslug(章のファイル名)。zenn-modelの検査と同じ規則。
_CHAPTER_SLUG = re.compile(r"[0-9a-z_-]{1,50}")
# 本の章を指すリンク。前に「3本目の」「前の6本目の」があれば、それも章の番号に置き換える。
_CHAPTER_LINK = re.compile(r"(?:(?:前の)?\d+本目の)?\[([^\]]+)\]\((doeff-[a-z]+\.md)(?:#[^)]*)?\)")
# 参考の節の、公開順の前後の回へのリンクの行。
_SERIES_LINE = re.compile(r"^- (?:前|次)の回:.*\n", re.M)
# 本の章に残ってはいけない、連載の中でだけ通じる言葉。
SERIES_WORDS = (r"\d+本目", "連載", r"(?:前|次)の回:", "メイン記事", "この記事")
_CODE_FENCE = re.compile(r"(^```.*?^```[ \t]*$)", re.S | re.M)


@dataclass(frozen=True)
class Rewrite:
    """原稿の文を、本の文へ直す1組。"""

    before: str
    after: str


@dataclass(frozen=True)
class SourceRewrites:
    """1本の原稿の章だけに当てる、直す組の並び。"""

    source: str
    rules: tuple[Rewrite, ...]


@dataclass(frozen=True)
class RewriteTable:
    """rewrites.jsonを読んだ物(全部の章に当てる組と、原稿ごとの組)。"""

    common: tuple[Rewrite, ...]
    by_source: tuple[SourceRewrites, ...]

    def own(self, source: str) -> tuple[Rewrite, ...]:
        """原稿1本の章だけに当てる組を引く。"""
        return next((item.rules for item in self.by_source if item.source == source), ())


@dataclass(frozen=True)
class Template:
    """章の雛形1つ(章のslug・原稿の名・頭のつなぎ・終わりのつなぎ)。"""

    slug: str
    source: str
    head: str
    tail: str


@dataclass(frozen=True)
class TemplateProblem:
    """読めなかった章の雛形と、その訳。"""

    message: str


@dataclass(frozen=True)
class ChapterRef:
    """本の章の目録の1行(原稿の名・章の番号・章の題)。リンクを章の番号と題に直す時に引く。"""

    source: str
    number: int
    title: str


@dataclass(frozen=True)
class Chapter:
    """書き出す章1つ(章のslug・原稿の名・章の番号と題・章のMarkdown・画像・食い違い)。"""

    slug: str
    source: str
    number: int
    title: str
    markdown: str
    images: tuple[str, ...]
    problems: tuple[str, ...]


@dataclass(frozen=True)
class Book:
    """書き出す本1冊(本のslug・正本のディレクトリ・config.yamlの文・章・本全体の食い違い)。"""

    slug: str
    directory: Path
    config: str
    chapters: tuple[Chapter, ...]
    problems: tuple[str, ...]


def parse_config(text: str) -> dict[str, object] | None:
    """config.yamlを読む。値はJSONと同じ書き方で、最後のchaptersは「  - <slug>」の行の並びの形だけを読む。

    #で始まる行と空の行は読み飛ばす。この形に合わない行があればNoneを返す。
    """
    lines = tuple(line for line in text.splitlines() if line.strip() and not line.startswith("#"))
    if "chapters:" not in lines:
        return None
    start = lines.index("chapters:")
    heads, items = lines[:start], lines[start + 1 :]
    pairs = tuple(line.partition(":") for line in heads)
    shaped = all(
        colon and key == key.strip() and not key.startswith(" ") for key, colon, _ in pairs
    )
    if not shaped or not all(item.startswith("  - ") for item in items):
        return None
    try:
        fields = {key: json.loads(value.strip()) for key, _, value in pairs}
    except json.JSONDecodeError:
        return None
    return {**fields, "chapters": [item.removeprefix("  - ").strip() for item in items]}


def chapter_slugs(fields: dict[str, object]) -> tuple[str, ...]:
    """config.yamlのchaptersの並びを返す。文字列の並びでなければ空にし、config_problemsが訳を出す。"""
    chapters = fields.get("chapters")
    if not isinstance(chapters, list) or not all(isinstance(slug, str) for slug in chapters):
        return ()
    return tuple(chapters)


def config_problems(fields: dict[str, object]) -> tuple[str, ...]:
    """config.yamlが、Zennの本の規則とこの本の決まり(無料・非公開)に合わない所を全部返す。"""
    title, summary, topics, published, price, _ = (fields.get(key) for key in CONFIG_KEYS)
    slugs = chapter_slugs(fields)
    checks = (
        (tuple(fields) == CONFIG_KEYS, f"config.yamlの項目は{CONFIG_KEYS}の順にしてください"),
        (isinstance(title, str) and 0 < len(title) <= 70, "titleは1〜70字の文字列にしてください"),
        (isinstance(summary, str) and len(summary) > 0, "summary(本の説明)を書いてください"),
        (published is False, "publishedはfalseにしてください(公開の操作はユーザーがする)"),
        (type(price) is int and price == 0, "priceは0にしてください(無料の本にする)"),
        (len(slugs) > 0, "chaptersに章のslugを1つ以上並べてください"),
        (len(set(slugs)) == len(slugs), "chaptersに同じ章が2度あります"),
        *(
            (
                _CHAPTER_SLUG.fullmatch(slug) is not None,
                f"章のslug「{slug}」は、a-z0-9・-・_の1〜50字にしてください",
            )
            for slug in slugs
        ),
    )
    return (*(message for valid, message in checks if not valid), *zenn.topic_problems(topics))


def cover_problems(directory: Path) -> tuple[str, ...]:
    """表紙cover.pngが、Zennの本の表紙に置けない所を全部返す。"""
    cover = directory / "cover.png"
    if not cover.is_file():
        return (f"表紙{cover.relative_to(SOURCE)}がありません",)
    png = cover.read_bytes()
    if png[:8] != b"\x89PNG\r\n\x1a\n":
        return (f"表紙{cover.relative_to(SOURCE)}がPNGではありません",)
    width, height = struct.unpack(">II", png[16:24])
    checks = (
        (len(png) <= COVER_LIMIT_BYTES, f"表紙は1MBまでにしてください(今は{len(png):,}バイト)"),
        (
            round(height / width, 1) == COVER_RATIO,
            f"表紙の幅と高さの比は1:1.4にしてください(今は{width}×{height})",
        ),
    )
    return tuple(message for valid, message in checks if not valid)


def read_rewrites(directory: Path) -> RewriteTable:
    """rewrites.json(鍵は原稿の名か「*」・値は{"from": 原稿の文, "to": 本の文}の並び)を読む。無ければ空。"""
    path = directory / "rewrites.json"
    raw = json.loads(path.read_text()) if path.is_file() else {}

    def rules(items: list[dict[str, str]]) -> tuple[Rewrite, ...]:
        """JSONの組の並びを、直す組の並びにするため。"""
        return tuple(Rewrite(item["from"], item["to"]) for item in items)

    return RewriteTable(
        common=rules(raw.get(EVERY_CHAPTER, [])),
        by_source=tuple(
            SourceRewrites(name, rules(items))
            for name, items in raw.items()
            if name != EVERY_CHAPTER
        ),
    )


def read_template(directory: Path, slug: str) -> Template | TemplateProblem:
    """章の雛形を読む。読めなければ、その訳を返す。"""
    path = directory / f"{slug}.md"
    if not path.is_file():
        return TemplateProblem(
            f"chaptersの章「{slug}」の雛形{path.relative_to(SOURCE)}がありません"
        )
    parsed = zenn.read_front(path.read_text())
    if parsed is None:
        return TemplateProblem(f"{path.relative_to(SOURCE)}にfront matterがありません")
    fields, body = parsed
    match fields:
        case {"source": str(source)} if len(fields) == 1 and (SOURCE / source).is_file():
            parts = body.split(f"\n{BODY_MARK}\n")
            if len(parts) != 2:
                return TemplateProblem(
                    f"{path.relative_to(SOURCE)}に、印の行「{BODY_MARK}」をちょうど1つ置いてください"
                )
            return Template(slug, source, parts[0].strip(), parts[1].strip())
    return TemplateProblem(
        f"{path.relative_to(SOURCE)}のfront matterは、在る原稿の名のsourceだけにしてください"
    )


def article_title(name: str) -> str | None:
    """原稿のfront matterのtitleを読む。無いか文字列でなければNoneを返す。"""
    parsed = zenn.read_front((SOURCE / name).read_text())
    title = parsed[0].get("title") if parsed is not None else None
    return title if isinstance(title, str) else None


def outside_code(text: str, change: Callable[[str], str]) -> str:
    """コードの枠の外だけを直す(コードの中の文字列は変えない)。"""
    parts = _CODE_FENCE.split(text)
    return "".join(part if part.startswith("```") else change(part) for part in parts)


def series_leftovers(text: str) -> tuple[str, ...]:
    """コードの枠の外に残った、連載の中でだけ通じる言葉を、前後の文字とともに返す。"""
    prose = _CODE_FENCE.sub("", text)
    return tuple(
        "連載の言葉「{}」が残っています: …{}…".format(
            found.group(0), prose[max(found.start() - 20, 0) : found.end() + 20].replace("\n", " ")
        )
        for word in SERIES_WORDS
        for found in re.finditer(word, prose)
    )


def build_chapter(
    template: Template,
    refs: tuple[ChapterRef, ...],
    table: RewriteTable,
    rows: tuple[release.PlanRow, ...],
) -> Chapter:
    """章の雛形と原稿から章のMarkdownを作り、食い違いを集める。refsは本の章の目録。"""
    ref = next(item for item in refs if item.source == template.source)
    parsed = zenn.read_front((SOURCE / template.source).read_text())
    if parsed is None or not 0 < len(ref.title) <= 70:
        return Chapter(
            template.slug,
            template.source,
            ref.number,
            ref.title,
            "",
            (),
            ("原稿のtitleが1〜70字ではありません",),
        )
    body = parsed[1]
    own = table.own(template.source)
    # 章だけに当てる組は、その章の原稿に無ければ止める(全部の章に当てる組は、本の単位で照らす)。
    missing = tuple(
        f"直す前の文「{rule.before[:40]}」が原稿にありません(原稿が変わったら、rewrites.jsonを直してください)"
        for rule in own
        if rule.before not in body
    )
    body = reduce(
        lambda text, rule: text.replace(rule.before, rule.after), (*table.common, *own), body
    )
    body = _SERIES_LINE.sub("", body).replace("この記事", "この章")

    def chapter_ref(match: re.Match[str]) -> str:
        """本の章を指すリンクを、章の番号と題にするため(本に無い記事のリンクは後で直す)。"""
        label, name = match.groups()
        target = next((item for item in refs if item.source == name), None)
        if target is None:
            return match.group(0)
        return f"第{target.number}章" if label == "章" else f"第{target.number}章「{target.title}」"

    text = f"{template.head}\n\n{body.strip()}\n\n{template.tail}\n"
    text = outside_code(text, lambda part: _CHAPTER_LINK.sub(chapter_ref, part))
    released = release.release_text(text, rows)
    problems = (*missing, *series_leftovers(released), *zenn.body_problems(released))
    markdown = f"---\ntitle: {json.dumps(ref.title, ensure_ascii=False)}\n---\n\n{released}"
    return Chapter(
        template.slug,
        template.source,
        ref.number,
        ref.title,
        markdown,
        zenn.image_targets(released),
        problems,
    )


def prepare_book(directory: Path, rows: tuple[release.PlanRow, ...]) -> Book:
    """本の正本1冊を、書き出す形にして食い違いを全部集める。"""
    slug = directory.name
    config = (directory / "config.yaml").read_text()
    fields = parse_config(config)
    if fields is None:
        return Book(
            slug,
            directory,
            config,
            (),
            (f"book {slug}: config.yamlを読めません(値をJSONと同じ書き方にしてください)",),
        )
    slugs = chapter_slugs(fields)
    loaded = tuple(read_template(directory, chapter) for chapter in slugs)
    templates = tuple(item for item in loaded if isinstance(item, Template))
    sources = tuple(template.source for template in templates)
    refs = tuple(
        ChapterRef(template.source, number, article_title(template.source) or "")
        for number, template in enumerate(templates, start=1)
    )
    table = read_rewrites(directory)
    chapters = tuple(build_chapter(template, refs, table, rows) for template in templates)
    problems = (
        *config_problems(fields),
        *cover_problems(directory),
        *(item.message for item in loaded if isinstance(item, TemplateProblem)),
        *(
            f"chaptersに無い章の雛形{path.name}があります"
            for path in sorted(directory.glob("*.md"))
            if path.stem not in slugs
        ),
        *(
            f"原稿{name}を2つの章にしています"
            for name in sorted(set(sources))
            if sources.count(name) > 1
        ),
        *(
            f"rewrites.jsonの鍵{item.source}は、本の章の原稿ではありません"
            for item in table.by_source
            if item.source not in sources
        ),
        # 全部の章に当てる組は、本のどこかの章の原稿に在ればよい。
        *(
            f"直す前の文「{rule.before[:40]}」がどの原稿にもありません(rewrites.jsonの「*」を直してください)"
            for rule in table.common
            if not any(rule.before in (SOURCE / name).read_text() for name in sources)
        ),
    )
    return Book(
        slug,
        directory,
        config,
        chapters,
        (
            *(f"book {slug}: {problem}" for problem in problems),
            *(
                f"book {slug}/{chapter.slug}: {problem}"
                for chapter in chapters
                for problem in chapter.problems
            ),
        ),
    )


def prepare_all() -> tuple[Book, ...]:
    """books/の下の本の正本を全部、書き出す形にする。"""
    rows = release.read_rows((SOURCE / "publication-plan.md").read_text())
    return tuple(prepare_book(path.parent, rows) for path in sorted(BOOKS.glob("*/config.yaml")))


def write(book: Book, output: Path) -> None:
    """食い違いの無い本1冊を、出力先のbooks/<本のslug>/と画像へ書く。"""
    directory = output / "books" / book.slug
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text(book.config)
    shutil.copyfile(book.directory / "cover.png", directory / "cover.png")
    for chapter in book.chapters:
        zenn.copy_images(chapter.images, output)
        (directory / f"{chapter.slug}.md").write_text(chapter.markdown)


def summary(book: Book) -> tuple[str, ...]:
    """書き出した本1冊の行(config.yamlと表紙、章ごとの出力先・原稿・画像の数・60字を越える文の数)。"""
    head = f"books/{book.slug}/config.yaml・cover.png(published: false・price 0・章 {len(book.chapters)})"
    lines = tuple(
        f"books/{book.slug}/{chapter.slug}.md ← {chapter.source}(第{chapter.number}章・画像 {len(chapter.images)}枚・"
        f"{release.SENTENCE_LIMIT}字を越える文 {len(release.measure(chapter.markdown).long_sentences)}個)"
        for chapter in book.chapters
    )
    return (head, *lines)


def main() -> None:
    """本の正本を全部書き出し、書き出した本の一覧か、全部の食い違いを表示するため。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="Zennの本を置くリポジトリの根(無ければ作る)")
    args = parser.parse_args()
    books = prepare_all()
    problems = tuple(problem for book in books for problem in book.problems)
    if problems or not books:
        print("\n".join(problems or ("books/の下に、config.yamlのある本の正本がありません",)))
        sys.exit(1)
    for book in books:
        write(book, args.output)
        print("\n".join(summary(book)))


if __name__ == "__main__":
    main()
