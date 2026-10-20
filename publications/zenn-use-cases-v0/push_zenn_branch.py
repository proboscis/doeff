"""公開順の表の記事を書き出し、記事だけを置くorphan branch(ZennのGitHub連携の先)へpushする。

ZennのGitHub連携は、このリポジトリの記事だけのbranch(既定zenn-content)を読む。branchの根には
articles/とimages/だけを置く。この命令はexport_zenn.pyの書き出しを一時ディレクトリに作り、その木を
branchの先端の上の1つのcommitにしてpushする。mainの木も、この作業木も変えない。1本を足す時も、
原稿と公開順の表の行をmainに入れてから、この命令を走らせる。

- 公開は予約で行う: 公開順の表に公開の刻を書いた回(「公開予定 2026-10-21 08:00 JST」)は、
  published: trueとpublished_atの組で置く。Zennはこの組を予約公開として受け、刻を過ぎると自分で公開する。
  刻の後に、公開順の表の公開状態を「公開済み <URL> <刻>」に直す(record_published.py)。
- 読者に見えている回を変えない: branchでpublishedがtrueで、公開の刻が無いか過ぎた記事は、publishedと
  published_atがbranchのままでなければ止まる(公開済みの記事を非公開に戻さず、公開日時を変えない)。
- その場で公開しない: 刻の前の記事(published: falseか、未来のpublished_at)は、published: falseか
  未来の刻の予約にだけ変えられる。published: trueで刻が無いか過ぎた書き出しは止まる。予約の刻を変える・
  予約を解く(刻を消す)のは、刻の前にだけできる。
- 予約を置き去りにしない: branchで公開済みか予約の在る記事が公開順の表に無ければ止まる。Zennは記事の
  削除をダッシュボードからしか受けないので、branchから外しても予約は残る。
- 変わった記事が無ければpushしない。原稿か画像に未commitの変更があれば止まる(branchのcommitは、
  書き出したcommitを名乗る)。
- --resync: Zennは連携の後のpushで変わった.mdだけを同期する。連携の直後に1度、記事を外すcommitと
  置き直すcommitを別々のpushで送り、全記事を同期させる。Zennは記事の削除をダッシュボードからしか
  受けないので、外すpushでZennの記事は消えない。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import export_zenn
from render_release import JST

ROOT = export_zenn.ROOT
# 書き出しの元。未commitの変更があれば、branchのcommitが名乗る元と中身が食い違う。
SOURCES = ("publications/zenn-use-cases-v0", "images")
# branchの記事はユーザーもGitHubで直すので、front matterからpublishedとpublished_atの行だけを読む。
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_PUBLISHED = re.compile(r"^published:\s*(true|false)\s*$", re.M)
_PUBLISHED_AT = re.compile(r"^published_at:\s*[\"']?([^\"'\n]*?)[\"']?\s*$", re.M)
# Zennのpublished_atの形(日本時間)。日付だけの時は、Zennと同じく0時と読む。
_ZENN_TIME = re.compile(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2})?")


@dataclass(frozen=True)
class Placed:
    """branchに置いた記事1本の公開設定(published — 読めなければNone・published_at — 無ければNone)。"""

    published: bool | None
    published_at: str | None


@dataclass(frozen=True)
class Branch:
    """pushする先のbranchの先端(commitと、記事のslugで引く公開設定)。"""

    tip: str
    placed: dict[str, Placed]


@dataclass(frozen=True)
class Push:
    """branchへ送るcommit 1つと、その1行の説明。"""

    sha: str
    note: str


def git(
    *args: str, env: dict[str, str] | None = None, cwd: Path = ROOT, stdin: str | None = None
) -> str:
    """gitを1回走らせて標準出力を返す。失敗したら、gitのエラーを表示して止まる。"""
    done = subprocess.run(
        ("git", *args), cwd=cwd, env=env, input=stdin, capture_output=True, text=True, check=False
    )
    if done.returncode != 0:
        sys.exit(f"git {' '.join(args)} が失敗しました:\n{done.stderr.strip()}")
    return done.stdout.strip()


def placed_of(article: str) -> Placed:
    """記事のfront matterのpublishedとpublished_atを読む。読めない項目はNoneにする。"""
    front = _FRONT.match(article)
    published = _PUBLISHED.search(front.group(1)) if front else None
    published_at = _PUBLISHED_AT.search(front.group(1)) if front else None
    return Placed(
        published.group(1) == "true" if published else None,
        published_at.group(1) if published_at and published_at.group(1) else None,
    )


def zenn_moment(published_at: str) -> datetime | None:
    """published_atを日本時間の刻にする。Zennの形でなければNoneを返す。"""
    if not _ZENN_TIME.fullmatch(published_at):
        return None
    layout = "%Y-%m-%d %H:%M" if " " in published_at else "%Y-%m-%d"
    return datetime.strptime(published_at, layout).replace(tzinfo=JST)


def visible(published: bool | None, published_at: str | None, now: datetime) -> bool:
    """その公開設定の記事が、nowにZennで読者に見えているか(trueで、刻が無いか過ぎている)。"""
    if published is not True:
        return False
    moment = zenn_moment(published_at) if published_at else None
    return published_at is None or (moment is not None and moment <= now)


def read_branch(remote: str, name: str, repo: Path = ROOT) -> Branch | None:
    """remoteのbranchを取り寄せ、記事ごとの公開設定を読む。branchが無ければNoneを返す。"""
    if not git("ls-remote", "--heads", remote, f"refs/heads/{name}", cwd=repo):
        return None
    git("fetch", "--no-tags", remote, f"refs/heads/{name}", cwd=repo)
    tip = git("rev-parse", "FETCH_HEAD", cwd=repo)
    # --resyncの途中で止まったbranchにはarticles/が無いので、木の名指しでなくpathで引く。
    paths = git("ls-tree", "--name-only", tip, "articles/", cwd=repo).splitlines()
    placed = {
        path.removeprefix("articles/").removesuffix(".md"): placed_of(
            git("show", f"{tip}:{path}", cwd=repo)
        )
        for path in paths
        if path.endswith(".md")
    }
    return Branch(tip, placed)


def item_problem(item: export_zenn.Prepared, before: Placed | None, now: datetime) -> str | None:
    """書き出す記事1本が、branchの公開設定(beforeはbranchの値・無ければNone)を変えてはいけない所の1行。"""
    path = f"articles/{item.slug}.md"
    placed_at = before.published_at if before else None
    # branchで読者に見えている記事(公開済みか、予約の刻を過ぎた記事)と、書き出しがそれを保つか。
    shown = before is not None and visible(before.published, placed_at, now)
    kept = (item.published, item.published_at) == (True, placed_at)
    checks = (
        (
            item.published_at is not None and zenn_moment(item.published_at) is None,
            f"{path}: published_at「{item.published_at}」はZennの形(YYYY-MM-DD hh:mm)でありません",
        ),
        (before is not None and before.published is None, f"{path}: branchのpublishedが読めません"),
        (
            placed_at is not None and zenn_moment(placed_at) is None,
            f"{path}: branchのpublished_at「{placed_at}」が読めません",
        ),
        (
            shown and not kept,
            f"{path}: Zennで公開済みです(published_at {placed_at or 'なし'})。"
            "非公開に戻さず、公開日時も変えません。公開順の表の公開状態を「公開済み <URL>」にし、"
            "公開の刻を書いた回は同じ刻のままにしてください",
        ),
        (
            not shown and visible(item.published, item.published_at, now),
            f"{path}: この命令は記事をその場で公開しません。"
            "公開は、公開順の表に未来の公開の刻を書いて予約してください",
        ),
    )
    return next((message for failed, message in checks if failed), None)


def published_problems(
    items: tuple[export_zenn.Prepared, ...], branch: dict[str, Placed], now: datetime
) -> tuple[str, ...]:
    """書き出しがbranchの記事の公開設定を変えてはいけない所を全部返す(nowは公開の刻と比べる今)。"""
    exported = {item.slug for item in items}
    changed = tuple(
        problem
        for item in items
        if (problem := item_problem(item, branch.get(item.slug), now)) is not None
    )
    dropped = tuple(
        f"articles/{slug}.md: branchで公開済みか予約の在る記事が、公開順の表にありません"
        "(branchから外しても、Zennの公開と予約は残ります)"
        for slug, placed in branch.items()
        if placed.published and slug not in exported
    )
    return (*changed, *dropped)


def tree_of(directory: Path, repo: Path = ROOT) -> str:
    """ディレクトリの中身を、repoのgitの木にして、その名を返す。"""
    git_dir = git("rev-parse", "--absolute-git-dir", cwd=repo)
    with tempfile.TemporaryDirectory() as scratch:
        env = {
            **os.environ,  # noqa: DOEFF004 — 道具が子の git へ今の環境を引き継ぐ 1 か所
            "GIT_DIR": git_dir,
            "GIT_WORK_TREE": str(directory),
            "GIT_INDEX_FILE": str(Path(scratch) / "index"),
        }
        git("add", "--all", "--force", ".", env=env, cwd=directory)
        return git("write-tree", env=env, cwd=directory)


def without_articles(tree: str, repo: Path = ROOT) -> str:
    """木からarticles/を外した木の名を返す(--resyncの1つ目のcommit)。"""
    entries = git("ls-tree", tree, cwd=repo).splitlines()
    kept = "".join(f"{entry}\n" for entry in entries if not entry.endswith("\tarticles"))
    return git("mktree", stdin=kept, cwd=repo)


def commit(tree: str, parent: str | None, message: str, repo: Path = ROOT) -> str:
    """木を1つのcommitにして、その名を返す(parentが無ければorphanの根のcommit)。"""
    parents = ("-p", parent) if parent else ()
    return git("commit-tree", tree, *parents, "-m", message, cwd=repo)


def plan(
    tree: str, branch: Branch | None, source: str, resync: bool, repo: Path = ROOT
) -> tuple[Push, ...]:
    """branchへ送るcommitを送る順に作る。変わった記事が無く--resyncも無ければ、空を返す。"""
    origin = f"publications/zenn-use-cases-v0/push_zenn_branch.pyが、doeff {source}から書き出した。"
    if branch is None:
        first = commit(tree, None, f"Zennの記事を置くbranchを作る\n\n{origin}", repo)
        return (Push(first, "記事を置くbranchを作る"),)
    if not resync:
        if git("rev-parse", f"{branch.tip}^{{tree}}", cwd=repo) == tree:
            return ()
        update = commit(tree, branch.tip, f"Zennの記事を書き出し直す\n\n{origin}", repo)
        return (Push(update, "記事を書き出し直す"),)
    message = "Zennに全記事を同期し直すため、記事を一度外す(次のcommitで置き直す)"
    emptied = commit(without_articles(tree, repo), branch.tip, message, repo)
    restored = commit(
        tree, emptied, f"Zennに全記事を同期し直すため、記事を置き直す\n\n{origin}", repo
    )
    return (Push(emptied, "記事を一度外す"), Push(restored, "記事を置き直す"))


def send(push: Push, remote: str, name: str, repo: Path = ROOT) -> None:
    """commit 1つをremoteのbranchの先端としてpushする。"""
    git("push", "--quiet", remote, f"{push.sha}:refs/heads/{name}", cwd=repo)


def now_of(text: str) -> datetime:
    """--nowの値(時差つきのISO 8601)を刻にする。時差が無ければ日本時間と読む。"""
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=JST)


def main() -> None:
    """書き出しをbranchへpushし、書き出した記事とpushしたcommitか、全部の食い違いを表示するため。"""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--remote", default="origin", help="pushする先のremote(既定origin)")
    parser.add_argument(
        "--branch", default="zenn-content", help="記事を置くbranch(既定zenn-content)"
    )
    parser.add_argument(
        "--resync",
        action="store_true",
        help="Zennと連携した直後に、全記事を同期させる2回のpushをする",
    )
    parser.add_argument("--dry-run", action="store_true", help="commitを作って表示し、pushしない")
    parser.add_argument(
        "--now",
        type=now_of,
        default=None,
        help="公開の刻と比べる今(時差つきのISO 8601・既定は今の刻 — 確かめのために過去や未来を渡す)",
    )
    args = parser.parse_args()
    now = args.now or datetime.now(JST)
    dirty = git("status", "--porcelain", "--", *SOURCES)
    if dirty and not args.dry_run:
        sys.exit(
            f"書き出しの元に未commitの変更があります。commitしてから走らせてください:\n{dirty}"
        )
    export = export_zenn.prepare_all()
    branch = read_branch(args.remote, args.branch)
    problems = (
        *export.problems,
        *published_problems(export.items, branch.placed if branch else {}, now),
    )
    if problems:
        sys.exit("\n".join(problems))
    with tempfile.TemporaryDirectory() as output:
        for item in export.items:
            export_zenn.write(item, Path(output))
        tree = tree_of(Path(output))
    source = git("rev-parse", "--short=12", "HEAD") + ("(未commitの変更あり)" if dirty else "")
    pushes = plan(tree, branch, source, args.resync)
    print("\n".join(export_zenn.summary(item) for item in export.items))
    if not pushes:
        print(f"{args.branch}: 変わった記事はありません(先端 {branch.tip[:12] if branch else '-'})")
        return
    for push in pushes:
        if not args.dry_run:
            send(push, args.remote, args.branch)
        unsent = "(--dry-run: pushしていない)" if args.dry_run else ""
        print(f"{args.branch} ← {push.sha[:12]} {push.note}{unsent}")


if __name__ == "__main__":
    main()
