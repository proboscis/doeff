"""公開の刻を過ぎた回を、Zennで開けることを確かめてから、公開順の表で「公開済み <URL> <刻>」に直す。

公開は予約で行う(push_zenn_branch.pyが、公開の刻を書いた回をpublished: trueとpublished_atの組で置き、
Zennが刻に公開する)。この命令は刻の後に走らせ、publication-plan.mdの公開状態を直す。

- 直すのは、公開の刻を書いた公開予定の行のうち、刻が今(--now)以前で、Zennの記事のURLが開ける回だけ。
  刻の前の回と、刻を過ぎてもZennで開けない回は直さない(開けない回は表示して、終わりのcodeを1にする)。
- 公開の刻は「公開済み <URL> <刻>」の形で残す。書き出しは同じpublished_atを書き続け、Zennの公開日時を
  変えない。
- 表を直した後は、表をmainに入れてからpush_zenn_branch.pyを走らせる。他の回の本文の、直した回への
  リンクが、ZennのURLへのリンクになる。
"""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import render_release as release
from render_release import JST

SUMMARY = "公開の刻を過ぎた回を、Zennで開けることを確かめてから、公開順の表で公開済みに直す。"
PLAN = release.SOURCE / "publication-plan.md"
# Zennの記事のURLの頭(公開後のURLは、この後ろにslugが付く)。
ZENN_ARTICLES = "https://zenn.dev/proboscis/articles/"


@dataclass(frozen=True)
class Recorded:
    """公開済みに直した回1つ(原稿・slug・URL・公開の刻)。"""

    name: str
    slug: str
    url: str
    at: datetime


@dataclass(frozen=True)
class Unreachable:
    """公開の刻を過ぎたのに、Zennで開けない回1つ(原稿・URL・公開の刻)。"""

    name: str
    url: str
    at: datetime


@dataclass(frozen=True)
class Line:
    """表の文の1行を直した結果(直した後の行と、直した回か開けなかった回 — どちらでもない行はNone)。"""

    text: str
    recorded: Recorded | None = None
    unreachable: Unreachable | None = None


@dataclass(frozen=True)
class Record:
    """表を直した結果(直した後の表の文・直した回・開けなかった回)。"""

    plan: str
    recorded: tuple[Recorded, ...]
    unreachable: tuple[Unreachable, ...]


def opens(url: str) -> bool:
    """URLを開き、200で答えればTrueを返す(Zennは予約の刻の前の記事に404を返す)。"""
    request = urllib.request.Request(url, headers={"User-Agent": "doeff-record-published"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status == 200
    except urllib.error.URLError:
        return False


def published_cell(url: str, at: datetime) -> str:
    """公開済みに直した公開状態の欄の文(公開の刻を残す)。"""
    return f"公開済み {url} {release.zenn_time(at)} JST"


def record_line(
    line: str, rows: tuple[release.PlanRow, ...], now: datetime, reachable: Callable[[str], bool]
) -> Line:
    """表の文の1行が、刻がnow以前の公開予定の行なら、Zennで開けることを確かめて公開済みに直す。"""
    found = release.STATE_ROW.match(line.rstrip("\n"))
    row = next((row for row in rows if found and row.slug == found.group(2)), None)
    at = row.state.at if row and isinstance(row.state, release.Scheduled) else None
    if found is None or row is None or at is None or at > now:
        return Line(line)
    url = ZENN_ARTICLES + row.slug
    if not reachable(url):
        return Line(line, unreachable=Unreachable(row.name, url, at))
    start, end = found.span(3)
    edited = line[:start] + published_cell(url, at) + line[end:]
    return Line(edited, recorded=Recorded(row.name, row.slug, url, at))


def record(plan: str, now: datetime, reachable: Callable[[str], bool]) -> Record:
    """表の文planのうち、刻がnow以前でZennで開ける公開予定の行を、公開済みに直す。

    reachableはURLが開けるかを答える(本番はopens・確かめでは偽の答え)。
    """
    rows = release.read_rows(plan)
    lines = tuple(
        record_line(line, rows, now, reachable) for line in plan.splitlines(keepends=True)
    )
    return Record(
        "".join(line.text for line in lines),
        tuple(line.recorded for line in lines if line.recorded),
        tuple(line.unreachable for line in lines if line.unreachable),
    )


def main() -> None:
    """刻を過ぎた回を公開済みに直し、直した回と開けなかった回を表示するため。"""
    parser = argparse.ArgumentParser(description=SUMMARY)
    parser.add_argument(
        "--now",
        type=datetime.fromisoformat,
        default=None,
        help="公開の刻と比べる今(時差つきのISO 8601・既定は今の刻)",
    )
    parser.add_argument("--dry-run", action="store_true", help="直す回を表示し、表を書かない")
    args = parser.parse_args()
    now = args.now or datetime.now(JST)
    now = now if now.tzinfo else now.replace(tzinfo=JST)
    result = record(PLAN.read_text(), now, opens)
    for done in result.recorded:
        print(f"{done.name}: 公開済み {done.url}(公開の刻 {release.zenn_time(done.at)} JST)")
    for missing in result.unreachable:
        print(
            f"{missing.name}: 公開の刻 {release.zenn_time(missing.at)} JST を過ぎたのに、"
            f"{missing.url} が開けません(zenn-contentの予約とZennのデプロイの結果を確かめてください)"
        )
    if not result.recorded and not result.unreachable:
        print("公開の刻を過ぎた公開予定の回はありません")
    if result.recorded and not args.dry_run:
        Path(PLAN).write_text(result.plan)
    sys.exit(1 if result.unreachable else 0)


if __name__ == "__main__":
    main()
