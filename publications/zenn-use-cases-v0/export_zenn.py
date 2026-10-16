"""公開計画の「公開順」の表の記事を、ZennのGitHub連携に置けるarticles/とimages/の組へ書き出す。

正本(このディレクトリのdoeff-*.md)と公開計画の表から、出力先に次の組を作る。

- articles/<slug>.md: 名は表のZennのslug。本文はrender_release.pyが作る公開する本文。
  front matterはtitle・emoji・type・topicsを正本から写し、publishedを表の公開状態から決める
  (公開済みだけtrue、公開予定はfalse。公開済みの記事を書き出し直しても非公開に戻さない)。
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
    """書き出す前の記事1本(原稿・slug・公開設定・記事のMarkdown・画像・食い違い)。"""

    name: str
    slug: str
    published: bool
    article: str
    images: tuple[str, ...]
    problems: tuple[str, ...]


def read_front(source: str) -> tuple[dict[str, object], str] | None:
    """正本をfront matterの項目と本文へ分ける。front matterが無ければNoneを返す。"""
    found = _FRONT.match(source)
    if found is None:
        return None
    # 正本の値は、文字列・配列・真偽値ともJSONと同じ書き方にしてある。
    pairs = (line.partition(":") for line in found.group(1).splitlines())
    fields = {key.strip(): json.loads(value.strip()) for key, _, value in pairs}
    return fields, source[found.end() :]


def front_problems(fields: dict[str, object]) -> tuple[str, ...]:
    """front matterがZennの規則(zenn-modelの検査の重大な項目)に合わない所を全部返す。"""
    title, emoji, kind, topics = (fields.get(key) for key in FRONT_KEYS[:4])
    topic_list = topics if isinstance(topics, list) else []
    checks = (
        (tuple(fields) == FRONT_KEYS, f"front matterの項目は{FRONT_KEYS}の順にしてください"),
        (isinstance(title, str) and 0 < len(title) <= 70, "titleは1〜70字の文字列にしてください"),
        (isinstance(emoji, str) and len(emoji) > 0, "emojiを1つ指定してください"),
        (kind in ("tech", "idea"), "typeはtechかideaにしてください"),
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


def typed_front(fields: dict[str, object]) -> Front | None:
    """JSONで読んだfront matterを型のある項目へ移す。型が合わなければNoneを返す。"""
    match fields:
        case {"title": str(title), "emoji": str(emoji), "type": str(kind), "topics": [*topics]}:
            names = tuple(topic for topic in topics if isinstance(topic, str))
            return Front(title, emoji, kind, names) if len(names) == len(topics) else None
    return None


def zenn_article(front: Front, body: str, published: bool) -> str:
    """Zennの記事のMarkdownを作る(publishedは正本でなく公開状態から決める)。"""
    head = (
        f"title: {json.dumps(front.title, ensure_ascii=False)}",
        f"emoji: {json.dumps(front.emoji, ensure_ascii=False)}",
        f"type: {json.dumps(front.kind)}",
        f"topics: {json.dumps(list(front.topics), ensure_ascii=False)}",
        f"published: {json.dumps(published)}",
    )
    return "---\n{}\n---\n{}".format("\n".join(head), body)


def prepare(row: release.PlanRow, rows: tuple[release.PlanRow, ...]) -> Prepared:
    """公開順の表の1行の記事を、書き出す形にして食い違いを集める。"""
    published = isinstance(row.state, release.Published)
    parsed = read_front((SOURCE / row.name).read_text())
    if parsed is None:
        return Prepared(row.name, row.slug, published, "", (), ("front matterがありません",))
    fields, body = parsed
    released = release.release_text(body, rows)
    images = tuple(_IMAGE_TARGET.findall(released))
    # 公開先から開けないリンク(リポジトリの中の相対パス)を残さない。
    stray = tuple(
        f"公開先から開けないリンク{target}が残っています"
        for target in _LINK_TARGET.findall(released)
        if not target.startswith(("https://", "http://", "#"))
    )
    problems = (
        *front_problems(fields),
        *(problem for target in images for problem in image_problems(target)),
        *stray,
    )
    front = typed_front(fields)
    if problems or front is None:
        return Prepared(row.name, row.slug, published, "", images, problems or ("型の誤り",))
    return Prepared(
        row.name, row.slug, published, zenn_article(front, released, published), images, ()
    )


def write(item: Prepared, output: Path) -> None:
    """食い違いの無い記事1本を、出力先のarticles/<slug>.mdと画像へ書く。"""
    for target in item.images:
        image = (ROOT / target.lstrip("/")).resolve()
        destination = output / target.lstrip("/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # 出力先がこのリポジトリの根なら、画像はすでに同じ場所にある。
        if destination.resolve() != image:
            shutil.copyfile(image, destination)
    (output / "articles").mkdir(parents=True, exist_ok=True)
    (output / "articles" / f"{item.slug}.md").write_text(item.article)


def main() -> None:
    """公開順の表の記事を書き出し、書き出した記事の一覧か、全部の食い違いを表示するため。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("output", type=Path, help="Zennの記事を置くリポジトリの根(無ければ作る)")
    args = parser.parse_args()
    rows = release.read_rows((SOURCE / "publication-plan.md").read_text())
    prepared = tuple(prepare(row, rows) for row in rows)
    problems = tuple(f"{item.name}: {problem}" for item in prepared for problem in item.problems)
    if not rows or problems:
        print("\n".join(problems or ("publication-plan.mdの「公開順」の表に記事がありません",)))
        sys.exit(1)
    for item in prepared:
        write(item, args.output)
        state = "true" if item.published else "false"
        print(
            f"articles/{item.slug}.md ← {item.name}(published: {state}・画像 {len(item.images)}枚)"
        )


if __name__ == "__main__":
    main()
