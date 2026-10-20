"""正本の原稿から公開する本文を作り、書き方の決まりの数を測る。

正本(このディレクトリのdoeff-*.md)は、編集と確認のため全記事への相互リンクを保つ。
公開する本文では、publication-plan.mdの「公開順」の表の公開状態に従ってリンクを直す。

- 公開済み: 表に書いたZennのURLへリンクする。
- 公開予定: リンクを外し、題に「(公開予定)」を添える。公開の刻を書いた回(予約公開)も、表を公開済みに
  直すまではリンクしない(刻の前にリンクすると、読者が開けないページへ案内する)。
- 下書きの記事と表に無い記事(候補): リンクを外し、題だけを残す。
- examples/などの相対パス: GitHubのmainのURLへ直す。画像の/images/はそのまま残す。
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SOURCE = Path(__file__).resolve().parent
GITHUB_BLOB = "https://github.com/proboscis/doeff/blob/main/publications/zenn-use-cases-v0/"
SENTENCE_LIMIT = 60
# 本文の字数は、コードの枠・画像・リンク先・Markdownの記号・空白を数えない。
_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.S)
_FENCE = re.compile(r"^```.*?^```[ \t]*$", re.S | re.M)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_ARTICLE_LINK = re.compile(r"\[([^\]]+)\]\((doeff-[a-z]+\.md)(#[^)]*)?\)")
# 「公開順」の表の1行: | 週 | [題](原稿) | `slug` | 読者への入口 | 公開状態 |
STATE_ROW = re.compile(
    r"^\|[^|]*\|\s*\[[^\]]+\]\((doeff-[a-z]+\.md)\)\s*\|\s*`([^`|]*)`\s*\|[^|]*\|\s*([^|]+?)\s*\|\s*$"
)
# Zennの記事のslug(articles/<slug>.mdのファイル名で、公開後のURLの末尾)。zenn-cliの検査と同じ規則。
_ZENN_SLUG = re.compile(r"[0-9a-z_-]{12,50}")
# 公開状態の欄の公開の刻(Zennのpublished_atと同じく日本時間)。「公開予定 2026-10-21 08:00 JST」の形で書く。
JST = timezone(timedelta(hours=9))
_RESERVED_AT = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}) JST")
# 文の終わりの記号(句点・全角の感嘆符と疑問符)と、その前に来る閉じ括弧。
_SENTENCE_END = "\u3002\uff01\uff1f"
_CLOSERS = "\u300d\u300f)\uff09"
_POLITE_END = re.compile(
    rf"(です|ます|ません|でした|ました|ください|でしょう|ましょう)か?[{_CLOSERS}]*[{_SENTENCE_END}]$"
)


@dataclass(frozen=True)
class Published:
    """公開済みの記事。urlはZennの実際のURL。atは予約公開で出した回の公開の刻(手で出した回はNone)。"""

    url: str
    at: datetime | None = None


@dataclass(frozen=True)
class Scheduled:
    """公開予定の記事。URLはまだ無い。atは公開の刻(予約公開 — 刻を書いていない回はNoneで、予約しない)。"""

    at: datetime | None = None


State = Published | Scheduled


@dataclass(frozen=True)
class PlanRow:
    """公開計画の「公開順」の表の1行(原稿のファイル名・Zennのslug・公開状態・属する連載)。

    seriesは、表の上の最も近い「### 」の見出しの名(doeffの連載・AIエージェントの連載)。前後の回は連載ごとに並ぶ。
    """

    name: str
    slug: str
    state: State
    series: str = ""


@dataclass(frozen=True)
class Measures:
    """書き方の決まりのうち、数で確かめられる項目の測った値。"""

    body_chars: int
    code_blocks: int
    images: int
    long_sentences: tuple[str, ...]
    plain_endings: tuple[str, ...]


def read_rows(plan: str) -> tuple[PlanRow, ...]:
    """公開計画の「公開順」の表から、原稿ごとのslugと公開状態を読む。下書きの行と表に無い原稿は候補になる。

    slugがZennの規則に合わない行と、公開済みのURLの末尾がslugと違う行は、誤った公開先へ
    リンクしないよう例外にする。公開状態が公開済み<URL>・公開予定・下書きのどれでもない行も、
    書き損じを候補として黙って読まないよう例外にする。
    """
    rows: list[PlanRow] = []
    series = ""
    for line in plan.splitlines():
        if line.startswith("### "):
            series = line.removeprefix("### ").strip()  # この見出しの下の表の行は、この連載の回。
        row = STATE_ROW.match(line)
        if row is None:
            continue
        name, slug, cell = row.groups()
        if not _ZENN_SLUG.fullmatch(slug):
            raise ValueError(f"{name}のslug「{slug}」は、a-z0-9・-・_の12〜50字にしてください")
        url = re.search(r"https://zenn\.dev/\S+", cell)
        if cell.startswith("公開済み") and url:
            if not url.group(0).endswith(f"/articles/{slug}"):
                raise ValueError(f"{name}の公開済みのURLの末尾がslug「{slug}」と違います")
            at = reserved_at(cell[url.end() :], name)
            rows.append(PlanRow(name, slug, Published(url.group(0), at), series))
        elif cell.startswith("公開予定"):
            at = reserved_at(cell.removeprefix("公開予定"), name)
            rows.append(PlanRow(name, slug, Scheduled(at), series))
        elif not cell.startswith("下書き"):
            raise ValueError(
                f"{name}の公開状態「{cell}」は、公開済み <ZennのURL>・公開予定・下書きのどれかにしてください"
            )
    return tuple(rows)


def reserved_at(text: str, name: str) -> datetime | None:
    """公開状態の欄の、状態とURLの後ろに書いた公開の刻を読む。無ければNoneを返す。

    刻の書き損じを「刻なし」と黙って読むと、予約したつもりの回が出ないので、形が違えば例外にする。
    """
    rest = text.strip()
    if not rest:
        return None
    found = _RESERVED_AT.fullmatch(rest)
    if found is None:
        raise ValueError(
            f"{name}の公開の刻「{rest}」は、「YYYY-MM-DD hh:mm JST」の形で書いてください"
        )
    return datetime.strptime(found.group(1), "%Y-%m-%d %H:%M").replace(tzinfo=JST)


def zenn_time(at: datetime) -> str:
    """公開の刻を、Zennのpublished_atの形(日本時間のYYYY-MM-DD hh:mm)にする。"""
    return at.astimezone(JST).strftime("%Y-%m-%d %H:%M")


def state_of(rows: tuple[PlanRow, ...], name: str) -> State | None:
    """原稿の公開状態を引く。表に無い原稿(候補)はNoneを返す。"""
    return next((row.state for row in rows if row.name == name), None)


def release_text(source: str, rows: tuple[PlanRow, ...]) -> str:
    """正本の本文を、公開状態に合わせたリンクの本文へ直す。"""

    def article_link(match: re.Match[str]) -> str:
        """記事間のリンクを、相手の記事の公開状態に合わせて直すため。"""
        title, name, _anchor = match.groups()
        state = state_of(rows, name)
        if isinstance(state, Published):
            return f"[{title}]({state.url})"
        if isinstance(state, Scheduled):
            return f"{title}(公開予定)"
        return title

    def relative_link(match: re.Match[str]) -> str:
        """完全な例などへの相対リンクを、公開先からも開けるGitHubのURLへ直すため。"""
        title, target = match.groups()
        if target.startswith(("https://", "http://", "#", "/")):
            return match.group(0)
        return f"[{title}]({GITHUB_BLOB}{target})"

    def outside_code(text: str) -> str:
        """コードの枠の外だけを直すため(コードの中の文字列は変えない)。"""
        text = _ARTICLE_LINK.sub(article_link, text)
        return re.sub(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)", relative_link, text)

    parts = re.split(r"(^```.*?^```[ \t]*$)", source, flags=re.S | re.M)
    return "".join(part if part.startswith("```") else outside_code(part) for part in parts)


def visible_text(source: str) -> str:
    """読者に見える本文の文字だけを残す(コードの枠・画像・リンク先・記号を除く)。"""
    text = _FRONT_MATTER.sub("", source)
    text = _FENCE.sub("", text)
    text = _IMAGE.sub("", text)
    text = _LINK.sub(r"\1", text)
    lines = [line for line in text.splitlines() if not re.fullmatch(r"\|?[\s|:-]+\|?", line)]
    text = "\n".join(re.sub(r"^\s*(#+|[-*]|\d+\.|>)\s+", "", line) for line in lines)
    return re.sub(r"\*\*|`", "", text)


def units(source: str) -> list[str]:
    """1文ずつの単位に分ける。表のセルと箇条書きの1項も1単位とする。"""
    found: list[str] = []
    for line in visible_text(source).splitlines():
        cells = [cell.strip() for cell in line.split("|")] if "|" in line else [line.strip()]
        for cell in cells:
            pieces = re.findall(rf"[^{_SENTENCE_END}]+[{_SENTENCE_END}]?", cell)
            found.extend(s.strip() for s in pieces if s.strip())
    return found


def measure(source: str) -> Measures:
    """本文の字数・コードの枠・画像の数と、60字を越える文・です・ます以外で終わる文を測る。"""
    body = re.sub(r"\s", "", visible_text(source))
    sentences = units(source)
    # 表のセルや箇条書きの名詞句は文ではないので、句点で終わる単位だけを口調の対象にする。
    plain = tuple(s for s in sentences if s.endswith("。") and not _POLITE_END.search(s))
    return Measures(
        body_chars=len(body),
        code_blocks=len(re.findall(r"^```\w", _FRONT_MATTER.sub("", source), re.M)),
        images=len(_IMAGE.findall(source)),
        long_sentences=tuple(s for s in sentences if len(s) > SENTENCE_LIMIT),
        plain_endings=plain,
    )


def main() -> None:
    """原稿1本の公開する本文を書き出し、決まりの数を表示するため。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("article", help="原稿のファイル名(例: doeff-main.md)")
    parser.add_argument("--output", type=Path, help="公開する本文を書き出す先")
    args = parser.parse_args()
    rows = read_rows((SOURCE / "publication-plan.md").read_text())
    released = release_text((SOURCE / args.article).read_text(), rows)
    if args.output:
        args.output.write_text(released)
    result = measure(released)
    remaining = sorted(set(re.findall(r"\]\((doeff-[a-z]+\.md)", released)))
    print(f"本文 {result.body_chars:,}字(コードの枠・画像・リンク先・記号・空白を除く)")
    print(f"コードの枠 {result.code_blocks}個・画像 {result.images}枚")
    print(f"{SENTENCE_LIMIT}字を越える文 {len(result.long_sentences)}個")
    for sentence in result.long_sentences:
        print(f"  {len(sentence)}字: {sentence}")
    print(f"です・ます以外で終わる文 {len(result.plain_endings)}個")
    for sentence in result.plain_endings:
        print(f"  {sentence}")
    print(f"記事間の相対リンク {len(remaining)}個")


if __name__ == "__main__":
    main()
