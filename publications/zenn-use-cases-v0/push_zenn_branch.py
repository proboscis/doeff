"""公開順の表の記事を書き出し、記事だけを置くorphan branch(ZennのGitHub連携の先)へpushする。

ZennのGitHub連携は、このリポジトリの記事だけのbranch(既定zenn-content)を読む。branchの根には
articles/とimages/だけを置く。この命令はexport_zenn.pyの書き出しを一時ディレクトリに作り、その木を
branchの先端の上の1つのcommitにしてpushする。mainの木も、この作業木も変えない。1本を足す時も、
原稿と公開順の表の行をmainに入れてから、この命令を走らせる。

- publishedを変えない: branchに在る記事は、branchのpublishedのままでなければ止まる(公開済みの記事を
  非公開に戻さず、公開予定の記事を公開しない)。branchに無い記事は、published: falseでなければ止まる。
  公開はユーザーがbranchの記事のpublishedをtrueにするcommitで行い、その後に公開順の表の公開状態を
  「公開済み <URL>」にする。
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
from pathlib import Path

import export_zenn

ROOT = export_zenn.ROOT
# 書き出しの元。未commitの変更があれば、branchのcommitが名乗る元と中身が食い違う。
SOURCES = ("publications/zenn-use-cases-v0", "images")
# branchの記事はユーザーもGitHubで直すので、front matterからpublishedの行だけを読む。
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_PUBLISHED = re.compile(r"^published:\s*(true|false)\s*$", re.M)


@dataclass(frozen=True)
class Branch:
    """pushする先のbranchの先端(commitと、記事のslugで引くpublished — 読めなければNone)。"""

    tip: str
    published: dict[str, bool | None]


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


def published_of(article: str) -> bool | None:
    """記事のfront matterのpublishedを返す。読めなければNoneを返す。"""
    front = _FRONT.match(article)
    found = _PUBLISHED.search(front.group(1)) if front else None
    return found.group(1) == "true" if found else None


def read_branch(remote: str, name: str) -> Branch | None:
    """remoteのbranchを取り寄せ、記事ごとのpublishedを読む。branchが無ければNoneを返す。"""
    if not git("ls-remote", "--heads", remote, f"refs/heads/{name}"):
        return None
    git("fetch", "--no-tags", remote, f"refs/heads/{name}")
    tip = git("rev-parse", "FETCH_HEAD")
    # --resyncの途中で止まったbranchにはarticles/が無いので、木の名指しでなくpathで引く。
    paths = git("ls-tree", "--name-only", tip, "articles/").splitlines()
    published = {
        path.removeprefix("articles/").removesuffix(".md"): published_of(
            git("show", f"{tip}:{path}")
        )
        for path in paths
        if path.endswith(".md")
    }
    return Branch(tip, published)


def change_problem(slug: str, before: bool | None) -> str:
    """branchに在る記事のpublishedを、書き出しが変える時の1行(beforeはbranchの値)。"""
    if before is None:
        return f"articles/{slug}.md: branchのpublishedが読めません"
    if before:
        return (
            f"articles/{slug}.md: branchで公開済みです。"
            "公開順の表の公開状態を「公開済み <URL>」にしてから走らせてください"
        )
    return (
        f"articles/{slug}.md: この命令は記事を公開しません。"
        "公開はbranchでpublishedをtrueにするcommitで行ってください"
    )


def published_problems(
    items: tuple[export_zenn.Prepared, ...], branch: dict[str, bool | None]
) -> tuple[str, ...]:
    """書き出しがbranchの記事のpublishedを変える所を全部返す(この命令は公開も非公開もしない)。"""
    exported = {item.slug for item in items}
    changed = tuple(
        change_problem(item.slug, branch[item.slug])
        for item in items
        if item.slug in branch and branch[item.slug] is not item.published
    )
    new = tuple(
        f"articles/{item.slug}.md: branchに無い記事は、published: falseで置きます"
        for item in items
        if item.slug not in branch and item.published
    )
    dropped = tuple(
        f"articles/{slug}.md: branchで公開済みの記事が、公開順の表にありません"
        for slug, published in branch.items()
        if published and slug not in exported
    )
    return (*changed, *new, *dropped)


def tree_of(directory: Path) -> str:
    """ディレクトリの中身を、このリポジトリのgitの木にして、その名を返す。"""
    git_dir = git("rev-parse", "--absolute-git-dir")
    with tempfile.TemporaryDirectory() as scratch:
        env = {
            **os.environ,
            "GIT_DIR": git_dir,
            "GIT_WORK_TREE": str(directory),
            "GIT_INDEX_FILE": str(Path(scratch) / "index"),
        }
        git("add", "--all", "--force", ".", env=env, cwd=directory)
        return git("write-tree", env=env, cwd=directory)


def without_articles(tree: str) -> str:
    """木からarticles/を外した木の名を返す(--resyncの1つ目のcommit)。"""
    entries = git("ls-tree", tree).splitlines()
    kept = "".join(f"{entry}\n" for entry in entries if not entry.endswith("\tarticles"))
    return git("mktree", stdin=kept)


def commit(tree: str, parent: str | None, message: str) -> str:
    """木を1つのcommitにして、その名を返す(parentが無ければorphanの根のcommit)。"""
    parents = ("-p", parent) if parent else ()
    return git("commit-tree", tree, *parents, "-m", message)


def plan(tree: str, branch: Branch | None, source: str, resync: bool) -> tuple[Push, ...]:
    """branchへ送るcommitを送る順に作る。変わった記事が無く--resyncも無ければ、空を返す。"""
    origin = f"publications/zenn-use-cases-v0/push_zenn_branch.pyが、doeff {source}から書き出した。"
    if branch is None:
        first = commit(tree, None, f"Zennの記事を置くbranchを作る\n\n{origin}")
        return (Push(first, "記事を置くbranchを作る"),)
    if not resync:
        if git("rev-parse", f"{branch.tip}^{{tree}}") == tree:
            return ()
        update = commit(tree, branch.tip, f"Zennの記事を書き出し直す\n\n{origin}")
        return (Push(update, "記事を書き出し直す"),)
    message = "Zennに全記事を同期し直すため、記事を一度外す(次のcommitで置き直す)"
    emptied = commit(without_articles(tree), branch.tip, message)
    restored = commit(tree, emptied, f"Zennに全記事を同期し直すため、記事を置き直す\n\n{origin}")
    return (Push(emptied, "記事を一度外す"), Push(restored, "記事を置き直す"))


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
    args = parser.parse_args()
    dirty = git("status", "--porcelain", "--", *SOURCES)
    if dirty and not args.dry_run:
        sys.exit(
            f"書き出しの元に未commitの変更があります。commitしてから走らせてください:\n{dirty}"
        )
    export = export_zenn.prepare_all()
    branch = read_branch(args.remote, args.branch)
    problems = (
        *export.problems,
        *published_problems(export.items, branch.published if branch else {}),
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
            git("push", "--quiet", args.remote, f"{push.sha}:refs/heads/{args.branch}")
        unsent = "(--dry-run: pushしていない)" if args.dry_run else ""
        print(f"{args.branch} ← {push.sha[:12]} {push.note}{unsent}")


if __name__ == "__main__":
    main()
