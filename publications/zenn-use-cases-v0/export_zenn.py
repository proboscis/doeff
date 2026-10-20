"""公開計画の「公開順」の表の記事を、ZennのGitHub連携に置けるarticles/とimages/の組へ書き出す。

正本(このディレクトリのdoeff-*.md)と公開計画の表から、出力先に次の組を作る。

- articles/<slug>.md: 名は表のZennのslug。本文はrender_release.pyが作る公開する本文。
  front matterはtitle・emoji・type・topicsを正本から写し、publishedを表の公開状態から決める
  (公開済みと、公開の刻を書いた公開予定はtrue、刻の無い公開予定はfalse。公開済みの記事を書き出し直しても
  非公開に戻さない)。公開の刻を書いた回はpublished_atに刻を書く — Zennはpublished: trueと未来の
  published_atの組を予約公開として受け、刻を過ぎると自分で公開する(Zennの公式ガイドの「予約公開」)。
- images/...: 本文が/images/...で参照する画像を、リポジトリのimages/から同じパスへ写す。

全記事の食い違い(front matter・画像・公開先から開けないリンク)を先に集め、1つでもあれば
何も書かずに全部を表示して止まる。出力先はZennの記事を置くリポジトリの根で、引数で受ける。
出力先の他のファイルは消さない。出力はzenn-check/check.mjs(zenn-cliのプレビューと
zenn-modelの検査)で確かめる。
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

import render_release as release

SOURCE = Path(__file__).resolve().parent
ROOT = SOURCE.parents[1]
# Zennのfront matterで書く項目と、その順。正本に他の項目があれば書き出さずに止める。
FRONT_KEYS = ("title", "emoji", "type", "topics", "published")
# ZennのGitHub連携で置ける画像の種類と、1枚の大きさの上限。
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
IMAGE_LIMIT_BYTES = 3_000_000
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_IMAGE_TARGET = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
_LINK_TARGET = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")
# zenn-modelの検査と同じく、topicsに使えないASCIIの記号と空白。
_TOPIC_SYMBOL = re.compile(r"[ -/:-@\[-`{-~]")


@dataclass(frozen=True)
class Front:
    """Zennのfront matterのうち、正本から写す項目(kindはfront matterのtype)。"""

    title: str
    emoji: str
    kind: str
    topics: tuple[str, ...]


@dataclass(frozen=True)
class Prepared:
    """書き出す前の記事1本(原稿・slug・公開設定・記事のMarkdown・画像・食い違い・公開の刻)。

    published_atはZennのfront matterのpublished_at(日本時間のYYYY-MM-DD hh:mm)。刻の無い回はNone。
    """

    name: str
    slug: str
    published: bool
    article: str
    images: tuple[str, ...]
    problems: tuple[str, ...]
    published_at: str | None = None


def read_front(source: str) -> tuple[dict[str, object], str] | None:  # noqa: DOEFF006 — 道具の小さな組の戻り
    """正本をfront matterの項目と本文へ分ける。front matterが無ければNoneを返す。"""
    found = _FRONT.match(source)
    if found is None:
        return None
    # 正本の値は、文字列・配列・真偽値ともJSONと同じ書き方にしてある。
    pairs = (line.partition(":") for line in found.group(1).splitlines())
    fields = {key.strip(): json.loads(value.strip()) for key, _, value in pairs}
    return fields, source[found.end() :]


def topic_problems(topics: object) -> tuple[str, ...]:
    """topicsがZennの規則(zenn-modelの検査の重大な項目)に合わない所を全部返す。記事と本で同じ規則。"""
    topic_list = topics if isinstance(topics, list) else []
    checks = (
        (isinstance(topics, list) and 0 < len(topics) <= 5, "topicsは1〜5個の配列にしてください"),
        *(
            (
                isinstance(topic, str) and 0 < len(topic) <= 18 and not _TOPIC_SYMBOL.search(topic),
                f"topic「{topic}」は、記号と空白の無い1〜18字にしてください",
            )
            for topic in topic_list
        ),
    )
    return tuple(message for valid, message in checks if not valid)


def front_problems(fields: dict[str, object]) -> tuple[str, ...]:
    """front matterがZennの規則(zenn-modelの検査の重大な項目)に合わない所を全部返す。"""
    title, emoji, kind, topics = (fields.get(key) for key in FRONT_KEYS[:4])
    checks = (
        (tuple(fields) == FRONT_KEYS, f"front matterの項目は{FRONT_KEYS}の順にしてください"),
        (isinstance(title, str) and 0 < len(title) <= 70, "titleは1〜70字の文字列にしてください"),
        (isinstance(emoji, str) and len(emoji) > 0, "emojiを1つ指定してください"),
        (kind in ("tech", "idea"), "typeはtechかideaにしてください"),
    )
    return (*(message for valid, message in checks if not valid), *topic_problems(topics))


def image_problems(target: str) -> tuple[str, ...]:
    """本文が参照する画像1枚が、Zennに置けない所を全部返す。"""
    if not target.startswith("/images/"):
        return (f"画像{target}は、/images/から始まるパスにしてください",)
    image = (ROOT / target.lstrip("/")).resolve()
    if not image.is_relative_to(ROOT / "images") or not image.is_file():
        return (f"画像{target}が、リポジトリのimages/にありません",)
    checks = (
        (image.suffix.lower() in IMAGE_SUFFIXES, f"画像{target}は、Zennで置けない種類です"),
        (
            image.stat().st_size <= IMAGE_LIMIT_BYTES,
            f"画像{target}は、Zennの上限の3MBを越えています",
        ),
    )
    return tuple(message for valid, message in checks if not valid)


def image_targets(released: str) -> tuple[str, ...]:
    """公開する本文が参照する画像のパスを、出てくる順に返す。"""
    return tuple(_IMAGE_TARGET.findall(released))


def body_problems(released: str) -> tuple[str, ...]:
    """公開する本文の、Zennに置けない画像と、公開先から開けないリンク(リポジトリの中の相対パス)を全部返す。"""
    stray = tuple(
        f"公開先から開けないリンク{target}が残っています"
        for target in _LINK_TARGET.findall(released)
        if not target.startswith(("https://", "http://", "#"))
    )
    return (
        *(problem for target in image_targets(released) for problem in image_problems(target)),
        *stray,
    )


def copy_images(images: tuple[str, ...], output: Path) -> None:
    """本文が参照する画像を、出力先の同じパスへ写す。"""
    for target in images:
        image = (ROOT / target.lstrip("/")).resolve()
        destination = output / target.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # 出力先がこのリポジトリの根なら、画像はすでに同じ場所にある。
        if destination.resolve() != image:
            shutil.copyfile(image, destination)


def typed_front(fields: dict[str, object]) -> Front | None:
    """JSONで読んだfront matterを型のある項目へ移す。型が合わなければNoneを返す。"""
    match fields:
        case {"title": str(title), "emoji": str(emoji), "type": str(kind), "topics": [*topics]}:
            names = tuple(topic for topic in topics if isinstance(topic, str))
            return Front(title, emoji, kind, names) if len(names) == len(topics) else None
    return None


def zenn_article(front: Front, body: str, published: bool, published_at: str | None = None) -> str:
    """Zennの記事のMarkdownを作る(publishedとpublished_atは正本でなく公開状態から決める)。"""
    head = (
        f"title: {json.dumps(front.title, ensure_ascii=False)}",
        f"emoji: {json.dumps(front.emoji, ensure_ascii=False)}",
        f"type: {json.dumps(front.kind)}",
        f"topics: {json.dumps(list(front.topics), ensure_ascii=False)}",
        f"published: {json.dumps(published)}",
        # Zennの公式ガイドと同じく、引用符を付けずに書く(秒の無い日時はYAMLで文字列に読まれる)。
        *((f"published_at: {published_at}",) if published_at else ()),
    )
    return "---\n{}\n---\n{}".format("\n".join(head), body)


def published_at_of(state: release.State) -> str | None:
    """公開状態の公開の刻を、Zennのpublished_atの形にする。刻の無い回はNoneを返す。"""
    return release.zenn_time(state.at) if state.at else None


def prepare(row: release.PlanRow, rows: tuple[release.PlanRow, ...]) -> Prepared:
    """公開順の表の1行の記事を、書き出す形にして食い違いを集める。"""
    at = published_at_of(row.state)
    # 公開の刻の無い公開予定だけが非公開。刻を書いた公開予定は、Zennが刻に公開する予約になる。
    published = isinstance(row.state, release.Published) or at is not None
    parsed = read_front((SOURCE / row.name).read_text())
    if parsed is None:
        return Prepared(row.name, row.slug, published, "", (), ("front matterがありません",), at)
    fields, body = parsed
    released = release.release_text(body, rows)
    images = image_targets(released)
    problems = (*front_problems(fields), *body_problems(released))
    front = typed_front(fields)
    if problems or front is None:
        return Prepared(row.name, row.slug, published, "", images, problems or ("型の誤り",), at)
    article = zenn_article(front, released, published, at)
    return Prepared(row.name, row.slug, published, article, images, (), at)


def write(item: Prepared, output: Path) -> None:
    """食い違いの無い記事1本を、出力先のarticles/<slug>.mdと画像へ書く。"""
    copy_images(item.images, output)
    (output / "articles").mkdir(parents=True, exist_ok=True)
    (output / "articles" / f"{item.slug}.md").write_text(item.article)


@dataclass(frozen=True)
class Export:
    """公開順の表の全記事を書き出す形にした物と、全記事の食い違い(1つでもあれば何も書かない)。"""

    items: tuple[Prepared, ...]
    problems: tuple[str, ...]


def prepare_all() -> Export:
    """公開順の表の全記事を書き出す形にし、食い違いを全部集める。"""
    rows = release.read_rows((SOURCE / "publication-plan.md").read_text())
    items = tuple(prepare(row, rows) for row in rows)
    problems = tuple(f"{item.name}: {problem}" for item in items for problem in item.problems)
    empty = () if rows else ("publication-plan.mdの「公開順」の表に記事がありません",)
    return Export(items, (*problems, *empty))


def summary(item: Prepared) -> str:
    """書き出した記事1本の1行(出力先のファイル・原稿・公開設定・画像の数)。"""
    state = "true" if item.published else "false"
    at = f"・published_at: {item.published_at}" if item.published_at else ""
    return (
        f"articles/{item.slug}.md ← {item.name}(published: {state}{at}・画像 {len(item.images)}枚)"
    )


def main() -> None:
    """公開順の表の記事を書き出し、書き出した記事の一覧か、全部の食い違いを表示するため。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="Zennの記事を置くリポジトリの根(無ければ作る)")
    args = parser.parse_args()
    export = prepare_all()
    if export.problems:
        print("\n".join(export.problems))
        sys.exit(1)
    for item in export.items:
        write(item, args.output)
        print(summary(item))


if __name__ == "__main__":
    main()
